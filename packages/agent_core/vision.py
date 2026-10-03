from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from .providers import PROJECT_ROOT, ProviderConfigError, ProviderResponseError
from .screening import ShopExtraction


SHOP_EXTRACTION_PROMPT_VERSION = "shop-extraction-v1"
SHOP_EXTRACTION_PROMPT_PATH = PROJECT_ROOT / "prompts" / "shop_extraction" / "v1" / "system.md"

Image = tuple[str, bytes]  # (file name, content)


def extraction_prompt_sha256() -> str:
    return hashlib.sha256(SHOP_EXTRACTION_PROMPT_PATH.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ExtractionResult:
    extraction: ShopExtraction
    provider: str
    model: str
    prompt_version: str
    request_id: str | None
    usage: dict[str, Any]
    latency_ms: int


class ShopExtractor(Protocol):
    provider_name: str
    model_name: str

    def extract(self, images: list[Image]) -> ExtractionResult: ...


class QwenShopExtractor:
    """Reads 1-3 screenshots of one account and returns verbatim facts."""

    provider_name = "qwen"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout_seconds: float = 90,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ProviderConfigError("DASHSCOPE_API_KEY 尚未配置")
        self.model_name = model
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def extract(self, images: list[Image]) -> ExtractionResult:
        content: list[dict[str, Any]] = []
        for name, data in images:
            mime = mimetypes.guess_type(name)[0] or "image/jpeg"
            encoded = base64.b64encode(data).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        content.append({"type": "text", "text": "请记录这些截图中的店铺信息。"})
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": SHOP_EXTRACTION_PROMPT_PATH.read_text(encoding="utf-8")},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "shop_extraction",
                    "strict": True,
                    "schema": ShopExtraction.model_json_schema(),
                },
            },
            "enable_thinking": False,
            "temperature": 0,
        }
        started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions", headers=self._headers, json=payload
            )
            response.raise_for_status()
            body = response.json()
            text = body["choices"][0]["message"]["content"]
            extraction = ShopExtraction.model_validate_json(text)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError(f"截图识别失败: {exc}") from exc
        return ExtractionResult(
            extraction=extraction,
            provider=self.provider_name,
            model=str(body.get("model") or self.model_name),
            prompt_version=SHOP_EXTRACTION_PROMPT_VERSION,
            request_id=str(body.get("id")) if body.get("id") else None,
            usage=body.get("usage") or {},
            latency_ms=round((time.perf_counter() - started) * 1000),
        )


class MockShopExtractor:
    """Deterministic stand-in. A file name containing "fail" simulates a read failure."""

    provider_name = "mock"
    model_name = "mock-vision-v1"

    def __init__(self, fixtures: dict[str, ShopExtraction] | None = None) -> None:
        self.fixtures = fixtures or {}

    def extract(self, images: list[Image]) -> ExtractionResult:
        first = Path(images[0][0]).stem
        if any("fail" in name for name, _ in images):
            raise ProviderResponseError("截图识别失败: 模拟的识别错误")
        extraction = self.fixtures.get(first) or ShopExtraction(
            shop_name=first,
            account_kind="personal",
            shop_type="suit_custom",
            bio_text=f"{first} 西装定制，全手工",
            followers_text="1200",
            location_text=None,
            price_mentions=[],
            fabric_mentions=["全麻衬"],
            high_end_mentions=["全手工"],
            store_mentions=["线下门店"],
            recent_post_dates=["昨天"],
            content_topics=["春夏亚麻西装"],
            knitwear_present="no",
            unclear_fields=[],
        )
        return ExtractionResult(
            extraction=extraction,
            provider=self.provider_name,
            model=self.model_name,
            prompt_version=SHOP_EXTRACTION_PROMPT_VERSION,
            request_id=None,
            usage={},
            latency_ms=0,
        )


def shop_extractor_from_env(name: str | None = None) -> ShopExtractor:
    load_dotenv(PROJECT_ROOT / ".env")
    selected = (name or os.getenv("AGENT_LLM_PROVIDER", "mock")).strip().lower()
    if selected == "mock":
        return MockShopExtractor()
    if selected != "qwen":
        raise ProviderConfigError(f"不支持的 AGENT_LLM_PROVIDER: {selected}")
    return QwenShopExtractor(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        # The text model reads images too (verified 2026-10-03); override if needed.
        model=os.getenv("QWEN_VISION_MODEL") or os.getenv("QWEN_MODEL", "qwen3.7-plus-2026-05-26"),
        base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        timeout_seconds=float(os.getenv("QWEN_VISION_TIMEOUT_SECONDS", "90")),
    )
