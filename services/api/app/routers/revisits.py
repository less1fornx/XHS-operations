"""Endpoints for the revisit list: which contacted shops are due, and what the human decided."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..runtime import store  # noqa: E402
from packages.agent_core.revisit import CATEGORIES, RETRY_DAYS, apply_action, revisit_rows  # noqa: E402


router = APIRouter()


def current_rows() -> dict[str, list[dict[str, Any]]]:
    return revisit_rows(store.work_log_records(), datetime.now(timezone.utc))


@router.get("/api/revisits")
def list_revisits() -> dict[str, Any]:
    rules = [{"category": key, **{k: rule[k] for k in ("order", "label", "days", "limit", "method")}} for key, rule in CATEGORIES.items()]
    return {**current_rows(), "rules": rules, "retry_days": RETRY_DAYS}


class RevisitDecision(BaseModel):
    action: Literal["sent", "cannot_send", "skip", "stop"]


@router.post("/api/revisits/{run_id}")
def decide_revisit(run_id: str, payload: RevisitDecision) -> dict[str, Any]:
    """Record the human's decision for one shop and return the refreshed lists."""
    rows = current_rows()
    row = next((item for item in [*rows["due"], *rows["upcoming"]] if item["run_id"] == run_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="这家店不在回访列表里")
    if payload.action in ("sent", "cannot_send") and row not in rows["due"]:
        raise HTTPException(status_code=409, detail="这家店还没到回访时间")
    record = store.get(run_id) or {}
    revisit = apply_action(
        (record.get("state") or {}).get("revisit"), row["category"], payload.action,
        datetime.now(timezone.utc), row["days_since_contact"],
    )
    store.set_revisit(run_id, revisit)
    return list_revisits()
