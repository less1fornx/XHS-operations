from __future__ import annotations

import sys
import os
import sqlite3
import hashlib
import json
from datetime import datetime
from threading import Lock
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator

from .storage import BusinessRuleError, RunStore


# The Phase 0 graph remains the single workflow source until the domain package
# is extracted in the next Phase 1 step.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LAB_SOURCE = PROJECT_ROOT / "labs" / "phase0-langgraph" / "src"
if str(LAB_SOURCE) not in sys.path:
    sys.path.insert(0, str(LAB_SOURCE))

from phase0_graph.lead_workflow import build_graph  # noqa: E402
from packages.agent_core.providers import provider_from_env  # noqa: E402
from services.worker.lead_generation import build_suggester, load_product_facts  # noqa: E402


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

app = FastAPI(title="Agent Operations API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    # Vite picks the next free port when 5173 is taken, so allow any local port.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RUN_DB_PATH = Path(os.getenv("AGENT_RUN_DB_PATH", str(DATA_DIR / "agent_ops.db")))
CHECKPOINT_DB_PATH = Path(
    os.getenv("AGENT_CHECKPOINT_DB_PATH", str(DATA_DIR / "agent_checkpoints.sqlite"))
)
checkpoint_connection = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)
checkpointer = SqliteSaver(checkpoint_connection)
checkpointer.setup()
store = RunStore(RUN_DB_PATH)
llm_provider = provider_from_env()
graph = build_graph(
    checkpointer=checkpointer,
    suggester=build_suggester(store, llm_provider, load_product_facts()),
)
graph_lock = Lock()
REVIEW_TIMEOUT_HOURS = float(os.getenv("AGENT_REVIEW_TIMEOUT_HOURS", "48"))


class RunCreate(BaseModel):
    shop_name: str = Field(min_length=1)
    video_text: str = Field(min_length=1)
    profile_url: str | None = None
    contact_basis: ContactBasis = "cold_outreach"
    contact_basis_note: str | None = None
    shop_type: ShopType = "suit_custom"
    # Pasted profile text can be long; the model input builder trims it.
    shop_note: str | None = Field(default=None, max_length=20000)
    activity_note: str | None = Field(default=None, max_length=20000)
    purchase_signal_note: str | None = Field(default=None, max_length=20000)


class RouteDecision(BaseModel):
    requested_channel: Channel


class ContentDecision(BaseModel):
    approved: bool
    edited_text: str | None = None
    scheduled_for: str | None = None


class EndDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ResultDecision(BaseModel):
    result: Literal["sent", "failed", "read_no_reply"]
    note: str | None = None


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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "api",
        "llm_provider": llm_provider.provider_name,
        "llm_model": llm_provider.model_name,
        "review_timeout_hours": str(REVIEW_TIMEOUT_HOURS),
    }


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    store.expire_stale_reviews(REVIEW_TIMEOUT_HOURS)
    return store.list()


@app.get("/api/queue")
def list_queue() -> list[dict[str, Any]]:
    return store.list_queue()


@app.get("/api/account/summary")
def account_summary() -> dict[str, Any]:
    return store.account_summary()


@app.get("/api/risk-events")
def list_risk_events() -> list[dict[str, Any]]:
    return store.list_risk_events()


@app.get("/api/outcomes")
def list_outcomes() -> list[dict[str, Any]]:
    return store.list_outcomes()


@app.post("/api/outcomes/import")
def import_outcomes(payload: OutcomeImport) -> list[dict[str, Any]]:
    try:
        return store.import_outcomes([item.model_dump() for item in payload.items])
    except BusinessRuleError as exc:
        status = 404 if exc.code == "outreach_task_not_found" else 409
        raise HTTPException(status_code=status, detail=f"{exc.code}: {exc.message}") from exc


def validate_schedule(value: str) -> None:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="invalid_schedule: 定时提醒时间格式无效") from exc


@app.post("/api/runs")
def create_run(payload: RunCreate) -> dict[str, Any]:
    if store.is_do_not_contact(payload.shop_name, payload.profile_url):
        raise HTTPException(
            status_code=409,
            detail="该店铺已被标记为不再触达，不会进入后续评选",
        )
    run_id = str(uuid4())
    initial_state = {
        "lead_id": store.find_lead_id(payload.shop_name, payload.profile_url) or str(uuid4()),
        "evidence_id": str(uuid4()),
        "task_id": str(uuid4()),
        "workflow_run_id": run_id,
        "shop_name": payload.shop_name,
        "profile_url": payload.profile_url or "",
        "contact_basis": payload.contact_basis,
        "contact_basis_note": payload.contact_basis_note or "",
        "video_text": payload.video_text,
        "shop_type": payload.shop_type,
        "shop_note": payload.shop_note or "",
        "activity_note": payload.activity_note or "",
        "purchase_signal_note": payload.purchase_signal_note or "",
        "trace": [],
        "route_review_status": "not_started",
        "content_review_status": "not_started",
        "task_status": "new",
    }
    with graph_lock:
        state = graph.invoke(initial_state, config_for(run_id))
    return save_result(run_id, state, "run_created")


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    payload = store.get(run_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Run not found")
    return payload


@app.post("/api/runs/{run_id}/route")
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


@app.post("/api/runs/{run_id}/content")
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


@app.post("/api/runs/{run_id}/end")
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
        return store.end_run(
            run_id, (payload.reason or "").strip() or "人工结束任务", idempotency_key=key
        )
    except BusinessRuleError as exc:
        status = 404 if exc.code == "run_not_found" else 409
        raise HTTPException(status_code=status, detail=f"{exc.code}: {exc.message}") from exc


@app.post("/api/runs/{run_id}/open")
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


@app.post("/api/runs/{run_id}/result")
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
    return save_result(
        run_id,
        state,
        f"record_result_{payload.result}",
        before_state=record["state"],
        idempotency_key=key,
    )


@app.get("/api/runs/{run_id}/events")
def list_run_events(run_id: str) -> list[dict[str, Any]]:
    if not store.get(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return store.events(run_id)
