from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from sqlmodel import Session, create_engine, select

from .models import (
    FeedbackNote,
    AuditLog,
    ContactSnapshot,
    ContentEvidence,
    HumanReview,
    ModelInvocation,
    OutreachOutcome,
    OutreachTask,
    PlatformAccount,
    RiskEvent,
    QuotaUsage,
    RunEvent,
    RunSnapshot,
    ScreeningBatch,
    ShopLead,
    WorkflowRun,
)


DEFAULT_ACCOUNT_ID = "local-xhs-personal"
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


REVIEW_STAGES = ("route_review", "content_review")
OPEN_STAGES = (*REVIEW_STAGES, "scheduled", "opened")


class BusinessRuleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they were stored in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class RunStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        self.quota_lock = Lock()

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

    def account_summary(self) -> dict[str, Any]:
        today = datetime.now(LOCAL_ZONE).date()
        with Session(self.engine) as session:
            account = self._ensure_account(session)
            usage = self._get_usage(session, today)
            dm_limit, comment_limit = self._effective_limits(account)
            return {
                "account_id": account.id,
                "status": account.status,
                "freeze_reason": account.freeze_reason,
                "date": today.isoformat(),
                "dm": {"used": usage.dm_new_count, "limit": dm_limit},
                "comment": {"used": usage.comment_new_count, "limit": comment_limit},
            }

    @staticmethod
    def _same_shop(lead: ShopLead, name: str, url: str, xhs_id: str) -> bool:
        """The 小红书号 decides when both sides have one; name and URL are fallbacks."""
        lead_id = (lead.handle or "").strip().casefold()
        if xhs_id and lead_id:
            return xhs_id == lead_id
        if lead.display_name.strip().casefold() == name:
            return True
        return bool(url) and (lead.profile_url or "").strip().rstrip("/").casefold() == url

    def _find_lead(
        self, session: Session, display_name: str, profile_url: str | None, xhs_id: str | None
    ) -> ShopLead | None:
        name = display_name.strip().casefold()
        url = (profile_url or "").strip().rstrip("/").casefold()
        key = (xhs_id or "").strip().casefold()
        leads = session.exec(
            select(ShopLead).where(ShopLead.platform_account_id == DEFAULT_ACCOUNT_ID)
        ).all()
        # Prefer an exact id match over a name match on an older record.
        if key:
            for lead in leads:
                if (lead.handle or "").strip().casefold() == key:
                    return lead
        for lead in leads:
            if self._same_shop(lead, name, url, key):
                return lead
        return None

    def is_do_not_contact(
        self, display_name: str, profile_url: str | None = None, xhs_id: str | None = None
    ) -> bool:
        with Session(self.engine) as session:
            lead = self._find_lead(session, display_name, profile_url, xhs_id)
            return bool(lead and lead.do_not_contact)

    def find_lead_id(
        self, display_name: str, profile_url: str | None = None, xhs_id: str | None = None
    ) -> str | None:
        with Session(self.engine) as session:
            lead = self._find_lead(session, display_name, profile_url, xhs_id)
            return lead.id if lead else None

    def contact_history(
        self, display_name: str, profile_url: str | None = None, xhs_id: str | None = None
    ) -> dict[str, Any] | None:
        """Whether this account was contacted before, looked up by 小红书号 first."""
        with Session(self.engine) as session:
            lead = self._find_lead(session, display_name, profile_url, xhs_id)
            if lead is None or lead.last_touched_at is None:
                return None
            return {
                "status": lead.contact_status,
                "do_not_contact": lead.do_not_contact,
                "last_touched_at": _aware(lead.last_touched_at).isoformat(),
            }

    def reserve_quota(
        self,
        run_id: str,
        task_id: str,
        channel: str,
        message_text: str | None = None,
    ) -> dict[str, Any]:
        """Reserve one daily outreach slot before the graph enters scheduled."""
        if channel not in ("dm", "comment"):
            raise BusinessRuleError("invalid_channel", "只有 DM 或评论任务可以占用配额")

        with self.quota_lock, Session(self.engine) as session:
            account = self._ensure_account(session)
            task = session.get(OutreachTask, task_id)
            if task is None or task.workflow_run_id != run_id:
                raise BusinessRuleError("task_not_found", "触达任务不存在或不属于当前工作流")
            if task.channel != channel:
                raise BusinessRuleError("channel_mismatch", "任务触达方式不一致")
            if task.status != "content_review":
                if task.quota_reserved and task.status == "scheduled":
                    return self.account_summary()
                raise BusinessRuleError("invalid_transition", "当前任务不在内容审核状态")
            if task.quota_reserved:
                return self.account_summary()
            if account.status == "frozen":
                raise BusinessRuleError(
                    "account_frozen", account.freeze_reason or "账号已冻结，不能创建新触达"
                )

            active_statuses = ("content_review", "approved", "scheduled", "opened", "sent", "failed")
            duplicate = session.exec(
                select(OutreachTask).where(
                    OutreachTask.shop_lead_id == task.shop_lead_id,
                    OutreachTask.id != task.id,
                    OutreachTask.status.in_(active_statuses),
                )
            ).first()
            if duplicate is not None:
                raise BusinessRuleError("same_target_today", "同一家店铺已有当天触达任务")

            today = datetime.now(LOCAL_ZONE).date()
            normalized_message = self._normalize_message(message_text)
            if normalized_message:
                same_day_tasks = session.exec(
                    select(OutreachTask).where(
                        OutreachTask.platform_account_id == DEFAULT_ACCOUNT_ID,
                        OutreachTask.quota_date == today,
                        OutreachTask.quota_reserved == True,  # noqa: E712
                        OutreachTask.status.in_(active_statuses),
                    )
                )
                for existing in same_day_tasks:
                    existing_text = self._normalize_message(existing.final_text or existing.draft_text)
                    if existing_text and existing_text == normalized_message:
                        raise BusinessRuleError(
                            "duplicate_content",
                            "当天已有完全相同的触达文案，请基于当前内容重新编辑",
                        )
            usage = self._get_usage(session, today)
            dm_limit, comment_limit = self._effective_limits(account)
            limit = dm_limit if channel == "dm" else comment_limit
            used = usage.dm_new_count if channel == "dm" else usage.comment_new_count
            if used >= limit:
                raise BusinessRuleError(
                    "quota_exceeded",
                    f"今日{('私信' if channel == 'dm' else '评论')}配额已用完",
                )

            if channel == "dm":
                usage.dm_new_count += 1
            else:
                usage.comment_new_count += 1
            usage.updated_at = utc_now()
            task.quota_reserved = True
            task.quota_date = today
            task.updated_at = utc_now()
            session.add(
                AuditLog(
                    actor="system",
                    action="quota_reserved",
                    entity_type="outreach_task",
                    entity_id=task.id,
                    after_json=json.dumps(
                        {"channel": channel, "date": today.isoformat()}, ensure_ascii=False
                    ),
                    trace_id=run_id,
                )
            )
            session.commit()
            return self.account_summary()

    def release_quota(self, task_id: str, channel: str) -> None:
        with self.quota_lock, Session(self.engine) as session:
            task = session.get(OutreachTask, task_id)
            if task is None or not task.quota_reserved or task.quota_date is None:
                return
            usage = self._get_usage(session, task.quota_date)
            if channel == "dm":
                usage.dm_new_count = max(0, usage.dm_new_count - 1)
            else:
                usage.comment_new_count = max(0, usage.comment_new_count - 1)
            task.quota_reserved = False
            task.quota_date = None
            task.updated_at = utc_now()
            session.commit()

    def list_queue(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = (
                select(RunSnapshot)
                .where(RunSnapshot.task_status.in_(("scheduled", "opened")))
                .order_by(RunSnapshot.updated_at.desc())
            )
            return [json.loads(row.payload_json) for row in session.exec(statement)]

    def list_risk_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit)
            return [
                {
                    "id": event.id,
                    "task_id": event.task_id,
                    "severity": event.severity,
                    "type": event.type,
                    "message": event.message,
                    "metadata": json.loads(event.metadata_json),
                    "created_at": event.created_at.isoformat(),
                }
                for event in session.exec(statement)
            ]

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

    def replace_model_draft(self, run_id: str, update: dict[str, Any]) -> dict[str, Any]:
        """Swap in a freshly generated draft while the run waits for content review."""
        with Session(self.engine) as session:
            snapshot = session.get(RunSnapshot, run_id)
            if snapshot is None:
                raise BusinessRuleError("run_not_found", "工作流不存在")
            if snapshot.stage != "content_review":
                raise BusinessRuleError("invalid_transition", "只有待审文案的任务可以重新生成草稿")
            payload = json.loads(snapshot.payload_json)
            state = payload["state"]
            state["previous_drafts"] = [*state.get("previous_drafts", []), state.get("draft") or ""][-5:]
            state.update(update)
            state["draft"] = update["model_draft"]
            state["trace"] = [*state.get("trace", []), "draft_regenerated"]
            snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
            snapshot.updated_at = utc_now()
            session.add(RunEvent(run_id=run_id, event_type="draft_regenerated", payload_json=json.dumps(payload, ensure_ascii=False), created_at=utc_now()))
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
                lead.contact_status = "replied" if replied else "no_reply"
                lead.updated_at = now

            payload = json.loads(snapshot.payload_json)
            state = payload["state"]
            new_stage = "replied" if replied else "read_no_reply"
            state["task_status"] = new_stage
            state["reply"] = {
                "replied": replied,
                "stage": stage,
                "ask_tags": ask_tags if replied else [],
                "reply_text": outcome.reply_text if replied else None,
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
            event = "no_reply_timeout" if system else "reply_recorded" if replied else "no_reply_recorded"
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

    def add_feedback_note(self, area: str, text: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            note = FeedbackNote(area=area, text=text)
            session.add(note)
            session.commit()
            session.refresh(note)
            return self._feedback_dict(note)

    def list_feedback_notes(self, limit: int = 50) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = select(FeedbackNote).order_by(FeedbackNote.created_at.desc()).limit(limit)
            return [self._feedback_dict(note) for note in session.exec(statement)]

    @staticmethod
    def _feedback_dict(note: FeedbackNote) -> dict[str, Any]:
        return {
            "id": note.id,
            "area": note.area,
            "text": note.text,
            "status": note.status,
            "created_at": note.created_at.replace(tzinfo=timezone.utc).isoformat(),
        }

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

    def save_batch(self, batch_id: str, status: str, payload: dict[str, Any]) -> None:
        now = utc_now()
        with Session(self.engine) as session:
            batch = session.get(ScreeningBatch, batch_id)
            if batch is None:
                batch = ScreeningBatch(id=batch_id, status=status, created_at=now)
                session.add(batch)
            batch.status = status
            batch.shop_count = len(payload.get("results", []))
            batch.payload_json = json.dumps(payload, ensure_ascii=False)
            batch.updated_at = now
            session.commit()

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            batch = session.get(ScreeningBatch, batch_id)
            return json.loads(batch.payload_json) if batch else None

    def list_batches(self, limit: int = 30) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = (
                select(ScreeningBatch).order_by(ScreeningBatch.created_at.desc()).limit(limit)
            )
            return [
                {
                    "id": row.id,
                    "status": row.status,
                    "shop_count": row.shop_count,
                    # SQLite drops the timezone; stored values are UTC.
                    "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
                    "shop_names": [
                        name
                        for result in json.loads(row.payload_json).get("results", [])
                        if (name := (result.get("extraction") or {}).get("shop_name"))
                    ],
                }
                for row in session.exec(statement)
            ]

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

    def _ensure_account(self, session: Session) -> PlatformAccount:
        account = session.get(PlatformAccount, DEFAULT_ACCOUNT_ID)
        if account is None:
            account = PlatformAccount(id=DEFAULT_ACCOUNT_ID)
            session.add(account)
            session.flush()
        return account

    @staticmethod
    def _get_usage(session: Session, usage_date: date) -> QuotaUsage:
        statement = select(QuotaUsage).where(
            QuotaUsage.platform_account_id == DEFAULT_ACCOUNT_ID,
            QuotaUsage.usage_date == usage_date,
        )
        usage = session.exec(statement).first()
        if usage is None:
            usage = QuotaUsage(
                platform_account_id=DEFAULT_ACCOUNT_ID,
                usage_date=usage_date,
            )
            session.add(usage)
            session.flush()
        return usage

    @staticmethod
    def _effective_limits(account: PlatformAccount) -> tuple[int, int]:
        if account.status == "degraded":
            return min(account.dm_daily_limit, 2), min(account.comment_daily_limit, 1)
        return account.dm_daily_limit, account.comment_daily_limit

    @staticmethod
    def _normalize_message(value: str | None) -> str:
        return "".join((value or "").casefold().split())

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

    def _append_risk_events(self, session: Session, state: dict[str, Any]) -> None:
        for flag in state.get("risk_flags", []):
            session.add(
                RiskEvent(
                    task_id=state.get("task_id"),
                    severity="critical",
                    type="policy_hit",
                    message=f"Policy blocked content: {flag}",
                    metadata_json=json.dumps({"flag": flag}, ensure_ascii=False),
                )
            )
        if state.get("task_status") == "failed":
            account = self._ensure_account(session)
            note = state.get("result_note") or "Human recorded a send failure"
            lowered = note.lower()
            restricted_terms = (
                "验证码", "禁言", "限制", "封禁", "警告", "风控", "功能受限", "私信受限",
                "评论受限", "账号异常", "申诉", "举报", "违规通知", "降权", "限流",
            )
            restricted = any(word in note for word in restricted_terms) or "captcha" in lowered or "restricted" in lowered
            account.status = "frozen" if restricted else "degraded"
            account.freeze_reason = note if restricted else account.freeze_reason
            account.updated_at = utc_now()
            session.add(
                RiskEvent(
                    platform_account_id=account.id,
                    task_id=state.get("task_id"),
                    severity="warning",
                    type="send_failure",
                    message=note,
                )
            )
            if restricted:
                session.add(
                    RiskEvent(
                        platform_account_id=account.id,
                        task_id=state.get("task_id"),
                        severity="critical",
                        type="account_frozen",
                        message="检测到平台限制类提示，已冻结新触达队列",
                    )
                )
        if state.get("task_status") == "read_no_reply":
            session.add(
                RiskEvent(
                    platform_account_id=DEFAULT_ACCOUNT_ID,
                    task_id=state.get("task_id"),
                    severity="info",
                    type="no_response",
                    message="已读无回复，已将该店铺加入勿扰名单，不再参与后续评选",
                    metadata_json=json.dumps(
                        {"reason": "read_no_reply", "do_not_contact": True},
                        ensure_ascii=False,
                    ),
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
