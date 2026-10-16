"""Shared logic for one lead's run: creating it, saving results, stages, idempotency."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field


from ..runtime import (  # noqa: E402
    store,
    graph,
    graph_lock,
)
from packages.agent_core.screening import (  # noqa: E402
    normalize_xhs_id,
)
from packages.agent_core.outcomes import (  # noqa: E402
    REPLY_WINDOW_DAYS,
    reply_timing,
)


Channel = Literal["dm", "comment", "none"]
ContactBasis = Literal["cold_outreach", "prior_conversation", "inbound_inquiry", "explicit_permission"]
ShopType = Literal[
    "suit_custom",
    "menswear",
    "apparel_studio",
    "content_creator",
    "staff_account",
    "hobbyist",
    "supplier",
    "other",
]
RunRecord = dict[str, Any]


class RunCreate(BaseModel):
    shop_name: str = Field(min_length=1)
    video_text: str = Field(min_length=1)
    profile_url: str | None = None
    xhs_id: str | None = Field(default=None, max_length=100)
    contact_basis: ContactBasis = "cold_outreach"
    contact_basis_note: str | None = None
    shop_type: ShopType = "suit_custom"
    audience: Literal["store_account", "personal_shop", "unknown"] = "unknown"
    # Pasted profile text can be long; the model input builder trims it.
    shop_note: str | None = Field(default=None, max_length=20000)
    activity_note: str | None = Field(default=None, max_length=20000)
    purchase_signal_note: str | None = Field(default=None, max_length=20000)
    # Set by batch screening so the contact snapshot knows the tier and signals.
    screening_snapshot: dict[str, Any] | None = None


def config_for(run_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": run_id}}


def interrupt_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = state.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


def stage_for(state: dict[str, Any]) -> str:
    payload = interrupt_payload(state)
    if payload:
        return str(payload.get("type", "human_review"))
    return str(state.get("task_status", "new"))


def public_run(run_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run_id,
        "stage": stage_for(state),
        "interrupt": interrupt_payload(state),
        "state": {key: value for key, value in state.items() if key != "__interrupt__"},
    }


def save_result(
    run_id: str,
    state: dict[str, Any],
    event_type: str,
    *,
    before_state: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    # The profile link can be added after the run started; the workflow's own state never saw it.
    if not state.get("profile_url"):
        kept = ((store.get(run_id) or {}).get("state") or {}).get("profile_url")
        if kept:
            state = {**state, "profile_url": kept}
    payload = public_run(run_id, state)
    store.save(
        run_id,
        payload,
        event_type,
        before_state=before_state,
        idempotency_key=idempotency_key,
    )
    return payload


def request_key(run_id: str, operation: str, body: Any, supplied: str | None) -> str:
    if supplied:
        return supplied
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{run_id}:{operation}:{canonical}".encode()).hexdigest()
    return f"{operation}:{run_id}:{digest}"


def cached_or_none(key: str) -> dict[str, Any] | None:
    return store.get_idempotent(key)


def close_unanswered() -> None:
    """User rule: 15 days without a reply ends the task as no-reply."""
    for run_id in store.unanswered_runs(REPLY_WINDOW_DAYS):
        store.record_reply(
            run_id, replied=False, stage="no_reply", ask_tags=[], reply_text=None, replied_at=None,
            note=f"发送后 {REPLY_WINDOW_DAYS} 天未回复，系统自动结束", keep_do_not_contact=True,
            timing_fn=reply_timing, system=True,
        )


def validate_schedule(value: str) -> None:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="invalid_schedule: 定时提醒时间格式无效") from exc


def start_run(payload: RunCreate) -> dict[str, Any]:
    xhs_id = normalize_xhs_id(payload.xhs_id)
    if store.is_do_not_contact(payload.shop_name, payload.profile_url, xhs_id):
        raise HTTPException(
            status_code=409,
            detail="该店铺已被标记为不再触达，不会进入后续评选",
        )
    run_id = str(uuid4())
    initial_state = {
        "lead_id": store.find_lead_id(payload.shop_name, payload.profile_url, xhs_id) or str(uuid4()),
        "xhs_id": xhs_id,
        "contact_history": store.contact_history(payload.shop_name, payload.profile_url, xhs_id),
        "evidence_id": str(uuid4()),
        "task_id": str(uuid4()),
        "workflow_run_id": run_id,
        "shop_name": payload.shop_name,
        "profile_url": payload.profile_url or "",
        "contact_basis": payload.contact_basis,
        "contact_basis_note": payload.contact_basis_note or "",
        "video_text": payload.video_text,
        "shop_type": payload.shop_type,
        "audience": payload.audience,
        "shop_note": payload.shop_note or "",
        "activity_note": payload.activity_note or "",
        "purchase_signal_note": payload.purchase_signal_note or "",
        "screening_snapshot": payload.screening_snapshot or {},
        "trace": [],
        "route_review_status": "not_started",
        "content_review_status": "not_started",
        "task_status": "new",
    }
    with graph_lock:
        state = graph.invoke(initial_state, config_for(run_id))
    return save_result(run_id, state, "run_created")
