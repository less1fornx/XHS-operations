from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformAccount(SQLModel, table=True):
    __tablename__ = "platform_accounts"

    id: str = Field(primary_key=True)
    platform: str = "xiaohongshu"
    account_type: str = "personal"
    display_name: str | None = None
    status: str = "normal"
    freeze_reason: str | None = None
    dm_daily_limit: int = 5
    comment_daily_limit: int = 3
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ShopLead(SQLModel, table=True):
    __tablename__ = "shop_leads"

    id: str = Field(default_factory=new_id, primary_key=True)
    platform_account_id: str | None = Field(default=None, index=True)
    platform: str = "xiaohongshu"
    handle: str | None = None
    display_name: str
    profile_url: str | None = None
    account_type: str = "unknown"
    activity_score: int | None = None
    business_fit_score: int | None = None
    contact_status: str = "new"
    do_not_contact: bool = False
    last_touched_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ContentEvidence(SQLModel, table=True):
    __tablename__ = "content_evidence"

    id: str = Field(default_factory=new_id, primary_key=True)
    shop_lead_id: str = Field(index=True)
    content_url: str | None = None
    content_type: str = "manual_text"
    title_or_caption: str | None = None
    observed_facts_json: str = "[]"
    source_snapshot: str | None = None
    content_hash: str | None = None
    published_at: datetime | None = None
    collected_by: str = "human"
    created_at: datetime = Field(default_factory=utc_now)


class ProductOffer(SQLModel, table=True):
    __tablename__ = "product_offers"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    category: str = "other"
    selling_points_json: str = "[]"
    minimum_order_note: str | None = None
    price_range_note: str | None = None
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkflowRun(SQLModel, table=True):
    __tablename__ = "workflow_runs"

    id: str = Field(primary_key=True)
    shop_lead_id: str | None = Field(default=None, index=True)
    run_type: str = "qualification"
    status: str = "running"
    graph_name: str = "lead_workflow"
    graph_version: str = "phase0-v1"
    thread_id: str = Field(index=True, unique=True)
    input_json: str = "{}"
    output_json: str | None = None
    error_json: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OutreachTask(SQLModel, table=True):
    __tablename__ = "outreach_tasks"

    id: str = Field(default_factory=new_id, primary_key=True)
    shop_lead_id: str = Field(index=True)
    platform_account_id: str = Field(index=True)
    evidence_id: str | None = Field(default=None, index=True)
    workflow_run_id: str | None = Field(default=None, index=True)
    channel: str
    contact_basis: str = "cold_outreach"
    status: str = "draft"
    suggested_channel: str | None = None
    draft_text: str | None = None
    final_text: str | None = None
    risk_level: str = "none"
    policy_reasons_json: str = "[]"
    scheduled_for: str | None = None
    reminder_sent_at: datetime | None = None
    opened_at: datetime | None = None
    sent_at: datetime | None = None
    result_note: str | None = None
    quota_reserved: bool = False
    quota_date: date | None = None
    idempotency_key: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class HumanReview(SQLModel, table=True):
    __tablename__ = "human_reviews"

    id: str = Field(default_factory=new_id, primary_key=True)
    task_id: str = Field(index=True)
    review_type: str
    decision: str
    selected_channel: str | None = None
    before_text: str | None = None
    after_text: str | None = None
    reason: str | None = None
    actor: str = "local_user"
    created_at: datetime = Field(default_factory=utc_now)


class OutreachOutcome(SQLModel, table=True):
    __tablename__ = "outreach_outcomes"

    id: str = Field(default_factory=new_id, primary_key=True)
    outreach_task_id: str = Field(index=True, unique=True)
    shop_lead_id: str = Field(index=True)
    reply_status: str
    intent_stage: str = "none"
    conversion_status: str = "open"
    converted_at: datetime | None = None
    order_value_note: str | None = None
    need_tags_json: str = "[]"
    attraction_tags_json: str = "[]"
    objection_tags_json: str = "[]"
    human_notes: str | None = None
    source: str = "human_api"
    confirmed_by: str = "local_user"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class QuotaUsage(SQLModel, table=True):
    __tablename__ = "quota_usage"
    __table_args__ = (
        UniqueConstraint("platform_account_id", "usage_date", name="uq_quota_account_date"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    platform_account_id: str = Field(index=True)
    usage_date: date
    dm_new_count: int = 0
    comment_new_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RiskEvent(SQLModel, table=True):
    __tablename__ = "risk_events"

    id: str = Field(default_factory=new_id, primary_key=True)
    platform_account_id: str | None = Field(default=None, index=True)
    task_id: str | None = Field(default=None, index=True)
    severity: str = "info"
    type: str
    message: str
    metadata_json: str = "{}"
    resolved_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: str = Field(default_factory=new_id, primary_key=True)
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before_json: str | None = None
    after_json: str | None = None
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ModelInvocation(SQLModel, table=True):
    __tablename__ = "model_invocations"

    id: str = Field(default_factory=new_id, primary_key=True)
    workflow_run_id: str | None = Field(default=None, index=True)
    shop_lead_id: str | None = Field(default=None, index=True)
    source: str = "production"
    case_id: str | None = None
    status: str
    provider: str
    model: str
    prompt_version: str | None = None
    prompt_sha256: str | None = None
    input_json: str = "{}"
    evidence_ids_json: str = "[]"
    output_json: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    request_id: str | None = None
    usage_json: str = "{}"
    latency_ms: int | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ScreeningBatch(SQLModel, table=True):
    __tablename__ = "screening_batches"

    id: str = Field(primary_key=True)
    status: str
    shop_count: int = 0
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RunSnapshot(SQLModel, table=True):
    __tablename__ = "run_snapshots"

    id: str = Field(primary_key=True)
    shop_name: str
    video_text: str
    stage: str
    task_status: str
    channel: str | None = None
    payload_json: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RunEvent(SQLModel, table=True):
    __tablename__ = "run_events"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    event_type: str
    payload_json: str = "{}"
    idempotency_key: str | None = Field(default=None, index=True, unique=True)
    created_at: datetime = Field(default_factory=utc_now)
