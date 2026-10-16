from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from ..models import (
    AuditLog,
    ContentEvidence,
    HumanReview,
    ModelInvocation,
    OutreachTask,
    RunEvent,
    RunSnapshot,
    ShopLead,
    WorkflowRun,
)
from .base import DEFAULT_ACCOUNT_ID, LOCAL_ZONE, REVIEW_STAGES, OPEN_STAGES, BusinessRuleError, utc_now, StoreBase


class RunsMixin(StoreBase):
    """A lead's workflow run from creation to sending: snapshots, events, drafts, ending."""

    def save(
        self,
        run_id: str,
        payload: dict[str, Any],
        event_type: str,
        *,
        before_state: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        state = payload["state"]
        now = utc_now()
        with Session(self.engine) as session:
            self._ensure_account(session)
            self._upsert_domain_records(session, run_id, payload, now)
            self._upsert_snapshot(session, run_id, payload, now)
            session.add(
                RunEvent(
                    run_id=run_id,
                    event_type=event_type,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    idempotency_key=idempotency_key,
                    created_at=now,
                )
            )
            self._append_audit(session, event_type, run_id, before_state, state)
            self._append_human_review(session, event_type, state, before_state)
            self._append_risk_events(session, state)
            session.commit()

    def get_idempotent(self, idempotency_key: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            statement = select(RunEvent).where(RunEvent.idempotency_key == idempotency_key)
            event = session.exec(statement).first()
            return json.loads(event.payload_json) if event else None

    def get(self, run_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                return None
            return json.loads(snapshot.payload_json)

    def list(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = select(RunSnapshot).order_by(RunSnapshot.updated_at.desc())
            return [json.loads(row.payload_json) for row in session.exec(statement)]

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = (
                select(RunEvent)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.id)
            )
            return [
                {
                    "id": event.id,
                    "run_id": event.run_id,
                    "event_type": event.event_type,
                    "payload": json.loads(event.payload_json),
                    "created_at": event.created_at.isoformat(),
                }
                for event in session.exec(statement)
            ]

    def list_queue(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = (
                select(RunSnapshot)
                .where(RunSnapshot.task_status.in_(("scheduled", "opened")))
                .order_by(RunSnapshot.updated_at.desc())
            )
            return [json.loads(row.payload_json) for row in session.exec(statement)]

    def record_model_invocation(self, **fields: Any) -> str:
        invocation = ModelInvocation(**fields)
        with Session(self.engine) as session:
            session.add(invocation)
            session.commit()
            return invocation.id

    def claim_due_reminders(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Claim each due reminder once; this never sends or opens a platform page."""
        current = now or utc_now()
        reminders: list[dict[str, Any]] = []
        with self.quota_lock, Session(self.engine) as session:
            tasks = session.exec(
                select(OutreachTask).where(
                    OutreachTask.status == "scheduled",
                    OutreachTask.reminder_sent_at.is_(None),
                )
            )
            for task in tasks:
                due_at = self._scheduled_datetime(task.scheduled_for)
                if due_at is None or due_at > current:
                    continue
                task.reminder_sent_at = current
                task.updated_at = current
                session.add(
                    AuditLog(
                        actor="worker",
                        action="reminder_sent",
                        entity_type="outreach_task",
                        entity_id=task.id,
                        after_json=json.dumps(
                            {"scheduled_for": task.scheduled_for, "channel": task.channel},
                            ensure_ascii=False,
                        ),
                        trace_id=task.workflow_run_id,
                    )
                )
                reminders.append(
                    {
                        "task_id": task.id,
                        "run_id": task.workflow_run_id,
                        "shop_lead_id": task.shop_lead_id,
                        "channel": task.channel,
                        "scheduled_for": task.scheduled_for,
                    }
                )
            session.commit()
        return reminders

    def mark_opened(self, run_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        with self.quota_lock, Session(self.engine) as session:
            if idempotency_key:
                cached = session.exec(
                    select(RunEvent).where(RunEvent.idempotency_key == idempotency_key)
                ).first()
                if cached is not None:
                    return json.loads(cached.payload_json)
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                raise BusinessRuleError("run_not_found", "工作流不存在")
            if snapshot.task_status == "opened":
                return json.loads(snapshot.payload_json)
            if snapshot.task_status != "scheduled":
                raise BusinessRuleError("invalid_transition", "只有定时任务可以记录已打开官方页面")
            account = self._ensure_account(session)
            if account.status == "frozen":
                raise BusinessRuleError("account_frozen", account.freeze_reason or "账号已冻结")

            payload = json.loads(snapshot.payload_json)
            state = payload["state"]
            now = utc_now()
            state["task_status"] = "opened"
            state["trace"] = [*state.get("trace", []), "official_page_opened"]
            payload["stage"] = "opened"
            payload["interrupt"] = None
            payload["state"] = state
            snapshot.stage = "opened"
            snapshot.task_status = "opened"
            snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
            snapshot.updated_at = now

            task = session.exec(
                select(OutreachTask).where(OutreachTask.workflow_run_id == run_id)
            ).first()
            if task is not None:
                task.status = "opened"
                task.opened_at = now
                task.updated_at = now
            workflow = session.get(WorkflowRun, run_id)
            if workflow is not None:
                workflow.output_json = json.dumps(payload, ensure_ascii=False)
                workflow.updated_at = now
            session.add(
                RunEvent(
                    run_id=run_id,
                    event_type="task_opened",
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    idempotency_key=idempotency_key,
                    created_at=now,
                )
            )
            session.add(
                AuditLog(
                    actor="local_user",
                    action="task_opened",
                    entity_type="outreach_task",
                    entity_id=task.id if task else run_id,
                    after_json=json.dumps({"opened_at": now.isoformat()}, ensure_ascii=False),
                    trace_id=run_id,
                )
            )
            session.commit()
            return payload

    def end_run(
        self,
        run_id: str,
        reason: str,
        *,
        actor: str = "local_user",
        event_type: str = "run_ended_by_human",
        idempotency_key: str | None = None,
        reasons: list[str] | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """End an unfinished run without contacting anyone and free its quota."""
        with Session(self.engine) as session:
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                raise BusinessRuleError("run_not_found", "工作流不存在")
            if snapshot.stage not in OPEN_STAGES:
                raise BusinessRuleError("invalid_transition", "当前任务已经结束，不能再次结束")
            payload = json.loads(snapshot.payload_json)
            state = payload["state"]
            now = utc_now()
            ended_stage = snapshot.stage
            if ended_stage == "route_review":
                state["route_review_status"] = "rejected"
                state["channel"] = "none"
            elif ended_stage == "content_review":
                state["content_review_status"] = "rejected"
            state["task_status"] = "blocked"
            state["result_note"] = reason
            # Why the human stopped here, kept for tuning screening and drafts later.
            feedback = {"stage": ended_stage, "reasons": reasons or [], "note": note, "at": now.isoformat()}
            if reasons or note:
                state["end_feedback"] = feedback
            state["trace"] = [*state.get("trace", []), event_type]
            payload.update({"stage": "blocked", "interrupt": None, "state": state})
            snapshot.stage = "blocked"
            snapshot.task_status = "blocked"
            snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
            snapshot.updated_at = now

            task = session.exec(
                select(OutreachTask).where(OutreachTask.workflow_run_id == run_id)
            ).first()
            if task is not None:
                task.status = "blocked"
                task.result_note = reason
                task.updated_at = now
                if reasons or note:
                    session.add(
                        HumanReview(
                            task_id=task.id,
                            review_type="end",
                            decision="reject",
                            before_text=state.get("draft") or None,
                            reason=json.dumps(feedback, ensure_ascii=False),
                        )
                    )
            workflow = session.get(WorkflowRun, run_id)
            if workflow is not None:
                workflow.status = "cancelled"
                workflow.output_json = json.dumps(payload, ensure_ascii=False)
                workflow.finished_at = now
                workflow.updated_at = now
            session.add(
                RunEvent(
                    run_id=run_id,
                    event_type=event_type,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    idempotency_key=idempotency_key,
                    created_at=now,
                )
            )
            session.add(
                AuditLog(
                    actor=actor,
                    action=event_type,
                    entity_type="workflow_run",
                    entity_id=run_id,
                    after_json=json.dumps(
                        {"ended_stage": ended_stage, "reason": reason, "reasons": reasons or [], "note": note},
                        ensure_ascii=False,
                    ),
                    trace_id=run_id,
                )
            )
            session.commit()
            task_id, channel = state.get("task_id"), state.get("channel")
        if task_id and channel in ("dm", "comment"):
            self.release_quota(task_id, channel)
        return payload

    def expire_stale_reviews(
        self, max_age_hours: float, now: datetime | None = None
    ) -> list[str]:
        """Auto-end runs that have waited for human review longer than the limit."""
        cutoff = (now or utc_now()) - timedelta(hours=max_age_hours)
        with Session(self.engine) as session:
            waiting = session.exec(
                select(RunSnapshot).where(RunSnapshot.stage.in_(REVIEW_STAGES))
            ).all()
            stale = [
                row.id
                for row in waiting
                if (row.updated_at.replace(tzinfo=timezone.utc) if row.updated_at.tzinfo is None else row.updated_at) < cutoff
            ]
        for run_id in stale:
            self.end_run(
                run_id,
                f"超过 {max_age_hours:g} 小时未人工处理，系统自动结束",
                actor="system",
                event_type="run_expired",
            )
        return stale

    def replace_model_draft(
        self, run_id: str, update: dict[str, Any], instruction: str | None = None
    ) -> dict[str, Any]:
        """Swap in a freshly generated draft while the run waits for content review."""
        with Session(self.engine) as session:
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                raise BusinessRuleError("run_not_found", "工作流不存在")
            if snapshot.stage != "content_review":
                raise BusinessRuleError("invalid_transition", "只有待审文案的任务可以重新生成草稿")
            payload = json.loads(snapshot.payload_json)
            state = payload["state"]
            before = state.get("draft") or ""
            state["previous_drafts"] = [*state.get("previous_drafts", []), before][-5:]
            state.update(update)
            state["draft"] = update["model_draft"]
            if instruction:
                # Kept verbatim: the reviewer's own words are the best input for future rules.
                state["draft_instructions"] = [
                    *state.get("draft_instructions", []),
                    {"at": utc_now().isoformat(), "instruction": instruction, "before": before, "after": state["draft"]},
                ]
                task = session.exec(select(OutreachTask).where(OutreachTask.workflow_run_id == run_id)).first()
                if task is not None:
                    session.add(HumanReview(task_id=task.id, review_type="instruction", decision="rewrite",
                                            before_text=before, after_text=state["draft"], reason=instruction))
            state["trace"] = [*state.get("trace", []), "draft_regenerated"]
            snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
            snapshot.updated_at = utc_now()
            session.add(RunEvent(run_id=run_id, event_type="draft_regenerated", payload_json=json.dumps(payload, ensure_ascii=False), created_at=utc_now()))
            session.commit()
            return payload

    def set_revisit(self, run_id: str, revisit: dict[str, Any]) -> dict[str, Any]:
        """Keep what the human decided when this shop came up for a revisit."""
        with Session(self.engine) as session:
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                raise BusinessRuleError("run_not_found", "工作流不存在")
            payload = json.loads(snapshot.payload_json)
            payload["state"]["revisit"] = revisit
            snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
            session.add(snapshot)
            session.commit()
            return payload

    def set_profile_url(self, run_id: str, url: str) -> dict[str, Any]:
        """Human adds the shop's profile link after the run exists, so sending opens it directly."""
        with Session(self.engine) as session:
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                raise BusinessRuleError("run_not_found", "工作流不存在")
            payload = json.loads(snapshot.payload_json)
            payload["state"]["profile_url"] = url
            snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
            snapshot.updated_at = utc_now()
            session.add(snapshot)
            session.commit()
            return payload

    def update_draft(self, run_id: str, text: str, edit: dict[str, Any]) -> dict[str, Any]:
        """Human changes an already-approved text before sending; the change is kept."""
        with Session(self.engine) as session:
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                raise BusinessRuleError("run_not_found", "工作流不存在")
            if snapshot.stage not in ("scheduled", "opened"):
                raise BusinessRuleError("invalid_transition", "只有待发送或发送中的文案可以在这里修改")
            payload = json.loads(snapshot.payload_json)
            state = payload["state"]
            now = utc_now()
            state["draft"] = text
            state["draft_edits"] = [*state.get("draft_edits", []), edit]
            state["trace"] = [*state.get("trace", []), "draft_edited"]
            snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
            snapshot.updated_at = now
            task = session.exec(
                select(OutreachTask).where(OutreachTask.workflow_run_id == run_id)
            ).first()
            if task is not None:
                task.final_text = text
                task.updated_at = now
                session.add(
                    HumanReview(
                        task_id=task.id,
                        review_type="content_edit",
                        decision="edit",
                        before_text=edit["before"],
                        after_text=text,
                        reason=json.dumps(
                            {"reasons": edit["reasons"], "note": edit["note"]}, ensure_ascii=False
                        ),
                    )
                )
            session.add(RunEvent(run_id=run_id, event_type="draft_edited", payload_json=json.dumps(payload, ensure_ascii=False), created_at=now))
            session.commit()
            return payload

    @staticmethod
    def _scheduled_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_ZONE)
        return parsed.astimezone(timezone.utc)

    def _upsert_domain_records(
        self,
        session: Session,
        run_id: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        state = payload["state"]
        lead_id = state.get("lead_id")
        evidence_id = state.get("evidence_id")
        if not lead_id or not evidence_id:
            return

        lead = session.get(ShopLead, lead_id)
        if lead is None:
            lead = ShopLead(
                id=lead_id,
                platform_account_id=DEFAULT_ACCOUNT_ID,
                display_name=state.get("shop_name", ""),
            )
            session.add(lead)
        lead.business_fit_score = state.get("fit_score")
        if state.get("xhs_id"):
            lead.handle = state.get("xhs_id")
        if state.get("profile_url"):
            lead.profile_url = state.get("profile_url")
        if state.get("task_status") == "read_no_reply":
            lead.contact_status = "do_not_contact"
            lead.do_not_contact = True
            lead.last_touched_at = now
        elif state.get("task_status") in ("sent", "failed"):
            lead.contact_status = "contacted"
            lead.last_touched_at = now
        lead.updated_at = now

        evidence = session.get(ContentEvidence, evidence_id)
        if evidence is None:
            evidence = ContentEvidence(
                id=evidence_id,
                shop_lead_id=lead_id,
                title_or_caption=state.get("shop_name"),
                observed_facts_json=json.dumps(state.get("evidence", []), ensure_ascii=False),
            )
            session.add(evidence)
        else:
            evidence.observed_facts_json = json.dumps(
                state.get("evidence", []), ensure_ascii=False
            )

        workflow = session.get(WorkflowRun, run_id)
        if workflow is None:
            workflow = WorkflowRun(
                id=run_id,
                shop_lead_id=lead_id,
                status=self._workflow_status(payload["stage"]),
                thread_id=run_id,
                input_json=json.dumps(
                    {
                        "shop_name": state.get("shop_name"),
                        "video_text": state.get("video_text"),
                        "contact_basis": state.get("contact_basis", "cold_outreach"),
                        "contact_basis_note": state.get("contact_basis_note", ""),
                    },
                    ensure_ascii=False,
                ),
            )
            session.add(workflow)
        workflow.status = self._workflow_status(payload["stage"])
        workflow.output_json = json.dumps(payload, ensure_ascii=False)
        workflow.updated_at = now

        task_id = state.get("task_id")
        channel = state.get("channel")
        if not task_id or channel not in ("dm", "comment"):
            return
        task = session.get(OutreachTask, task_id)
        if task is None:
            task = OutreachTask(
                id=task_id,
                shop_lead_id=lead_id,
                platform_account_id=DEFAULT_ACCOUNT_ID,
                evidence_id=evidence_id,
                workflow_run_id=run_id,
                channel=channel,
                contact_basis=state.get("contact_basis", "cold_outreach"),
                idempotency_key=f"{run_id}:{channel}",
            )
            session.add(task)
        task.status = self._task_status(payload["stage"], state)
        task.contact_basis = state.get("contact_basis", task.contact_basis)
        task.suggested_channel = state.get("suggested_channel")
        task.draft_text = state.get("draft")
        if task.status == "scheduled":
            task.final_text = state.get("draft")
        elif task.status == "opened":
            task.opened_at = task.opened_at or now
        elif task.status == "sent":
            task.sent_at = task.sent_at or now
        task.risk_level = "high" if state.get("risk_flags") else "none"
        task.policy_reasons_json = json.dumps(state.get("risk_flags", []), ensure_ascii=False)
        task.scheduled_for = state.get("scheduled_for")
        task.result_note = state.get("result_note")
        task.updated_at = now

    def _upsert_snapshot(
        self, session: Session, run_id: str, payload: dict[str, Any], now: datetime
    ) -> None:
        state = payload["state"]
        snapshot = session.get(RunSnapshot, run_id)
        if snapshot is None:
            session.add(
                RunSnapshot(
                    id=run_id,
                    shop_name=state.get("shop_name", ""),
                    video_text=state.get("video_text", ""),
                    stage=payload["stage"],
                    task_status=state.get("task_status", "new"),
                    channel=state.get("channel"),
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        snapshot.shop_name = state.get("shop_name", snapshot.shop_name)
        snapshot.video_text = state.get("video_text", snapshot.video_text)
        snapshot.stage = payload["stage"]
        snapshot.task_status = state.get("task_status", snapshot.task_status)
        snapshot.channel = state.get("channel")
        snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
        snapshot.updated_at = now

    def _append_audit(
        self,
        session: Session,
        event_type: str,
        run_id: str,
        before_state: dict[str, Any] | None,
        state: dict[str, Any],
    ) -> None:
        session.add(
            AuditLog(
                actor="local_user" if "review" in event_type or "result" in event_type else "system",
                action=event_type,
                entity_type="workflow_run",
                entity_id=run_id,
                before_json=json.dumps(before_state, ensure_ascii=False) if before_state else None,
                after_json=json.dumps(state, ensure_ascii=False),
                trace_id=run_id,
            )
        )

    def _append_human_review(
        self,
        session: Session,
        event_type: str,
        state: dict[str, Any],
        before_state: dict[str, Any] | None,
    ) -> None:
        task_id = state.get("task_id")
        if not task_id:
            return
        if event_type == "route_decided":
            session.add(
                HumanReview(
                    task_id=task_id,
                    review_type="route",
                    decision="reject" if state.get("channel") == "none" else "approve",
                    selected_channel=state.get("channel"),
                    reason="human route decision",
                )
            )
        elif event_type == "content_reviewed":
            session.add(
                HumanReview(
                    task_id=task_id,
                    review_type="content",
                    decision=(
                        "approve"
                        if state.get("content_review_status") == "approved"
                        else "reject"
                    ),
                    before_text=(before_state or {}).get("draft"),
                    after_text=state.get("draft"),
                )
            )
        elif event_type.startswith("record_result_"):
            session.add(
                HumanReview(
                    task_id=task_id,
                    review_type="platform_result",
                    decision="confirm",
                    reason=state.get("result_note"),
                )
            )

    @staticmethod
    def _workflow_status(stage: str) -> str:
        return "waiting_human" if stage in ("route_review", "content_review") else "completed"

    @staticmethod
    def _task_status(stage: str, state: dict[str, Any]) -> str:
        if state.get("task_status") == "blocked":
            return "blocked"
        if stage == "content_review":
            return "content_review"
        if stage == "scheduled":
            return "scheduled"
        if stage in ("opened", "sent", "failed", "read_no_reply"):
            return stage
        return "route_review"
