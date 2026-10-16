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
    assert any("夸奖" in issue for issue in issues)

    new = (
        "贵店笔记里那件驼色法兰绒单西，写到入秋就能穿。法兰绒里面那一层也得压得住，我们是做高端面料内搭的源头工厂，"
        "也对接了约200家西装定制店，想和贵店合作西装内搭，一件起订，方便的话发几款给您看看。"
    )
    assert first_message_style_issues(new, "personal_shop") == []


def test_style_checks_follow_the_v9_structure() -> None:
    from packages.agent_core.style import first_message_style_issues

    # The v8 shape: "你", no strength, ends on a pairing question.
    v8 = "看到你用LP羊绒法兰绒做的那套美拉德西装，驳头的弧度很少见。我们是做西装内搭的毛衫工厂，这种厚度的法兰绒，你一般让客人里面配高领还是开衫？"
    issues = first_message_style_issues(v8, "personal_shop")
    assert any("“你”" in issue for issue in issues)
    assert any("问句" in issue for issue in issues)
    assert any("实力" in issue for issue in issues)
    assert any("想合作" in issue for issue in issues)

    # Praise for the shop's fabric is not our strength; only what follows "我们" counts.
    theirs = "贵店选的高端面料和做工都摆在笔记里。我们是做西装内搭的毛衫工厂，想和贵店合作西装内搭。"
    assert any("实力" in issue for issue in first_message_style_issues(theirs))
    star = "贵店给明星艺人做的造型很多。我们是做西装内搭的毛衫工厂，注重纱线和做工，想和贵店合作西装内搭。"
    assert any("明星" in issue for issue in first_message_style_issues(star))


def test_mock_draft_passes_the_checks_it_is_drafted_for() -> None:
    from packages.agent_core.pipeline import generate_lead_suggestion
    from packages.agent_core.providers import MockProvider
    from packages.agent_core.style import first_message_style_issues
    from services.worker.lead_generation import build_agent_input, load_product_facts

    state = {"shop_name": "店", "video_text": "海军蓝三件套", "workflow_run_id": "r"}
    result = generate_lead_suggestion(build_agent_input(state, load_product_facts(), "dm"), MockProvider())
    assert first_message_style_issues(result.output.draft) == []


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


def test_style_check_flags_a_wedding_opening() -> None:
    from packages.agent_core.style import first_message_style_issues

    draft = "看到贵店的新郎西服和备婚攻略。我们是做西装内搭的毛衫工厂，这类礼服客人一般怎么搭？"
    assert any("婚礼" in issue for issue in first_message_style_issues(draft, "store_account"))


def test_style_check_flags_a_sentence_copied_from_the_prompt_examples() -> None:
    from packages.agent_core.style import first_message_style_issues

    ours = "我们是做西装内搭的毛衫工厂，同样注重纱线、细节和做工，想和贵店合作西装内搭。"
    copied = "看到贵店那件驼色法兰绒单西。这样的西装，配的内搭也不能将就，" + ours
    own = "看到贵店那件驼色法兰绒单西。法兰绒里面那一层也得压得住，" + ours
    assert any("照抄" in issue and "配的内搭也不能将就" in issue for issue in first_message_style_issues(copied))
    # The part about us repeats the prompt's wording on purpose and must not be flagged.
    assert first_message_style_issues(own) == []


def test_strengths_are_drawn_by_the_program_and_checked() -> None:
    from packages.agent_core.style import first_message_style_issues, load_positionings, pick_strengths
    from services.worker.lead_generation import strengths_for

    every = [item["text"] for item in load_positionings()["suit_inner"]["strengths"]]
    draws = [pick_strengths(f"run-{i}") for i in range(200)]
    # The same run always gets the same set, so a rewrite does not change what it must say.
    assert pick_strengths("run-7") == pick_strengths("run-7")
    assert {len(draw) for draw in draws} == {1, 2}
    assert {text for draw in draws for text in draw} == set(every)
    assert strengths_for({"workflow_run_id": "run-7", "contact_basis": "prior_conversation"}) == []

    opening = "看到贵店那件驼色法兰绒单西。法兰绒里面那一层也得压得住，"
    only_fabric = opening + "我们是做西装内搭的毛衫工厂，做的是高端面料的内搭，想和贵店合作西装内搭。"
    assert first_message_style_issues(only_fabric, strengths=["做高端面料的内搭"]) == []
    wrong = first_message_style_issues(only_fabric, strengths=["累计对接约200家西装定制店"])
    assert any("没有写这一条指定的实力" in issue and "200" in issue for issue in wrong)
    assert any("写了没有指定的实力" in issue and "高端面料" in issue for issue in wrong)
    bridged = "看到贵店那件驼色法兰绒单西。这样的料子，要同样注重纱线、细节和做工的内搭来配，我们是做西装内搭的毛衫工厂，想和贵店合作西装内搭。"
    assert first_message_style_issues(bridged, strengths=["注重纱线、细节和做工"]) == []


def test_positioning_is_forced_by_shop_type() -> None:
    """User rule 2026-10-09: a suit shop hears about suit inner layers, any other account
    hears that we are a flat-knitting factory. The program decides and checks; the model does not."""
    from packages.agent_core.style import first_message_style_issues, pick_strengths, positioning_for
    from services.worker.lead_generation import build_agent_input, load_product_facts

    assert positioning_for("suit_custom") == "suit_inner"
    for other in ("menswear", "apparel_studio", "content_creator", "staff_account", "hobbyist", "supplier", "other"):
        assert positioning_for(other) == "flat_knit"

    facts = load_product_facts()
    suit = build_agent_input({"workflow_run_id": "r1", "shop_name": "某西装店", "video_text": "炭灰法兰绒单西", "shop_type": "suit_custom"}, facts, "dm")
    brand = build_agent_input({"workflow_run_id": "r1", "shop_name": "某品牌", "video_text": "秋季上新工装外套", "shop_type": "menswear"}, facts, "dm")
    assert suit.positioning == "suit_inner" and suit.self_description == "做西装内搭(横机·针织·POLO)的源头工厂"
    assert brand.positioning == "flat_knit" and brand.self_description == "专门做横机针织的工厂"
    # Every knit message carries both points; the suit one draws from its own three.
    assert brand.strengths_to_use == ["有严谨的工艺流程确保产品质量", "产品售后有保障"]
    assert all("工艺" not in text and "售后" not in text for text in suit.strengths_to_use)

    for agent_input in (suit, brand):
        draft = MockProvider().generate(agent_input, build_prompt(agent_input)).output.draft
        assert first_message_style_issues(draft, strengths=agent_input.strengths_to_use, positioning=agent_input.positioning) == []

    opening = "看到贵店新出的那件灯芯绒夹克。往后要加针织款可以交给我们，"
    knit = opening + "我们是专门做横机针织的工厂，工艺流程严谨、质量有保证，售后也有保障，想和贵店合作针织款，一件起订。"
    knit_strengths = pick_strengths("any", "flat_knit")
    assert first_message_style_issues(knit, strengths=knit_strengths, positioning="flat_knit") == []
    # The suit introduction sent to a non-suit account is caught, and the other way round.
    crossed = opening + "我们是做西装内搭(横机·针织·POLO)的源头工厂，工艺流程严谨，售后有保障，想和贵店合作西装内搭。"
    issues = first_message_style_issues(crossed, strengths=knit_strengths, positioning="flat_knit")
    assert any("自称没有按指定的写" in issue for issue in issues) and any("西装内搭" in issue for issue in issues)
    assert any("自称没有按指定的写" in issue for issue in first_message_style_issues(knit, positioning="suit_inner"))
    old_self = "看到贵店那件驼色法兰绒单西。法兰绒里面那一层也得压得住，我们是做西装内搭的源头工厂，想和贵店合作西装内搭。"
    assert any("横机·针织·POLO" in issue for issue in first_message_style_issues(old_self, positioning="suit_inner"))


def test_passing_judgement_on_the_shop_is_flagged() -> None:
    """User 2026-10-09: say what we saw, do not rate the shop or tell it what it demands."""
    from packages.agent_core.style import first_message_style_issues

    ours = "我们是做西装内搭的毛衫工厂，注重纱线、细节和做工，想和贵店合作西装内搭。"
    for verdict in ("这种材质对触感和做工要求极高，", "把职场绅士的氛围感拿捏得很准，", "对面料确实讲究，", "纹理细节很清晰，"):
        issues = first_message_style_issues("看到您安利的羊绒丝混纺面料，" + verdict + ours)
        assert any("下了评价" in issue for issue in issues), verdict
    # The shop's own words, quoted, are not our verdict; a plain statement of what we saw is fine.
    quoted = "看到贵店写的“这块料子很高级，做工要求很高”那条笔记。这样的料子里面也要配得上，" + ours
    assert first_message_style_issues(quoted) == []


def test_two_evidence_ids_in_one_field_are_accepted() -> None:
    from packages.agent_core.contracts import validate_evidence_refs

    from services.worker.lead_generation import build_agent_input, load_product_facts

    state = {"workflow_run_id": "r1", "shop_name": "某店", "video_text": "炭灰法兰绒单西", "shop_note": "上海手工西装定制"}
    agent_input = build_agent_input(state, load_product_facts(), "dm")
    known = [item.evidence_id for item in agent_input.evidence]
    output = MockProvider().generate(agent_input, build_prompt(agent_input)).output
    joined = output.model_copy(update={"evidence_refs": [",".join(known + known[:1])]})
    assert validate_evidence_refs(agent_input, joined) == []
    assert validate_evidence_refs(agent_input, output.model_copy(update={"evidence_refs": [known[0] + "，e99"]})) == ["e99"]
