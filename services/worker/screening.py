from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from packages.agent_core.providers import ProviderResponseError
from packages.agent_core.screening import ShopExtraction
from packages.agent_core.vision import ShopExtractor, extraction_prompt_sha256
from services.api.app.storage import RunStore
from services.worker.screening_graph import ShopInput, classify_error


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = PROJECT_ROOT / "data" / "uploads" / "screening"
# User decision 2026-10-03: original screenshots stay on this machine for 10 days.
SCREENSHOT_RETENTION_DAYS = 10


def make_read_shop(store: RunStore, extractor: ShopExtractor):
    """Read one shop's screenshots, retry once on service errors, record every attempt."""

    def read_shop(shop: ShopInput) -> ShopExtraction:
        images = [(Path(path).name, Path(path).read_bytes()) for path in shop["image_paths"]]
        common = {
            "source": "screening",
            "case_id": f"{shop['batch_id']}:{shop['shop_key']}",
            "input_json": json.dumps(
                {"batch_id": shop["batch_id"], "images": [name for name, _ in images]},
                ensure_ascii=False,
            ),
            "provider": extractor.provider_name,
            "model": extractor.model_name,
            "prompt_sha256": extraction_prompt_sha256(),
        }
        for attempt in (1, 2):
            try:
                result = extractor.extract(images)
            except ProviderResponseError as exc:
                store.record_model_invocation(
                    **common,
                    status="failed",
                    prompt_version="shop-extraction-v1",
                    error_type=type(exc.__cause__).__name__ if exc.__cause__ else "ProviderResponseError",
                    error_message=str(exc),
                )
                if attempt == 1 and classify_error(exc) == "system":
                    continue
                raise
            store.record_model_invocation(
                **common,
                status="succeeded",
                prompt_version=result.prompt_version,
                output_json=result.extraction.model_dump_json(),
                request_id=result.request_id,
                usage_json=json.dumps(result.usage, ensure_ascii=False),
                latency_ms=result.latency_ms,
            )
            return result.extraction
        raise AssertionError("unreachable")

    return read_shop


def purge_old_uploads(
    root: Path = UPLOAD_ROOT, days: int = SCREENSHOT_RETENTION_DAYS, now: float | None = None
) -> list[str]:
    """Delete batch screenshot folders older than the retention period."""
    if not root.exists():
        return []
    cutoff = (now or time.time()) - days * 86400
    removed: list[str] = []
    for folder in root.iterdir():
        if folder.is_dir() and folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder)
            removed.append(folder.name)
    return removed
