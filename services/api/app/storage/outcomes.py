from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from ..models import (
    AuditLog,
    ContactSnapshot,
    OutreachOutcome,
    OutreachTask,
    RunEvent,
    RunSnapshot,
    ShopLead,
)
from .base import BusinessRuleError, utc_now, _aware, StoreBase


class OutcomesMixin(StoreBase):
    """What happened after sending: contact-time snapshots, replies, imports."""

    def list_outcomes(self, limit: int = 200) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = (
                select(OutreachOutcome)
                .order_by(OutreachOutcome.updated_at.desc())
                .limit(limit)
            )
            return [self._public_outcome(row) for row in session.exec(statement)]

    def import_outcomes(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Upsert human-confirmed outcomes as one atomic import batch."""
        now = utc_now()
        saved: list[OutreachOutcome] = []
        with self.quota_lock, Session(self.engine) as session:
            for record in records:
                run_id = str(record["run_id"])
                task = session.exec(
                    select(OutreachTask).where(OutreachTask.workflow_run_id == run_id)
                ).first()
                if task is None:
                    raise BusinessRuleError(
                        "outreach_task_not_found",
                        f"工作流 {run_id} 没有对应的触达任务",
                    )
                if task.status not in ("sent", "read_no_reply"):
                    raise BusinessRuleError(
                        "outcome_before_send",
                        f"工作流 {run_id} 尚未确认发送，不能记录业务结果",
                    )

                outcome = session.exec(
                    select(OutreachOutcome).where(
                        OutreachOutcome.outreach_task_id == task.id
                    )
                ).first()
                before = self._public_outcome(outcome) if outcome is not None else None
                if outcome is None:
                    outcome = OutreachOutcome(
                        outreach_task_id=task.id,
                        shop_lead_id=task.shop_lead_id,
                    )
                    session.add(outcome)

                outcome.reply_status = str(record["reply_status"])
                outcome.intent_stage = str(record.get("intent_stage", "none"))
                outcome.conversion_status = str(record.get("conversion_status", "open"))
                outcome.converted_at = record.get("converted_at")
                outcome.order_value_note = record.get("order_value_note")
                outcome.need_tags_json = json.dumps(record.get("need_tags", []), ensure_ascii=False)
                outcome.attraction_tags_json = json.dumps(
                    record.get("attraction_tags", []), ensure_ascii=False
                )
                outcome.objection_tags_json = json.dumps(
                    record.get("objection_tags", []), ensure_ascii=False
                )
                outcome.human_notes = record.get("human_notes")
                outcome.source = str(record.get("source", "human_api"))
                outcome.confirmed_by = str(record.get("confirmed_by", "local_user"))
                outcome.updated_at = now
                session.flush()

                after = self._public_outcome(outcome)
                session.add(
                    AuditLog(
                        actor="local_user",
                        action="outcome_feedback_imported",
                        entity_type="outreach_outcome",
                        entity_id=outcome.id,
                        before_json=json.dumps(before, ensure_ascii=False) if before else None,
                        after_json=json.dumps(after, ensure_ascii=False),
                        trace_id=run_id,
                    )
                )
                saved.append(outcome)
            session.commit()
            return [self._public_outcome(row) for row in saved]

    def save_contact_snapshot(self, run_id: str, state: dict[str, Any]) -> None:
        """Freeze what we knew and what we sent, once, when the human confirms sending."""
        with Session(self.engine) as session:
            existing = session.exec(
                select(ContactSnapshot).where(ContactSnapshot.run_id == run_id)
            ).first()
            if existing is not None:
                return
            screening = state.get("screening_snapshot") or {}
            meta = state.get("model_meta") or {}
            session.add(
                ContactSnapshot(
                    run_id=run_id,
                    shop_lead_id=state.get("lead_id"),
                    sent_at=utc_now(),
                    channel=state.get("channel") or "dm",
                    final_text=state.get("draft") or "",
                    tier=screening.get("tier"),
                    shop_type=state.get("shop_type"),
                    features_json=json.dumps(
                        {
                            "signals": screening.get("signals", {}),
                            "content_angles": screening.get("content_angles", []),
                            "latest_post_age_days": screening.get("latest_post_age_days"),
                            "account_kind": screening.get("account_kind"),
                            "contact_basis": state.get("contact_basis"),
                            "model_route": state.get("model_route"),
                            "suggested_channel": state.get("suggested_channel"),
                            "draft_edited_by_human": bool(
                                state.get("model_draft") and state.get("model_draft") != state.get("draft")
                            ),
                            "model_draft": state.get("model_draft"),
                            "draft_edits": [
                                {key: edit.get(key) for key in ("stage", "ops", "reasons", "note")}
                                for edit in state.get("draft_edits", [])
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    versions_json=json.dumps(
                        {
                            "screening_rules": screening.get("rules_version"),
                            "prompt": meta.get("prompt_version"),
                            "model": meta.get("model"),
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            session.commit()

    def record_reply(
        self,
        run_id: str,
        *,
        replied: bool,
        stage: str,
        ask_tags: list[str],
        reply_text: str | None,
        replied_at: datetime | None,
        note: str | None,
        keep_do_not_contact: bool,
        timing_fn,
        system: bool = False,
        follow_up: bool = False,
    ) -> dict[str, Any]:
        """Record the first reply (or the lack of one) and where the lead stands now."""
        with self.quota_lock, Session(self.engine) as session:
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                raise BusinessRuleError("run_not_found", "工作流不存在")
            if snapshot.stage not in ("sent", "replied", "read_no_reply"):
                raise BusinessRuleError("invalid_transition", "只有已发送的任务可以记录回复")
            task = session.exec(
                select(OutreachTask).where(OutreachTask.workflow_run_id == run_id)
            ).first()
            if task is None:
                raise BusinessRuleError("outreach_task_not_found", "找不到对应的触达任务")
            contact = session.exec(
                select(ContactSnapshot).where(ContactSnapshot.run_id == run_id)
            ).first()
            sent_at = (contact.sent_at if contact else None) or task.sent_at or snapshot.updated_at
            now = utc_now()

            outcome = session.exec(
                select(OutreachOutcome).where(OutreachOutcome.outreach_task_id == task.id)
            ).first()
            if outcome is None:
                outcome = OutreachOutcome(
                    outreach_task_id=task.id, shop_lead_id=task.shop_lead_id, reply_status="no_reply"
                )
                session.add(outcome)
            outcome.reply_status = (
                "declined" if replied and keep_do_not_contact and stage == "replied_no_intent" else
                "replied" if replied else "no_reply"
            )
            if replied:
                # Keep the first reply's time: later updates only move the stage.
                outcome.replied_at = outcome.replied_at or replied_at or now
                outcome.reply_timing = timing_fn(_aware(sent_at), _aware(outcome.replied_at))
                if reply_text is not None:
                    outcome.reply_text = reply_text
                outcome.ask_tags_json = json.dumps(ask_tags, ensure_ascii=False)
            elif follow_up:
                # Keep what came back (an automatic reply, say) without counting it as a reply.
                outcome.reply_text = reply_text if reply_text is not None else outcome.reply_text
                outcome.ask_tags_json = json.dumps(ask_tags, ensure_ascii=False)
            outcome.stage = stage
            outcome.conversion_status = "won" if stage == "won" else "lost" if stage == "lost" else "open"
            if stage == "won":
                outcome.converted_at = outcome.converted_at or now
            outcome.human_notes = note or outcome.human_notes
            outcome.source = "system_timeout" if system else "human_api"
            outcome.updated_at = now

            lead = session.get(ShopLead, task.shop_lead_id)
            if lead is not None:
                lead.do_not_contact = keep_do_not_contact
                lead.contact_status = "contacted" if follow_up else "replied" if replied else "no_reply"
                lead.updated_at = now

            payload = json.loads(snapshot.payload_json)
            state = payload["state"]
            new_stage = "sent" if follow_up else "replied" if replied else "read_no_reply"
            state["task_status"] = new_stage
            state["reply"] = {
                "replied": replied,
                "stage": stage,
                "follow_up": follow_up,
                "follow_up_note": note if follow_up else None,
                "ask_tags": ask_tags if replied or follow_up else [],
                "reply_text": outcome.reply_text if replied or follow_up else None,
                "replied_at": outcome.replied_at.isoformat() if outcome.replied_at else None,
                "reply_timing": outcome.reply_timing if replied else None,
                "sent_at": _aware(sent_at).isoformat(),
            }
            if note:
                state["result_note"] = note
            state["trace"] = [*state.get("trace", []), "reply_recorded" if replied else "no_reply_recorded"]
            payload.update({"stage": new_stage, "interrupt": None, "state": state})
            snapshot.stage = new_stage
            snapshot.task_status = new_stage
            snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
            snapshot.updated_at = now
            task.status = new_stage
            task.updated_at = now
            event = "no_reply_timeout" if system else "follow_up_recorded" if follow_up else "reply_recorded" if replied else "no_reply_recorded"
            session.add(RunEvent(run_id=run_id, event_type=event, payload_json=json.dumps(payload, ensure_ascii=False), created_at=now))
            session.add(
                AuditLog(
                    actor="system" if system else "local_user",
                    action=event,
                    entity_type="outreach_outcome",
                    entity_id=outcome.id,
                    after_json=json.dumps(state["reply"], ensure_ascii=False),
                    trace_id=run_id,
                )
            )
            session.commit()
            return payload

    def unanswered_runs(self, days: float, now: datetime | None = None) -> list[str]:
        """Runs sent more than `days` ago with no reply recorded."""
        cutoff = (now or utc_now()) - timedelta(days=days)
        with Session(self.engine) as session:
            rows = session.exec(select(RunSnapshot).where(RunSnapshot.stage == "sent")).all()
            stale: list[str] = []
            for row in rows:
                contact = session.exec(
                    select(ContactSnapshot).where(ContactSnapshot.run_id == row.id)
                ).first()
                sent_at = _aware(contact.sent_at if contact else row.updated_at)
                if sent_at < cutoff:
                    stale.append(row.id)
            return stale

    def work_log_records(self) -> list[dict[str, Any]]:
        """Every task with when it was created and, if it was, when it was sent."""
        with Session(self.engine) as session:
            sent = {row.run_id: row.sent_at for row in session.exec(select(ContactSnapshot))}
            return [
                {
                    "run_id": row.id,
                    "stage": row.stage,
                    "created_at": _aware(row.created_at).isoformat(),
                    "sent_at": _aware(sent[row.id]).isoformat() if row.id in sent else None,
                    "state": json.loads(row.payload_json)["state"],
                }
                for row in session.exec(select(RunSnapshot))
            ]

    def list_reply_records(self) -> list[dict[str, Any]]:
        """Contact-time snapshot joined with what happened afterwards, for analysis."""
        with Session(self.engine) as session:
            records = []
            for contact in session.exec(select(ContactSnapshot).order_by(ContactSnapshot.sent_at.desc())):
                task = session.exec(
                    select(OutreachTask).where(OutreachTask.workflow_run_id == contact.run_id)
                ).first()
                outcome = (
                    session.exec(
                        select(OutreachOutcome).where(OutreachOutcome.outreach_task_id == task.id)
                    ).first()
                    if task
                    else None
                )
                records.append(
                    {
                        "run_id": contact.run_id,
                        "sent_at": _aware(contact.sent_at).isoformat(),
                        "channel": contact.channel,
                        "tier": contact.tier,
                        "shop_type": contact.shop_type,
                        "features": json.loads(contact.features_json),
                        "versions": json.loads(contact.versions_json),
                        "final_text": contact.final_text,
                        "outcome": {
                            "stage": outcome.stage,
                            "reply_status": outcome.reply_status,
                            "reply_timing": outcome.reply_timing,
                            "ask_tags": json.loads(outcome.ask_tags_json),
                        }
                        if outcome
                        else None,
                    }
                )
            return records

    @staticmethod
    def _public_outcome(outcome: OutreachOutcome) -> dict[str, Any]:
        return {
            "id": outcome.id,
            "outreach_task_id": outcome.outreach_task_id,
            "shop_lead_id": outcome.shop_lead_id,
            "reply_status": outcome.reply_status,
            "intent_stage": outcome.intent_stage,
            "conversion_status": outcome.conversion_status,
            "converted_at": outcome.converted_at.isoformat() if outcome.converted_at else None,
            "order_value_note": outcome.order_value_note,
            "need_tags": json.loads(outcome.need_tags_json),
            "attraction_tags": json.loads(outcome.attraction_tags_json),
            "objection_tags": json.loads(outcome.objection_tags_json),
            "human_notes": outcome.human_notes,
            "source": outcome.source,
            "confirmed_by": outcome.confirmed_by,
            "created_at": outcome.created_at.isoformat(),
            "updated_at": outcome.updated_at.isoformat(),
        }
