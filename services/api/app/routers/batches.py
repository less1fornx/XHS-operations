"""Endpoints for screenshot screening batches."""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote
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
    shop_extractor,
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
from services.worker.inbox import (  # noqa: E402
    DONE_DIR, SEARCH_DIR, batch_stamp, inbox_dir, list_pending, move_into, pending_by_shop, plan_import, save_pasted,
)
from packages.agent_core.vision import identify_screenshot  # noqa: E402
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
SEARCH_KEYWORDS_PATH = Path(__file__).resolve().parents[4] / "config" / "search_keywords.v1.json"


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

    return run_new_batch(batch_id, shops)


def run_new_batch(batch_id: str, shops: list[dict[str, Any]]) -> dict[str, Any]:
    """Read, rank and save a batch whose screenshots are already in the upload folder."""
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


def _inbox_state() -> tuple[Path | None, str]:
    root = inbox_dir()
    if root is None:
        return None, "not_configured"
    try:
        return (root, "ready") if root.is_dir() else (root, "unreachable")
    except OSError:
        return root, "unreachable"


@router.get("/api/inbox")
def inbox_status() -> dict[str, Any]:
    """How many new screenshots wait in the shared folder. Reads file names only, no model call."""
    root, state = _inbox_state()
    if state != "ready":
        return {"state": state, "path": str(root) if root else None, "pending": 0, "names": []}
    try:
        files = list_pending(root)
    except OSError:
        return {"state": "unreachable", "path": str(root), "pending": 0, "names": []}
    names = [str(f.relative_to(root)) for f in files]
    search_dir = root / SEARCH_DIR
    return {
        "state": "ready", "path": str(root), "pending": len(files), "names": names[:30],
        "shops": pending_by_shop(root, files),
        "search_pages": sum(1 for f in search_dir.iterdir() if f.is_file()) if search_dir.is_dir() else 0,
    }


@router.post("/api/inbox/paste")
async def paste_into_inbox(request: Request) -> dict[str, Any]:
    """Save one screenshot pasted on the page into the shared folder, named so it groups by shop."""
    root, state = _inbox_state()
    if state == "not_configured":
        raise HTTPException(status_code=409, detail="inbox_not_configured: 还没有设置截图文件夹")
    if state != "ready":
        raise HTTPException(status_code=409, detail=f"inbox_unreachable: 截图文件夹连不上（{root}）。请确认共享盘已经连接。")
    form = await request.form()
    upload = form.get("image")
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=409, detail="no_image: 剪贴板里没有图片。请先截图，再粘贴。")
    suffix = ALLOWED_IMAGE_TYPES.get(upload.content_type or "")
    if suffix is None:
        raise HTTPException(status_code=409, detail="invalid_image: 只支持 JPG、PNG 或 WebP 图片")
    data = await upload.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=409, detail="image_too_large: 图片超过 10MB")
    kind = "search" if form.get("kind") == "search" else "shop"
    try:
        saved = save_pasted(root, data, suffix, kind, str(form.get("label") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"invalid_label: {exc}") from exc
    return {"saved": str(saved.relative_to(root)), "kind": kind}


@router.get("/api/search-suggestions")
def search_suggestions(seed: str = "", city: str = "") -> dict[str, Any]:
    """Turn one search word into several that are more likely to surface target shops.

    Built by rule from config, no model call. Each item is a link the human opens;
    the system never runs the search or touches the result page.
    """
    config = json.loads(SEARCH_KEYWORDS_PATH.read_text(encoding="utf-8"))
    base = " ".join(seed.split())[:30] or config["default_seed"]
    place = " ".join(city.split())[:10]
    items, seen = [], set()
    for variant in config["variants"]:
        words = [variant["replace"]] if variant.get("replace") else [base, variant.get("suffix", "")]
        if variant.get("city") and place:
            words.insert(0, place)
        query = " ".join(word for word in words if word)
        if query in seen or (query == base and not place):
            continue
        seen.add(query)
        items.append({"query": query, "why": variant["why"],
                      "url": "https://www.xiaohongshu.com/search_result?keyword=" + quote(query)})
    return {"seed": base, "city": place, "items": [
        {"query": base, "why": "你输入的原词", "url": "https://www.xiaohongshu.com/search_result?keyword=" + quote(base)}, *items,
    ]}


@router.post("/api/inbox/import")
def import_inbox() -> dict[str, Any]:
    """Sort the waiting screenshots into shops, screen them as one batch, then file the originals away."""
    root, state = _inbox_state()
    if state == "not_configured":
        raise HTTPException(status_code=409, detail="inbox_not_configured: 还没有设置截图文件夹")
    if state != "ready":
        raise HTTPException(status_code=409, detail=f"inbox_unreachable: 截图文件夹连不上（{root}）。请确认共享盘已经连接。")
    files = list_pending(root)
    if not files:
        raise HTTPException(status_code=409, detail="inbox_empty: 截图文件夹里没有新的截图")

    def identify(file: Path) -> Any:
        return identify_screenshot(shop_extractor, (file.name, file.read_bytes()))

    plan = plan_import(root, files, identify)
    stamp = batch_stamp()
    skipped = []
    for item in plan.skipped:
        if item["move_to"]:
            move_into(root, item["file"], item["move_to"])
        skipped.append({"name": item["file"].name, "reason": item["reason"]})
    if not plan.groups:
        return {"batch": None, "skipped": skipped, "waiting": plan.waiting, "groups": []}

    batch_id = str(uuid4())
    folder = UPLOAD_ROOT / batch_id
    folder.mkdir(parents=True, exist_ok=True)
    shops, groups = [], []
    for index, group in enumerate(plan.groups):
        paths = []
        for number, source in enumerate(group["files"]):
            # Local copies follow the 10-day retention rule; the shared folder keeps the originals.
            target = folder / f"shop_{index}_{number}{source.suffix.lower()}"
            target.write_bytes(source.read_bytes())
            paths.append(str(target))
        shops.append({"batch_id": batch_id, "shop_key": f"shop_{index}", "image_paths": paths})
        groups.append({"shop_key": f"shop_{index}", "label": group["label"], "images": len(group["files"]), "unused": len(group["unused"])})
    payload = run_new_batch(batch_id, shops)
    payload["inbox"] = {"groups": groups, "stamp": stamp}
    store.save_batch(batch_id, payload["status"], payload)
    # File the originals away only after the batch is safely saved.
    for group in plan.groups:
        for source in [*group["files"], *group["unused"]]:
            move_into(root, source, DONE_DIR, stamp)
    return {"batch": payload, "skipped": skipped, "waiting": plan.waiting, "groups": groups}


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
