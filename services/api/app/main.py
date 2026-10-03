from __future__ import annotations

import sys
import os
import sqlite3
import hashlib
import json
from datetime import datetime
from threading import Lock, Thread
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
# request.form() yields Starlette's UploadFile; FastAPI's is a subclass of it.
from starlette.datastructures import UploadFile
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
from packages.agent_core.screening import (  # noqa: E402
    MAX_IMAGES_PER_SHOP,
    MAX_SHOPS_PER_BATCH,
    ShopExtraction,
    ShopScreening,
    ShopType as ScreeningShopType,
    to_lead_context,
    today_in_shanghai,
)
from packages.agent_core.outcomes import (  # noqa: E402
    ASK_TAG_GROUPS,
    ASK_TAGS,
    NORMAL_REPLY_DAYS,
    OUTCOME_RULES_VERSION,
    REPLY_WINDOW_DAYS,
    STAGES,
    keeps_do_not_contact,
    reply_timing,
    scrub_contact_info,
    wants_product_info,
)
from packages.agent_core.providers import ProviderResponseError  # noqa: E402
from packages.agent_core.vision import read_reply, shop_extractor_from_env  # noqa: E402
from services.worker.screening import UPLOAD_ROOT, make_read_shop, purge_old_uploads  # noqa: E402
from services.worker.screening_graph import build_batch_graph, rank_results  # noqa: E402


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
shop_extractor = shop_extractor_from_env()
batch_graph = build_batch_graph(
    make_read_shop(store, shop_extractor),
    lambda name: store.is_do_not_contact(name),
    today_in_shanghai,
    checkpointer=checkpointer,
)
batch_lock = Lock()
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
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
    # Set by batch screening so the contact snapshot knows the tier and signals.
    screening_snapshot: dict[str, Any] | None = None


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


def close_unanswered() -> None:
    """User rule: 15 days without a reply ends the task as no-reply."""
    for run_id in store.unanswered_runs(REPLY_WINDOW_DAYS):
        store.record_reply(
            run_id, replied=False, stage="no_reply", ask_tags=[], reply_text=None, replied_at=None,
            note=f"发送后 {REPLY_WINDOW_DAYS} 天未回复，系统自动结束", keep_do_not_contact=True,
            timing_fn=reply_timing, system=True,
        )


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    store.expire_stale_reviews(REVIEW_TIMEOUT_HOURS)
    close_unanswered()
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


def start_run(payload: RunCreate) -> dict[str, Any]:
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
        "screening_snapshot": payload.screening_snapshot or {},
        "trace": [],
        "route_review_status": "not_started",
        "content_review_status": "not_started",
        "task_status": "new",
    }
    with graph_lock:
        state = graph.invoke(initial_state, config_for(run_id))
    return save_result(run_id, state, "run_created")


@app.post("/api/runs")
def create_run(payload: RunCreate) -> dict[str, Any]:
    return start_run(payload)


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
    if payload.result == "sent":
        store.save_contact_snapshot(run_id, state)
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


# --- Phase 2C: screenshot batches -------------------------------------------


class BatchDecision(BaseModel):
    shop_key: str = Field(min_length=1, max_length=50)
    route: Channel = "none"
    tier_feedback: Literal["agree", "disagree"] | None = None
    note: str | None = Field(default=None, max_length=500)


class BatchReview(BaseModel):
    decisions: list[BatchDecision] = Field(max_length=MAX_SHOPS_PER_BATCH)


def batch_config(batch_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": f"batch-{batch_id}"}}


def batch_payload(batch_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": batch_id,
        "status": state.get("status", "processing"),
        "created_at": state.get("created_at"),
        "dm_slots": state.get("dm_slots"),
        "comment_slots": state.get("comment_slots"),
        "results": state.get("results", []),
        "ranked": state.get("ranked", []),
        "review": state.get("review"),
        "drafts": state.get("drafts", []),
    }


@app.get("/api/batches")
def list_batches() -> list[dict[str, Any]]:
    purge_old_uploads()
    return store.list_batches()


@app.post("/api/batches")
async def create_batch(request: Request) -> dict[str, Any]:
    form = await request.form()
    batch_id = str(uuid4())
    folder = UPLOAD_ROOT / batch_id
    shops: list[dict[str, Any]] = []
    for index in range(MAX_SHOPS_PER_BATCH + 1):
        files = [item for item in form.getlist(f"shop_{index}") if isinstance(item, UploadFile)]
        if not files:
            continue
        if len(shops) >= MAX_SHOPS_PER_BATCH:
            raise HTTPException(status_code=409, detail=f"too_many_shops: 每批最多 {MAX_SHOPS_PER_BATCH} 家店")
        if len(files) > MAX_IMAGES_PER_SHOP:
            raise HTTPException(status_code=409, detail=f"too_many_images: 每家店最多 {MAX_IMAGES_PER_SHOP} 张截图")
        shop_key = f"shop_{index}"
        paths: list[str] = []
        for number, upload in enumerate(files):
            suffix = ALLOWED_IMAGE_TYPES.get(upload.content_type or "")
            if suffix is None:
                raise HTTPException(status_code=409, detail=f"invalid_image: {upload.filename} 不是 JPG、PNG 或 WebP 图片")
            data = await upload.read()
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=409, detail=f"image_too_large: {upload.filename} 超过 10MB")
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{shop_key}_{number}{suffix}"
            path.write_bytes(data)
            paths.append(str(path))
        shops.append({"batch_id": batch_id, "shop_key": shop_key, "image_paths": paths})
    if not shops:
        raise HTTPException(status_code=409, detail="no_images: 请至少为一家店上传截图")

    summary = store.account_summary()
    initial = {
        "batch_id": batch_id,
        "shops": shops,
        "dm_slots": max(summary["dm"]["limit"] - summary["dm"]["used"], 0),
        "comment_slots": max(summary["comment"]["limit"] - summary["comment"]["used"], 0),
        "results": [],
        "status": "processing",
    }
    with batch_lock:
        state = batch_graph.invoke(initial, batch_config(batch_id))
    payload = batch_payload(batch_id, {**state, "created_at": datetime.now().isoformat()})
    store.save_batch(batch_id, payload["status"], payload)
    return payload


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    payload = store.get_batch(batch_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Batch not found")
    return payload


@app.get("/api/batches/{batch_id}/images/{file_name}")
def get_batch_image(batch_id: str, file_name: str) -> FileResponse:
    folder = (UPLOAD_ROOT / batch_id).resolve()
    path = (folder / file_name).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(status_code=404, detail="原图不存在或已按 10 天保存期限删除")
    return FileResponse(path)


def draft_selected_shops(batch_id: str, selected: list[tuple[dict[str, Any], str]]) -> None:
    """Hand each approved shop to the existing single-shop workflow, route preset."""
    for item, route in selected:
        extraction = ShopExtraction.model_validate(item["extraction"])
        screening = ShopScreening.model_validate(item["screening"])
        context = to_lead_context(extraction, screening)
        basis = "批量初筛入选（{}）：{}".format(
            screening.rules_version, "；".join(screening.tier_reasons)
        )
        if screening.content_angles:
            basis += "。建议切入点：" + "；".join(screening.content_angles)
        record = {"shop_key": item["shop_key"], "shop_name": extraction.shop_name, "route": route}
        try:
            run = start_run(
                RunCreate(
                    **context,
                    contact_basis="cold_outreach",
                    contact_basis_note=basis,
                    screening_snapshot={**screening.model_dump(), "account_kind": extraction.account_kind},
                )
            )
            with graph_lock:
                state = graph.invoke(Command(resume={"requested_channel": route}), config_for(run["id"]))
            save_result(run["id"], state, "route_decided", before_state=run["state"])
            record.update(run_id=run["id"], status="drafted")
        except Exception as exc:  # one shop failing must not stop the others
            record.update(status="failed", error=str(exc))
        payload = store.get_batch(batch_id) or {}
        payload["drafts"] = [*payload.get("drafts", []), record]
        if len(payload["drafts"]) == len(selected):
            payload["status"] = "drafted"
        store.save_batch(batch_id, payload.get("status", "drafting"), payload)


class ShopCorrection(BaseModel):
    shop_type: ScreeningShopType


@app.post("/api/batches/{batch_id}/shops/{shop_key}/correct")
def correct_batch_shop(batch_id: str, shop_key: str, payload: ShopCorrection) -> dict[str, Any]:
    """Human fixes what the model misread; the shop is re-tiered by the same rules."""
    current = store.get_batch(batch_id)
    if not current:
        raise HTTPException(status_code=404, detail="Batch not found")
    if current["status"] != "awaiting_review":
        raise HTTPException(status_code=409, detail="只有待审核的批次可以改正识别结果")
    target = next((r for r in current["results"] if r["shop_key"] == shop_key and r["status"] == "read"), None)
    if target is None:
        raise HTTPException(status_code=404, detail="找不到这家店的识别结果")
    before = target["extraction"]["shop_type"]
    target["extraction"]["shop_type"] = payload.shop_type
    current["corrections"] = [
        *current.get("corrections", []),
        {"shop_key": shop_key, "field": "shop_type", "before": before, "after": payload.shop_type,
         "at": datetime.now().isoformat()},
    ]
    current["ranked"] = rank_results(
        current["results"], today_in_shanghai(), lambda name: store.is_do_not_contact(name),
        current["dm_slots"], current["comment_slots"],
    )
    store.save_batch(batch_id, current["status"], current)
    return current


@app.post("/api/batches/{batch_id}/review")
def review_batch(batch_id: str, payload: BatchReview) -> dict[str, Any]:
    current = store.get_batch(batch_id)
    if not current:
        raise HTTPException(status_code=404, detail="Batch not found")
    if current["status"] != "awaiting_review":
        raise HTTPException(status_code=409, detail="当前批次不在待审核状态")
    by_key = {item["shop_key"]: item for item in current["ranked"]}
    unknown = [d.shop_key for d in payload.decisions if d.shop_key not in by_key]
    if unknown:
        raise HTTPException(status_code=409, detail=f"unknown_shop: {', '.join(unknown)}")
    decision = payload.model_dump()
    with batch_lock:
        state = batch_graph.invoke(Command(resume=decision), batch_config(batch_id))
    selected = [
        (by_key[d.shop_key], d.route)
        for d in payload.decisions
        if d.route in ("dm", "comment") and by_key[d.shop_key]["screening"]["tier"] != "excluded"
    ]
    result = {**current, "review": state.get("review"), "status": "drafting" if selected else "reviewed", "drafts": []}
    store.save_batch(batch_id, result["status"], result)
    if selected:
        Thread(target=draft_selected_shops, args=(batch_id, selected), daemon=True).start()
    return result


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
        if not self.replied and self.stage != "no_reply":
            raise ValueError("对方没有回复时，阶段只能是未回复")
        return self


@app.get("/api/outcome-options")
def outcome_options() -> dict[str, Any]:
    return {
        "rules_version": OUTCOME_RULES_VERSION,
        "normal_reply_days": NORMAL_REPLY_DAYS,
        "reply_window_days": REPLY_WINDOW_DAYS,
        "stages": STAGES,
        "ask_tag_groups": ASK_TAG_GROUPS,
    }


@app.post("/api/runs/{run_id}/reply")
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
            note=payload.note,
            keep_do_not_contact=keeps_do_not_contact(payload.replied, payload.stage, payload.ask_tags),
            timing_fn=reply_timing,
        )
    except BusinessRuleError as exc:
        status = 404 if exc.code == "run_not_found" else 409
        raise HTTPException(status_code=status, detail=f"{exc.code}: {exc.message}") from exc
    result["state"]["reply"]["wants_product_info"] = wants_product_info(payload.ask_tags)
    return result


@app.post("/api/runs/{run_id}/reply/read")
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


@app.get("/api/reply-records")
def reply_records() -> list[dict[str, Any]]:
    return store.list_reply_records()
