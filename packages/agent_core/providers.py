from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from .contracts import (
    LeadAgentInput,
    LeadAgentOutput,
    PersonalizationPoint,
    validate_evidence_refs,
)
from .prompting import PromptBundle


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ProviderConfigError(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class GenerationResult:
    output: LeadAgentOutput
    provider: str
    model: str
    prompt_version: str
    request_id: str | None
    usage: dict[str, Any]
    latency_ms: int
    raw_response: dict[str, Any]


class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    def generate(
        self, agent_input: LeadAgentInput, prompt: PromptBundle
    ) -> GenerationResult: ...


class QwenProvider:
    provider_name = "qwen"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "qwen3.7-plus-2026-05-26",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout_seconds: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ProviderConfigError("DASHSCOPE_API_KEY 尚未配置")
        self.model_name = model
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def generate(
        self, agent_input: LeadAgentInput, prompt: PromptBundle
    ) -> GenerationResult:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "lead_agent_output",
                    "strict": True,
                    "schema": LeadAgentOutput.model_json_schema(),
                },
            },
            "enable_thinking": False,
            "temperature": 0.2,
        }
        started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message.content is not text")
            output = LeadAgentOutput.model_validate_json(content)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError(f"Qwen 返回结果无法通过结构化校验: {exc}") from exc

        invalid_refs = validate_evidence_refs(agent_input, output)
        if invalid_refs:
            raise ProviderResponseError(
                f"Qwen 引用了不存在的 evidence_id: {', '.join(invalid_refs)}"
            )
        return GenerationResult(
            output=output,
            provider=self.provider_name,
            model=str(body.get("model") or self.model_name),
            prompt_version=prompt.version,
            request_id=str(body.get("id")) if body.get("id") else None,
            usage=body.get("usage") or {},
            latency_ms=round((time.perf_counter() - started) * 1000),
            raw_response=body,
        )


class MockProvider:
    """Deterministic provider for learning the pipeline without an API key."""

    provider_name = "mock"
    model_name = "rule-mock-v1"

    def generate(
        self, agent_input: LeadAgentInput, prompt: PromptBundle
    ) -> GenerationResult:
        started = time.perf_counter()
        primary = agent_input.evidence[0]
        stopped = agent_input.prior_outcome in ("declined", "read_no_reply", "blocked")
        irrelevant = agent_input.shop_type == "other"
        forced = agent_input.human_selected_route
        if forced == "comment":
            route = "comment"
            reason = "人工已选择评论路线，围绕当前内容做非销售型互动"
            draft = f"你们对{primary.text}的展示很直观，细节解释得很清楚。"
            points = [PersonalizationPoint(text=primary.text, evidence_ref=primary.evidence_id)]
            product_match = []
        elif (stopped or irrelevant) and forced is None:
            route = "reject"
            reason = "历史结果要求停止联系" if stopped else "店铺业务与目标产品不匹配"
            draft = ""
            points: list[PersonalizationPoint] = []
            product_match: list[str] = []
        elif agent_input.shop_type == "apparel_studio" and not agent_input.purchase_signal_note:
            route = "comment"
            reason = "内容相关但没有采购信号，先生成非销售型内容互动建议"
            draft = f"你们对{primary.text}的展示很直观，细节解释得很清楚。"
            points = [PersonalizationPoint(text=primary.text, evidence_ref=primary.evidence_id)]
            product_match = []
        else:
            route = "dm"
            reason = "目标店铺与产品方向相关，可进行一次低压力的 B2B 需求询问"
            # Same shape the v5 prompt asks for: their point, one line on us, a question about them.
            draft = (
                f"看到你们最近的内容提到{primary.text}。"
                "我们是做西装内搭的毛衫工厂，想请教一下：客人订这类西装时，里面一般怎么搭？"
            )
            if agent_input.revision_request:
                # Make a reviewer's instruction visible in mock runs.
                draft = f"（已按“{agent_input.revision_request}”修改）" + draft
            points = [PersonalizationPoint(text=primary.text, evidence_ref=primary.evidence_id)]
            product_match = agent_input.product_facts[:4]

        output = LeadAgentOutput(
            route=route,
            route_reason=reason,
            shop_business_summary=agent_input.business_context_note,
            evidence_refs=[primary.evidence_id],
            personalization_points=points,
            product_match=product_match,
            draft=draft,
            quality_flags=[],
            uncertainties=([] if agent_input.purchase_signal_note else ["没有可核验的采购信号"]),
            human_review_required=True,
        )
        return GenerationResult(
            output=output,
            provider=self.provider_name,
            model=self.model_name,
            prompt_version=prompt.version,
            request_id=None,
            usage={},
            latency_ms=round((time.perf_counter() - started) * 1000),
            raw_response={"mock": True, "output": output.model_dump(mode="json")},
        )


def provider_from_env(name: str | None = None) -> LLMProvider:
    load_dotenv(PROJECT_ROOT / ".env")
    selected = (name or os.getenv("AGENT_LLM_PROVIDER", "mock")).strip().lower()
    if selected == "mock":
        return MockProvider()
    if selected != "qwen":
        raise ProviderConfigError(f"不支持的 AGENT_LLM_PROVIDER: {selected}")
    return QwenProvider(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        model=os.getenv("QWEN_MODEL", "qwen3.7-plus-2026-05-26"),
        base_url=os.getenv(
            "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        timeout_seconds=float(os.getenv("QWEN_TIMEOUT_SECONDS", "60")),
    )
