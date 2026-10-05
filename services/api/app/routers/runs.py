"""Endpoints for one lead's run: route, draft review, edits, sending, results."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator

from ..storage import BusinessRuleError

from ..runtime import (  # noqa: E402
    store,
    check_first_message,
    graph,
    suggester,
    graph_lock,
    REVIEW_TIMEOUT_HOURS,
)
from packages.workflows.lead_workflow import policy_flags  # noqa: E402
from packages.agent_core.edits import END_REASONS, describe_edit  # noqa: E402
from packages.agent_core.outcomes import (  # noqa: E402
    scrub_contact_info,
)
from ..flows.run_flow import (  # noqa: E402
    Channel,
    RunCreate,
    config_for,
    save_result,
    request_key,
    cached_or_none,
    validate_schedule,
    start_run,
    close_unanswered,
)


router = APIRouter()


class RouteDecision(BaseModel):
    requested_channel: Channel


class ContentDecision(BaseModel):
    approved: bool
    edited_text: str | None = None
    scheduled_for: str | None = None
    edit_reasons: list[str] = Field(default_factory=list, max_length=10)
    edit_note: str | None = Field(default=None, max_length=500)


class DraftEdit(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    edit_reasons: list[str] = Field(default_factory=list, max_length=10)
    edit_note: str | None = Field(default=None, max_length=500)


class EndDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    reasons: list[str] = Field(default_factory=list, max_length=10)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def known_reasons(self) -> "EndDecision":
        unknown = [key for key in self.reasons if key not in END_REASONS]
        if unknown:
            raise ValueError(f"未知的结束原因：{unknown}")
        return self


class ResultDecision(BaseModel):
    result: Literal["sent", "failed", "read_no_reply"]
    note: str | None = None


@router.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    store.expire_stale_reviews(REVIEW_TIMEOUT_HOURS)
    close_unanswered()
    return store.list()


@router.get("/api/queue")
def list_queue() -> list[dict[str, Any]]:
    return store.list_queue()


@router.post("/api/runs")
def create_run(payload: RunCreate) -> dict[str, Any]:
    return start_run(payload)


@router.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    payload = store.get(run_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Run not found")
    return payload


@router.post("/api/runs/{run_id}/route")
def decide_route(
    run_id: str,
    payload: RouteDecision,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = request_key(run_id, "route", payload.model_dump(), idempotency_header)
    cached = cached_or_none(key)
    if cached is not None:
        return cached
    record = store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    if record["stage"] != "route_review":
        raise HTTPException(status_code=409, detail="当前工作流不在路线审核阶段")
    # A DM needs a concrete, recorded basis: either the human's note or the
    # evidence-backed points the model cited for this shop.
    if payload.requested_channel == "dm" and not (
        (record["state"].get("contact_basis_note") or "").strip()
        or record["state"].get("model_points")
    ):
        raise HTTPException(
            status_code=409,
            detail="contact_basis_note_required: 私信必须填写具体店铺、内容或历史交流依据",
        )
    try:
        with graph_lock:
            state = graph.invoke(
                Command(resume={"requested_channel": payload.requested_channel}),
                config_for(run_id),
            )
        return save_result(
            run_id,
            state,
            "route_decided",
            before_state=record["state"],
            idempotency_key=key,
        )
    except BusinessRuleError as exc:
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.message}") from exc


@router.post("/api/runs/{run_id}/content")
def decide_content(
    run_id: str,
    payload: ContentDecision,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = request_key(run_id, "content", payload.model_dump(), idempotency_header)
    cached = cached_or_none(key)
    if cached is not None:
        return cached
    record = store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    if record["stage"] != "content_review":
        raise HTTPException(status_code=409, detail="当前工作流不在内容审核阶段")
    if payload.approved and not (payload.scheduled_for or "").strip():
        raise HTTPException(status_code=409, detail="scheduled_for_required: 加入定时队列必须设置时间")
    if payload.approved:
        validate_schedule(payload.scheduled_for or "")
        if not (payload.edited_text or record["state"].get("draft") or "").strip():
            raise HTTPException(status_code=409, detail="draft_required: 文案为空，不能加入定时队列")

    reserved = False
    if payload.approved:
        try:
            store.reserve_quota(
                run_id,
                record["state"]["task_id"],
                record["state"].get("channel", ""),
                payload.edited_text or record["state"].get("draft", ""),
            )
            reserved = True
        except BusinessRuleError as exc:
            raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.message}") from exc

    try:
        with graph_lock:
            state = graph.invoke(
                Command(
                    resume={
                        "approved": payload.approved,
                        "edited_text": payload.edited_text or record["state"].get("draft", ""),
                        "scheduled_for": payload.scheduled_for or "",
                    }
                ),
                config_for(run_id),
            )
        if reserved and state.get("task_status") == "blocked":
            store.release_quota(record["state"]["task_id"], record["state"].get("channel", ""))
            reserved = False
        # Keep exactly what the human changed, and why, for later prompt work.
        original = record["state"].get("draft") or ""
        if payload.approved and payload.edited_text and payload.edited_text != original:
            state = {
                **state,
                "draft_edits": [
                    *record["state"].get("draft_edits", []),
                    describe_edit(original, payload.edited_text, payload.edit_reasons, payload.edit_note,
                                  "content_review", datetime.now().isoformat()),
                ],
            }
        return save_result(
            run_id,
            state,
            "content_reviewed",
            before_state=record["state"],
            idempotency_key=key,
        )
    except BusinessRuleError as exc:
        if reserved:
            store.release_quota(record["state"]["task_id"], record["state"].get("channel", ""))
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.message}") from exc
    except Exception:
        if reserved:
            store.release_quota(record["state"]["task_id"], record["state"].get("channel", ""))
        raise


@router.post("/api/runs/{run_id}/end")
def end_run(
    run_id: str,
    payload: EndDecision,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = request_key(run_id, "end", payload.model_dump(), idempotency_header)
    cached = cached_or_none(key)
    if cached is not None:
        return cached
    try:
        note = scrub_contact_info(payload.note.strip()) if payload.note and payload.note.strip() else None
        labels = [END_REASONS[item] for item in payload.reasons]
        summary = "；".join([*labels, *([note] if note else [])])
        return store.end_run(
            run_id,
            (f"不联系：{summary}" if summary else (payload.reason or "").strip()) or "人工结束任务",
            idempotency_key=key,
            reasons=payload.reasons,
            note=note,
        )
    except BusinessRuleError as exc:
        status = 404 if exc.code == "run_not_found" else 409
        raise HTTPException(status_code=status, detail=f"{exc.code}: {exc.message}") from exc


@router.post("/api/runs/{run_id}/draft")
def edit_draft(run_id: str, payload: DraftEdit) -> dict[str, Any]:
    record = store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    before = record["state"].get("draft") or ""
    if payload.text == before:
        return record
    flags = policy_flags({"draft": payload.text, "channel": record["state"].get("channel")})
    if flags:
        raise HTTPException(
            status_code=409,
            detail="policy_blocked: 修改后的文案包含不允许的内容（站外联系方式，或评论中的销售话术），没有保存",
        )
    try:
        return store.update_draft(
            run_id, payload.text,
            describe_edit(before, payload.text, payload.edit_reasons, payload.edit_note,
                          record["stage"], datetime.now().isoformat()),
        )
    except BusinessRuleError as exc:
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.message}") from exc


class RegenerateRequest(BaseModel):
    # What the reviewer wants changed, in their own words. Optional.
    instruction: str | None = Field(default=None, max_length=500)


@router.post("/api/runs/{run_id}/regenerate")
def regenerate_draft(run_id: str, payload: RegenerateRequest | None = None) -> dict[str, Any]:
    """Ask the model for a new draft on the same route with the current prompt.

    The human's route decision stays; only the draft and the model's notes change.
    """
    record = store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    if record["stage"] != "content_review":
        raise HTTPException(status_code=409, detail="只有待审文案的任务可以重新生成草稿")
    channel = record["state"].get("channel")
    if channel not in ("dm", "comment"):
        raise HTTPException(status_code=409, detail="这条任务没有选定私信或评论路线")
    instruction = scrub_contact_info((payload.instruction or "").strip()) if payload else ""
    previous = record["state"].get("draft") or ""
    update = suggester(
        record["state"], channel,
        {"previous_draft": previous, "request": instruction} if instruction else None,
    )
    update.pop("suggested_channel", None)
    if update.get("model_status") != "succeeded" or not (update.get("model_draft") or "").strip():
        raise HTTPException(status_code=502, detail=f"重新生成失败，原草稿没有变：{update.get('model_error') or '模型没有返回草稿'}")
    if policy_flags({"draft": update["model_draft"], "channel": channel}):
        raise HTTPException(status_code=409, detail="policy_blocked: 新草稿包含不允许的内容，没有采用，原草稿没有变")
    update["draft_issues"] = check_first_message({**record["state"], "draft": update["model_draft"]})
    try:
        return store.replace_model_draft(run_id, update, instruction=instruction or None)
    except BusinessRuleError as exc:
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.message}") from exc


@router.post("/api/runs/{run_id}/open")
def open_official_page(
    run_id: str,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = request_key(run_id, "open", {}, idempotency_header)
    cached = cached_or_none(key)
    if cached is not None:
        return cached
    try:
        return store.mark_opened(run_id, idempotency_key=key)
    except BusinessRuleError as exc:
        status = 404 if exc.code == "run_not_found" else 409
        raise HTTPException(status_code=status, detail=f"{exc.code}: {exc.message}") from exc


@router.post("/api/runs/{run_id}/result")
def record_result(
    run_id: str,
    payload: ResultDecision,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = request_key(run_id, "result", payload.model_dump(), idempotency_header)
    cached = cached_or_none(key)
    if cached is not None:
        return cached
    record = store.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    current_state = record["state"]
    if payload.result == "read_no_reply":
        if current_state.get("channel") != "dm":
            raise HTTPException(status_code=409, detail="只有私信任务可以标记已读无回复")
        if current_state.get("task_status") != "sent":
            raise HTTPException(status_code=409, detail="请先记录私信已发送，再标记已读无回复")
        if not payload.note or not payload.note.strip():
            raise HTTPException(status_code=409, detail="标记已读无回复必须填写人工观察备注")
    elif payload.result == "sent" and current_state.get("task_status") not in ("opened", "sent"):
        raise HTTPException(status_code=409, detail="请先记录已打开官方页面，再确认私信或评论已发送")
    elif current_state.get("task_status") not in ("scheduled", "opened", "sent"):
        raise HTTPException(status_code=409, detail="当前任务不允许记录发送结果")
    if payload.result == "sent" and current_state.get("task_status") in ("scheduled", "opened"):
        if store.account_summary()["status"] == "frozen":
            raise HTTPException(status_code=409, detail="account_frozen: 账号已冻结，不能确认新的发送动作")
    state = dict(current_state)
    state["task_status"] = payload.result
    state["result_note"] = payload.note or ""
    state["trace"] = [*state.get("trace", []), f"record_result_{payload.result}"]
    if payload.result == "sent":
        store.save_contact_snapshot(run_id, state)
    return save_result(
        run_id,
        state,
        f"record_result_{payload.result}",
        before_state=record["state"],
        idempotency_key=key,
    )


@router.get("/api/runs/{run_id}/events")
def list_run_events(run_id: str) -> list[dict[str, Any]]:
    if not store.get(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return store.events(run_id)
