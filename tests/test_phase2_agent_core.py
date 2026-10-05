from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from packages.agent_core.contracts import LeadAgentInput, LeadAgentOutput
from packages.agent_core.evaluation import load_cases, run_evaluation
from packages.agent_core.pipeline import POLICY_GATE_PROVIDER, generate_lead_suggestion
from packages.agent_core.prompting import PromptBundle, build_prompt
from packages.agent_core.providers import (
    GenerationResult,
    MockProvider,
    ProviderResponseError,
    QwenProvider,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def first_case() -> LeadAgentInput:
    return load_cases(PROJECT_ROOT / "evals" / "phase2_cases.v1.json")[0].input


class FailingProvider:
    """Stands in for a model that must not be reached, or that errors out."""

    provider_name = "failing"
    model_name = "failing-v1"

    def __init__(self) -> None:
        self.calls = 0

    def generate(
        self, agent_input: LeadAgentInput, prompt: PromptBundle
    ) -> GenerationResult:
        self.calls += 1
        raise ProviderResponseError("模拟模型调用失败")


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

    assert report.summary["total"] == 6
    assert report.summary["route_accuracy"] == 1
    assert report.summary["pass_rate"] == 1
    assert report.summary["policy_gate_rejects"] == 3


@pytest.mark.parametrize("prior_outcome", ["declined", "read_no_reply", "blocked"])
def test_no_contact_outcome_is_rejected_without_calling_provider(
    prior_outcome: str,
) -> None:
    agent_input = first_case().model_copy(update={"prior_outcome": prior_outcome})
    provider = FailingProvider()

    result = generate_lead_suggestion(agent_input, provider)

    assert provider.calls == 0
    assert result.provider == POLICY_GATE_PROVIDER
    assert result.output.route == "reject"
    assert result.output.draft == ""
    assert result.output.quality_flags == ["repeat_contact"]


@pytest.mark.parametrize("prior_outcome", ["none", "replied"])
def test_contactable_outcome_reaches_provider(prior_outcome: str) -> None:
    agent_input = first_case().model_copy(update={"prior_outcome": prior_outcome})

    result = generate_lead_suggestion(agent_input, MockProvider())

    assert result.provider == "mock"


def test_evaluation_records_provider_error_and_continues() -> None:
    cases = load_cases(PROJECT_ROOT / "evals" / "phase2_cases.v1.json")
    report = run_evaluation(cases, FailingProvider())

    assert report.summary["total"] == 6
    assert report.summary["provider_errors"] == 3
    assert report.summary["policy_gate_rejects"] == 3
    assert report.summary["passed"] == 3


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


def test_first_message_style_checks_catch_the_old_template() -> None:
    from packages.agent_core.style import first_message_style_issues

    old = (
        "您好，关注到睿曼西服的婚礼西装案例也很显质感。我们是毛衫工厂，累计已承接过约200家西装定制店的业务，"
        "品质有保障，支持一件起订，没有存货压力。不知贵店目前在西装内搭方面是否有相关需求？"
    )
    issues = first_message_style_issues(old, "store_account")
    assert any("主动权" in issue for issue in issues)
    assert any("罗列" in issue for issue in issues)
    assert any("夸奖" in issue for issue in issues)

    new = "看到你用LP羊绒法兰绒做的那套美拉德西装，驳头的弧度很少见。我们是做西装内搭的毛衫工厂，这种厚度的法兰绒，你一般让客人里面配高领还是开衫？"
    assert first_message_style_issues(new, "personal_shop") == []


def test_audience_is_decided_by_the_program() -> None:
    from packages.agent_core.screening import ShopExtraction, audience_of

    base = {"shop_name": "店", "shop_type": "suit_custom"}
    assert audience_of(ShopExtraction.model_validate({**base, "account_kind": "enterprise_verified"})) == "store_account"
    assert audience_of(ShopExtraction.model_validate({**base, "account_kind": "personal"})) == "personal_shop"
    assert audience_of(ShopExtraction.model_validate({**base, "account_kind": "personal", "store_mentions": ["A店", "B店"]})) == "store_account"


def test_sameness_separates_template_from_tailored() -> None:
    from packages.agent_core.style import first_message_style_issues, sameness

    template = ["您好A店。我们是毛衫工厂，累计约200家，品质有保障，支持一件起订。是否有需求？",
                "您好B店。我们是毛衫工厂，累计约200家，品质有保障，支持一件起订。是否有需求？"]
    tailored = ["那套炭灰窗格双排扣，驳头开得比常见的低。配它你们一般建议高领还是衬衫？",
                "800克羊绒做斗篷，垂下来的线条是靠克重撑住的。里面你会让客人穿什么？"]
    assert sameness(template) > 0.8
    assert sameness(tailored) < 0.1
    assert any("下了结论" in issue for issue in first_message_style_issues("这种轻结构对内搭的厚度要求很高。你们怎么配？"))
