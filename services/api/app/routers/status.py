"""Endpoints for health, progress numbers, feedback notes, account state and option lists."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator


from ..runtime import (  # noqa: E402
    RUN_DB_PATH,
    store,
    llm_provider,
    REVIEW_TIMEOUT_HOURS,
)
from packages.agent_core.edits import EDIT_REASONS, END_REASON_GROUPS, END_REASONS  # noqa: E402
from packages.agent_core.outcomes import (  # noqa: E402
    scrub_contact_info,
)
from services.worker.work_log import COLUMNS, STEP, build_rows  # noqa: E402
from services.worker.status_report import FEEDBACK_AREAS, collect as collect_status, stage_rows  # noqa: E402


router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "api",
        "llm_provider": llm_provider.provider_name,
        "llm_model": llm_provider.model_name,
        "review_timeout_hours": str(REVIEW_TIMEOUT_HOURS),
    }


@router.get("/api/account/summary")
def account_summary() -> dict[str, Any]:
    return store.account_summary()


@router.get("/api/risk-events")
def list_risk_events() -> list[dict[str, Any]]:
    return store.list_risk_events()


@router.get("/api/edit-options")
def edit_options() -> dict[str, Any]:
    return {"reasons": EDIT_REASONS, "end_reason_groups": END_REASON_GROUPS}


class FeedbackCreate(BaseModel):
    area: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def known_area(self) -> "FeedbackCreate":
        if self.area not in FEEDBACK_AREAS:
            raise ValueError("未知的意见类别")
        return self


@router.get("/api/status")
def project_status() -> dict[str, Any]:
    """Progress numbers per stage, straight from the database (same source as the status_report command)."""
    return {
        "stages": stage_rows(stats := collect_status(RUN_DB_PATH)),
        "end_reasons": [{"label": END_REASONS.get(k, k), "count": v} for k, v in sorted(stats["end_reasons"].items(), key=lambda kv: -kv[1])],
        "edit_reasons": [{"label": EDIT_REASONS.get(k, k), "count": v} for k, v in sorted(stats["edit_reasons"].items(), key=lambda kv: -kv[1])],
        "feedback_areas": FEEDBACK_AREAS,
        "notes": store.list_feedback_notes(),
    }


@router.get("/api/work-log")
def work_log() -> dict[str, Any]:
    return {"columns": COLUMNS, "steps": STEP, "rows": build_rows(store.work_log_records())}


@router.post("/api/feedback")
def add_feedback(payload: FeedbackCreate) -> dict[str, Any]:
    text = scrub_contact_info(payload.text.strip())
    if not text:
        raise HTTPException(status_code=422, detail="意见内容不能为空")
    return store.add_feedback_note(payload.area, text)
