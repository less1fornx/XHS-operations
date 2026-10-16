from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, select

from packages.agent_core.style import shared_share

from ..models import (
    AuditLog,
    OutreachTask,
    PlatformAccount,
    QuotaUsage,
    RiskEvent,
)
from .base import DEFAULT_ACCOUNT_ID, LOCAL_ZONE, BusinessRuleError, utc_now, StoreBase


# User decision 2026-10-08: past the baseline the reviewer is told and decides whether to go on;
# only the ceiling refuses. A degraded account lowers the baseline, not the ceiling. The comment
# numbers follow the DM ones in proportion and are not yet confirmed by the user.
BASELINE = {"dm": 10, "comment": 6}
DEGRADED_BASELINE = {"dm": 2, "comment": 1}
CEILING = {"dm": 20, "comment": 12}
CHANNEL_ZH = {"dm": "私信", "comment": "评论"}


class QuotaMixin(StoreBase):
    """Daily quota, account state and risk events."""

    def account_summary(self) -> dict[str, Any]:
        today = datetime.now(LOCAL_ZONE).date()
        with Session(self.engine) as session:
            account = self._ensure_account(session)
            usage = self._get_usage(session, today)
            baseline = self._baseline(account)
            return {
                "account_id": account.id,
                "status": account.status,
                "freeze_reason": account.freeze_reason,
                "date": today.isoformat(),
                # "limit" is the baseline: going past it needs the reviewer's say-so. "ceiling" refuses.
                "dm": {"used": usage.dm_new_count, "limit": baseline["dm"], "ceiling": CEILING["dm"]},
                "comment": {"used": usage.comment_new_count, "limit": baseline["comment"], "ceiling": CEILING["comment"]},
            }

    def reserve_quota(
        self,
        run_id: str,
        task_id: str,
        channel: str,
        message_text: str | None = None,
        confirm_over_baseline: bool = False,
    ) -> dict[str, Any]:
        """Reserve one daily outreach slot before the graph enters scheduled.

        Past the baseline this raises `over_baseline` with the numbers the reviewer needs;
        the caller asks them and calls again with `confirm_over_baseline=True`."""
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
            todays_texts: list[str] = []
            if normalized_message:
                same_day_tasks = session.exec(
                    select(OutreachTask).where(
                        OutreachTask.platform_account_id == DEFAULT_ACCOUNT_ID,
                        OutreachTask.quota_date == today,
                        OutreachTask.quota_reserved == True,  # noqa: E712
                    )
                )
                for existing in same_day_tasks:
                    existing_text = self._normalize_message(existing.final_text or existing.draft_text)
                    if existing_text:
                        # Similarity looks at everything lined up today, also what was already answered.
                        todays_texts.append(existing_text)
                    if existing.status not in active_statuses:
                        continue
                    if existing_text and existing_text == normalized_message:
                        raise BusinessRuleError(
                            "duplicate_content",
                            "当天已有完全相同的触达文案，请基于当前内容重新编辑",
                        )
            usage = self._get_usage(session, today)
            baseline, ceiling, name = self._baseline(account)[channel], CEILING[channel], CHANNEL_ZH[channel]
            used = usage.dm_new_count if channel == "dm" else usage.comment_new_count
            if used >= ceiling:
                raise BusinessRuleError("quota_exceeded", f"今天已排 {used} 条{name}，到了上限 {ceiling} 条，不能再加")
            over = used >= baseline
            similarity = shared_share(normalized_message, todays_texts) if todays_texts else None
            if over and not confirm_over_baseline:
                parts = [f"今天已排 {used} 条{name}，这会是第 {used + 1} 条，超过了基线 {baseline} 条（上限 {ceiling} 条）。"]
                if account.status == "degraded":
                    parts.append(f"账号出过异常，处于降级状态，所以基线是 {baseline} 条。")
                if similarity is not None:
                    parts.append(f"这条文案有 {round(similarity * 100)}% 的内容和今天已排的 {len(todays_texts)} 条相同。")
                raise BusinessRuleError("over_baseline", "".join(parts))

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
                        {
                            "channel": channel,
                            "date": today.isoformat(),
                            "count_today": used + 1,
                            "over_baseline_confirmed": over,
                            "similarity_to_today": similarity,
                        },
                        ensure_ascii=False,
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
    def _baseline(account: PlatformAccount) -> dict[str, int]:
        return DEGRADED_BASELINE if account.status == "degraded" else BASELINE

    @staticmethod
    def _normalize_message(value: str | None) -> str:
        return "".join((value or "").casefold().split())

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
