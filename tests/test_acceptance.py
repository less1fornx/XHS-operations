from __future__ import annotations

import re
from pathlib import Path

from packages.agent_core.acceptance import (
    AcceptanceCase,
    AcceptanceItem,
    AcceptanceItems,
    JudgeOutput,
    MockJudge,
    Verdict,
    build_judge_prompt,
    judge_draft,
    load_cases,
    load_items,
    program_verdict,
    summarize,
)
from packages.agent_core.providers import MockProvider, ProviderResponseError
from services.worker.evaluate_acceptance import AcceptanceReport, generate_draft, render
from services.worker.lead_generation import load_product_facts


ROOT = Path(__file__).resolve().parents[1]


def item(item_id: str, kind: str = "judge", **extra) -> AcceptanceItem:
    return AcceptanceItem(id=item_id, text=f"条目 {item_id}", kind=kind, **extra)


def case(**extra) -> AcceptanceCase:
    return AcceptanceCase(
        case_id="REAL-test", shop_name="测试西装店", audience="store_account",
        state={"shop_note": "全手工西服定制", "video_text": "近期笔记标题：G9夹克 轻而有骨"},
        sent_draft="贵店的G9夹克很特别。", **extra,
    )


class FixedJudge:
    provider_name = "fixed"
    model_name = "fixed"

    def __init__(self, verdicts: dict[str, bool]) -> None:
        self.verdicts = verdicts
        self.seen = ""

    def judge(self, system: str, user: str) -> JudgeOutput:
        self.seen = user
        return JudgeOutput(verdicts=[Verdict(item_id=k, reason="理由", passed=v) for k, v in self.verdicts.items()])


def test_program_checks_decide_what_needs_no_model() -> None:
    no_ni = item("3", "program", check="no_ni")
    assert program_verdict(no_ni, "您笔记里那件西装").passed
    assert not program_verdict(no_ni, "你笔记里那件西装").passed

    no_question = item("7a", "program", check="no_question")
    assert not program_verdict(no_question, "更倾向配圆领还是翻领？").passed
    assert not program_verdict(no_question, "有兴趣了解一下吗。").passed
    # A fabric name must not read as a question.
    assert program_verdict(no_question, "贵店的复古花呢夹克很特别，想和您合作内搭。").passed

    length = item("8", "program", check="max_length", limit=10)
    assert program_verdict(length, "一二三四五六七八九十").passed
    assert not program_verdict(length, "一二三四五六七八九十一").passed


def test_unconfirmed_item_is_reported_but_does_not_fail_the_draft() -> None:
    items = AcceptanceItems(version="t", source="t", items=[item("1"), item("9", counts=False)])

    result = judge_draft(case(), "贵店的G9夹克很特别。", items, FixedJudge({"1": True, "9": False}), ["一件起订"])

    assert result["passed"] and result["failed_items"] == []
    assert [v["passed"] for v in result["verdicts"]] == [True, False]

    result = judge_draft(case(), "贵店的G9夹克很特别。", items, FixedJudge({"1": False, "9": True}), [])
    assert not result["passed"] and result["failed_items"] == ["1"]


def test_judge_sees_shop_facts_our_facts_and_only_its_own_items() -> None:
    items = AcceptanceItems(
        version="t", source="t", extra_our_facts=["做高端面料内搭"],
        items=[item("1"), item("3", "program", check="no_ni")],
    )
    judge = FixedJudge({"[1]": True})

    result = judge_draft(case(), "贵店的G9夹克很特别。", items, judge, ["一件起订"])

    # The real judge answers with the brackets it was shown; that must still match.
    assert result["error"] is None and result["passed"]

    assert "G9夹克 轻而有骨" in judge.seen and "- 一件起订" in judge.seen and "- 做高端面料内搭" in judge.seen
    assert "[1] 条目 1" in judge.seen and "[3]" not in judge.seen


def test_a_judge_that_skips_an_item_is_an_error_not_a_pass() -> None:
    items = AcceptanceItems(version="t", source="t", items=[item("1"), item("2")])

    result = judge_draft(case(), "贵店的G9夹克很特别。", items, FixedJudge({"1": True}), [])

    assert not result["passed"] and "2" in result["error"]
    assert [v["passed"] for v in result["verdicts"]] == [None, None]

    class Broken(FixedJudge):
        def judge(self, system: str, user: str) -> JudgeOutput:
            raise ProviderResponseError("超时")

    result = judge_draft(case(), "贵店的G9夹克很特别。", items, Broken({}), [])
    assert not result["passed"] and result["error"] == "超时"


def test_report_compares_the_judge_with_what_the_user_wrote() -> None:
    items = AcceptanceItems(version="t", source="t", items=[item("5"), item("7b")])
    judge = FixedJudge({"5": False, "7b": True})

    results = [judge_draft(case(user_marks={"5": False, "7b": False}), "贵店的G9夹克很特别。", items, judge, [])]
    summary = summarize(results, items, judge)

    assert summary["judge_vs_user"]["agree"] == 1 and summary["judge_vs_user"]["total"] == 2
    assert summary["judge_vs_user"]["disagreements"][0]["item_id"] == "7b"
    assert summary["per_item"]["5"] == {"passed": 0, "judged": 1, "kind": "judge", "counts": True}
    text = render(AcceptanceReport(summary=summary, cases=results), {"5": "条目 5", "7b": "条目 7b"})
    assert "一致 1/2" in text and "测试西装店 [7b] 你的意见是不过，裁判判过" in text


def test_real_items_and_cases_load_and_run_end_to_end_with_mocks() -> None:
    items = load_items(ROOT / "evals" / "acceptance_items.v1.json")
    cases = load_cases(ROOT / "evals" / "acceptance_cases.v1.json")
    assert len(cases) == 8 and all(c.sent_draft and c.state.get("video_text") for c in cases)
    # Shops are identified by what they publish, never by how to reach them.
    assert all(not re.search(r"@[\w-]+\.\w+", str(c.state)) and "xhs_id" not in c.state for c in cases)
    judged_ids = {i.id for i in items.items}
    assert all(set(c.user_marks) <= judged_ids for c in cases)

    facts = load_product_facts()
    first = judge_draft(cases[0], cases[0].sent_draft, items, MockJudge(), facts)
    assert first["error"] is None and all(v["passed"] is not None for v in first["verdicts"])
    # The sent draft uses “你” and ends on a question; the program items catch both without a model.
    assert "3" in first["failed_items"]
    assert {v["item_id"]: v["passed"] for v in first["verdicts"]}["7a"] is False

    generated = generate_draft(cases[1], MockProvider(), facts)
    assert generated["draft"] and generated["rewrites"] <= 2
    assert "【验收条目】" in build_judge_prompt(cases[1], generated["draft"], items.items, facts)
