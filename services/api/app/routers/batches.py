"""Endpoints for screenshot screening batches."""

from __future__ import annotations

from datetime import datetime
from threading import Thread
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
# request.form() yields Starlette's UploadFile; FastAPI's is a subclass of it.
from starlette.datastructures import UploadFile
from langgraph.types import Command
from pydantic import BaseModel, Field


from ..runtime import (  # noqa: E402
    store,
    read_shop,
    batch_graph,
    batch_lock,
    ALLOWED_IMAGE_TYPES,
    MAX_IMAGE_BYTES,
)
from packages.agent_core.screening import (  # noqa: E402
    MAX_IMAGES_PER_SHOP,
    MAX_SHOPS_PER_BATCH,
    ShopType as ScreeningShopType,
    today_in_shanghai,
)
from services.worker.screening import UPLOAD_ROOT, purge_old_uploads  # noqa: E402
from packages.workflows.screening_workflow import classify_error, rank_results  # noqa: E402
from ..flows.run_flow import (  # noqa: E402
    Channel,
)
from ..flows.batch_flow import (  # noqa: E402
    batch_config,
    batch_payload,
    draft_selected_shops,
)


router = APIRouter()


# --- Phase 2C: screenshot batches -------------------------------------------


class BatchDecision(BaseModel):
    shop_key: str = Field(min_length=1, max_length=50)
    route: Channel = "none"
    tier_feedback: Literal["agree", "disagree"] | None = None
    note: str | None = Field(default=None, max_length=500)


class BatchReview(BaseModel):
    decisions: list[BatchDecision] = Field(max_length=MAX_SHOPS_PER_BATCH)


@router.get("/api/batches")
def list_batches() -> list[dict[str, Any]]:
    purge_old_uploads()
    return store.list_batches()


@router.post("/api/batches")
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


@router.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    payload = store.get_batch(batch_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Batch not found")
    return payload


@router.get("/api/batches/{batch_id}/images/{file_name}")
def get_batch_image(batch_id: str, file_name: str) -> FileResponse:
    folder = (UPLOAD_ROOT / batch_id).resolve()
    path = (folder / file_name).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(status_code=404, detail="原图不存在或已按 10 天保存期限删除")
    return FileResponse(path)


class ShopCorrection(BaseModel):
    shop_type: ScreeningShopType | None = None
    xhs_id: str | None = Field(default=None, max_length=100)


@router.post("/api/batches/{batch_id}/shops/{shop_key}/correct")
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
    changes = {"shop_type": payload.shop_type, "xhs_id": payload.xhs_id}
    for field, value in changes.items():
        if value is None:
            continue
        current["corrections"] = [
            *current.get("corrections", []),
            {"shop_key": shop_key, "field": field, "before": target["extraction"].get(field),
             "after": value, "at": datetime.now().isoformat()},
        ]
        target["extraction"][field] = value
    current["ranked"] = rank_results(
        current["results"], today_in_shanghai(),
        lambda name, xhs_id="": store.is_do_not_contact(name, None, xhs_id),
        current["dm_slots"], current["comment_slots"],
    )
    store.save_batch(batch_id, current["status"], current)
    return current


@router.post("/api/batches/{batch_id}/shops/{shop_key}/retry")
async def retry_batch_shop(batch_id: str, shop_key: str, request: Request) -> dict[str, Any]:
    """Read one failed shop again inside the same batch, optionally with replacement screenshots.

    User request 2026-10-05: a failed shop must not force a whole new batch and a
    second upload of every picture.
    """
    current = store.get_batch(batch_id)
    if not current:
        raise HTTPException(status_code=404, detail="Batch not found")
    if current["status"] != "awaiting_review":
        raise HTTPException(status_code=409, detail="只有待审核的批次可以重新读取")
    target = next((r for r in current["results"] if r["shop_key"] == shop_key), None)
    if target is None:
        raise HTTPException(status_code=404, detail="找不到这家店")

    form = await request.form() if request.headers.get("content-type", "").startswith("multipart/") else None
    files = [item for item in form.getlist("images") if isinstance(item, UploadFile)] if form else []
    if len(files) > MAX_IMAGES_PER_SHOP:
        raise HTTPException(status_code=409, detail=f"too_many_images: 每家店最多 {MAX_IMAGES_PER_SHOP} 张截图")
    if files:
        folder = UPLOAD_ROOT / batch_id
        folder.mkdir(parents=True, exist_ok=True)
        new_images: list[tuple[str, bytes]] = []
        for number, upload in enumerate(files):
            suffix = ALLOWED_IMAGE_TYPES.get(upload.content_type or "")
            if suffix is None:
                raise HTTPException(status_code=409, detail=f"invalid_image: {upload.filename} 不是 JPG、PNG 或 WebP 图片")
            data = await upload.read()
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=409, detail=f"image_too_large: {upload.filename} 超过 10MB")
            new_images.append((f"{shop_key}_r{len(current.get('retries', []))}_{number}{suffix}", data))
        paths = []
        for name, data in new_images:
            (folder / name).write_bytes(data)
            paths.append(str(folder / name))
        target["image_paths"] = paths
    elif not all(Path(path).is_file() for path in target["image_paths"]):
        raise HTTPException(status_code=409, detail="原图已按 10 天保存期限删除，请重新选择截图")

    shop = {"batch_id": batch_id, "shop_key": shop_key, "image_paths": target["image_paths"]}
    try:
        extraction = read_shop(shop)
    except Exception as exc:  # still failing: keep the shop in the batch with the new reason
        target.update(status="failed", error=str(exc), error_kind=classify_error(exc))
        target.pop("extraction", None)
    else:
        target.update(status="read", extraction=extraction.model_dump())
        target.pop("error", None)
        target.pop("error_kind", None)
    current["retries"] = [
        *current.get("retries", []),
        {"shop_key": shop_key, "new_images": bool(files), "status": target["status"], "at": datetime.now().isoformat()},
    ]
    current["ranked"] = rank_results(
        current["results"], today_in_shanghai(),
        lambda name, xhs_id="": store.is_do_not_contact(name, None, xhs_id),
        current["dm_slots"], current["comment_slots"],
    )
    store.save_batch(batch_id, current["status"], current)
    return current


@router.post("/api/batches/{batch_id}/review")
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
