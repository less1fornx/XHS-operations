from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from packages.agent_core.contracts import LeadAgentInput, LeadAgentOutput
from packages.agent_core.evaluation import load_cases, run_evaluation
from packages.agent_core.prompting import build_prompt
from packages.agent_core.providers import MockProvider, QwenProvider


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def first_case() -> LeadAgentInput:
    return load_cases(PROJECT_ROOT / "evals" / "phase2_cases.v1.json")[0].input


def test_reject_output_cannot_include_a_draft() -> None:
    with pytest.raises(ValidationError):
        LeadAgentOutput(
            route="reject",
            route_reason="不应联系",
            shop_business_summary="业务不匹配",
            evidence_refs=["e1"],
            personalization_points=[],
            product_match=[],
            draft="仍然发送一条消息",
            quality_flags=[],
            uncertainties=[],
            human_review_required=True,
        )


def test_all_routes_require_human_review() -> None:
    with pytest.raises(ValidationError, match="所有路线都必须进入人工审核"):
        LeadAgentOutput(
            route="dm",
            route_reason="业务匹配",
            shop_business_summary="西装定制店",
            evidence_refs=["evidence-001"],
            personalization_points=[],
            product_match=["内搭"],
            draft="你好，想了解一下你们近期是否有内搭需求？",
            quality_flags=[],
            uncertainties=[],
            human_review_required=False,
        )


def test_mock_provider_runs_fixed_evaluation_set() -> None:
    cases = load_cases(PROJECT_ROOT / "evals" / "phase2_cases.v1.json")
    report = run_evaluation(cases, MockProvider())

    assert report.summary["total"] == 4
    assert report.summary["route_accuracy"] == 1
    assert report.summary["pass_rate"] == 1


def test_qwen_provider_sends_strict_json_schema() -> None:
    captured: dict = {}
    agent_input = first_case()
    output = LeadAgentOutput(
        route="dm",
        route_reason="业务匹配，可进行低压力询问",
        shop_business_summary=agent_input.business_context_note,
        evidence_refs=["e1"],
        personalization_points=[{"text": "春夏轻薄西装", "evidence_ref": "e1"}],
        product_match=["西装", "衬衫", "内搭", "支持一件起订"],
        draft="你好，看到你们分享春夏轻薄西装。我们提供西装、衬衫和内搭，也支持一件起订，想了解近期是否有相关产品需求？",
        quality_flags=[],
        uncertainties=[],
        human_review_required=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "req-test",
                "model": "qwen3.7-plus-2026-05-26",
                "choices": [{"message": {"content": output.model_dump_json()}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 80},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = QwenProvider(api_key="test-key", client=client)
    result = provider.generate(agent_input, build_prompt(agent_input))

    assert result.output.route == "dm"
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["enable_thinking"] is False
    assert "max_tokens" not in captured
