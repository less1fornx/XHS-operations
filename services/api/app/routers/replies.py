"""Endpoints for what happens after sending: replies and outcomes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
# request.form() yields Starlette's UploadFile; FastAPI's is a subclass of it.
from starlette.datastructures import UploadFile
from pydantic import BaseModel, Field, model_validator

from ..storage import BusinessRuleError

from ..runtime import (  # noqa: E402
    store,
    shop_extractor,
    ALLOWED_IMAGE_TYPES,
    MAX_IMAGE_BYTES,
)
from packages.agent_core.screening import (  # noqa: E402
    MAX_IMAGES_PER_SHOP,
)
from packages.agent_core.outcomes import (  # noqa: E402
    ASK_TAG_GROUPS,
    ASK_TAGS,
    NORMAL_REPLY_DAYS,
    OUTCOME_RULES_VERSION,
    REPLY_WINDOW_DAYS,
    STAGES,
    FOLLOW_UP_STAGE,
    keeps_do_not_contact,
    reply_timing,
    scrub_contact_info,
    wants_product_info,
)
from packages.agent_core.providers import ProviderResponseError  # noqa: E402
from packages.agent_core.vision import read_reply  # noqa: E402


router = APIRouter()


class OutcomeFeedback(BaseModel):
    run_id: str = Field(min_length=1)
    reply_status: Literal["no_reply", "replied", "declined", "blocked"]
    intent_stage: Literal[
        "none", "interested", "needs_details", "sample", "quotation", "negotiation"
    ] = "none"
    conversion_status: Literal["open", "won", "lost"] = "open"
    converted_at: datetime | None = None
    order_value_note: str | None = Field(default=None, max_length=200)
    need_tags: list[str] = Field(default_factory=list, max_length=20)
    attraction_tags: list[str] = Field(default_factory=list, max_length=20)
    objection_tags: list[str] = Field(default_factory=list, max_length=20)
    human_notes: str | None = Field(default=None, max_length=2000)
    source: Literal["human_api", "manual_import"] = "human_api"
    confirmed_by: str = Field(default="local_user", min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_business_result(self) -> "OutcomeFeedback":
        if self.conversion_status == "won":
            if self.reply_status != "replied":
                raise ValueError("成交结果必须建立在对方已回复的记录上")
            if self.intent_stage == "none":
                raise ValueError("成交结果必须填写成交前的意向阶段")
            if self.converted_at is None:
                raise ValueError("成交结果必须填写 converted_at")
        if self.reply_status == "no_reply" and self.intent_stage != "none":
            raise ValueError("未回复记录不能填写正向意向阶段")
        return self


class OutcomeImport(BaseModel):
    items: list[OutcomeFeedback] = Field(min_length=1, max_length=200)


@router.get("/api/outcomes")
def list_outcomes() -> list[dict[str, Any]]:
    return store.list_outcomes()


@router.post("/api/outcomes/import")
def import_outcomes(payload: OutcomeImport) -> list[dict[str, Any]]:
    try:
        return store.import_outcomes([item.model_dump() for item in payload.items])
    except BusinessRuleError as exc:
        status = 404 if exc.code == "outreach_task_not_found" else 409
        raise HTTPException(status_code=status, detail=f"{exc.code}: {exc.message}") from exc


# --- Phase 3 step 1: what happened after sending ------------------------------


class ReplyRecord(BaseModel):
    replied: bool
    stage: str
    ask_tags: list[str] = Field(default_factory=list, max_length=15)
    reply_text: str | None = Field(default=None, max_length=4000)
    replied_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def check(self) -> "ReplyRecord":
        if self.stage not in STAGES:
            raise ValueError("未知的阶段")
        unknown = [tag for tag in self.ask_tags if tag not in ASK_TAGS]
        if unknown:
            raise ValueError(f"未知的标签: {', '.join(unknown)}")
        if self.replied and self.stage == "no_reply":
            raise ValueError("对方已回复时，阶段不能是未回复")
        if self.stage == FOLLOW_UP_STAGE:
            if not (self.note or "").strip():
                raise ValueError("选“需要我跟进”时，请写一句要跟进什么")
            self.replied = False          # not counted as a reply until a real one is recorded
        elif not self.replied and self.stage != "no_reply":
            raise ValueError("对方没有回复时，阶段只能是未回复")
        return self


@router.get("/api/outcome-options")
def outcome_options() -> dict[str, Any]:
    return {
        "rules_version": OUTCOME_RULES_VERSION,
        "normal_reply_days": NORMAL_REPLY_DAYS,
        "reply_window_days": REPLY_WINDOW_DAYS,
        "stages": STAGES,
        "ask_tag_groups": ASK_TAG_GROUPS,
    }


@router.post("/api/runs/{run_id}/reply")
def record_reply(run_id: str, payload: ReplyRecord) -> dict[str, Any]:
    replied_at = payload.replied_at
    if replied_at is not None and replied_at.tzinfo is None:
        replied_at = replied_at.astimezone()
    try:
        result = store.record_reply(
            run_id,
            replied=payload.replied,
            stage=payload.stage,
            ask_tags=payload.ask_tags,
            reply_text=scrub_contact_info(payload.reply_text) if payload.reply_text else None,
            replied_at=replied_at,
            note=scrub_contact_info(payload.note) if payload.note else None,
            follow_up=payload.stage == FOLLOW_UP_STAGE,
            keep_do_not_contact=payload.stage != FOLLOW_UP_STAGE
            and keeps_do_not_contact(payload.replied, payload.stage, payload.ask_tags),
            timing_fn=reply_timing,
        )
    except BusinessRuleError as exc:
        status = 404 if exc.code == "run_not_found" else 409
        raise HTTPException(status_code=status, detail=f"{exc.code}: {exc.message}") from exc
    result["state"]["reply"]["wants_product_info"] = wants_product_info(payload.ask_tags)
    return result


@router.post("/api/runs/{run_id}/reply/read")
async def read_reply_screenshots(run_id: str, request: Request) -> dict[str, Any]:
    """Read DM screenshots in memory. They are never written to disk."""
    if not store.get(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    form = await request.form()
    files = [item for item in form.getlist("images") if isinstance(item, UploadFile)]
    if not 1 <= len(files) <= MAX_IMAGES_PER_SHOP:
        raise HTTPException(status_code=409, detail=f"请上传 1 到 {MAX_IMAGES_PER_SHOP} 张私信截图")
    images = []
    for upload in files:
        if (upload.content_type or "") not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=409, detail=f"invalid_image: {upload.filename} 不是 JPG、PNG 或 WebP 图片")
        data = await upload.read()
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=409, detail=f"image_too_large: {upload.filename} 超过 10MB")
        images.append((upload.filename or "reply.jpg", data))
    common = {"workflow_run_id": run_id, "source": "reply", "case_id": run_id, "input_json": json.dumps({"images": len(images)})}
    try:
        result = read_reply(shop_extractor, images)
    except ProviderResponseError as exc:
        store.record_model_invocation(
            **common, status="failed", provider=shop_extractor.provider_name, model=shop_extractor.model_name,
            prompt_version="reply-extraction-v1", error_type=type(exc.__cause__).__name__ if exc.__cause__ else "ProviderResponseError",
            error_message=str(exc),
        )
        raise HTTPException(status_code=502, detail="截图没有读出来，请重试，或直接手动填写对方的回复") from exc
    extraction = result.extraction
    store.record_model_invocation(
        **common, status="succeeded", provider=result.provider, model=result.model, prompt_version=result.prompt_version,
        output_json=extraction.model_dump_json(), usage_json=json.dumps(result.usage, ensure_ascii=False), latency_ms=result.latency_ms,
    )
    if extraction.page_kind != "dm_conversation":
        raise HTTPException(status_code=409, detail=f"图片已读取，但不是私信对话：{extraction.page_summary or '未识别出内容类型'}")
    return {
        "replied": extraction.replied,
        "auto_reply_only": extraction.auto_reply_only,
        "reply_text": "\n".join(extraction.counterpart_messages),
        "suggested_tags": extraction.suggested_tags,
        "contact_info_shared": extraction.contact_info_shared,
        "reply_time_text": extraction.reply_time_text,
    }


@router.get("/api/reply-records")
def reply_records() -> list[dict[str, Any]]:
    return store.list_reply_records()
