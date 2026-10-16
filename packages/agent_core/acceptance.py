"""Acceptance items: plain-language requirements a first message must meet, judged one by one.

User decision 2026-10-05 (project-review/参考调研.md, item A); the items themselves were
written with the user on 2026-10-08. Keyword checks cannot tell whether an opening is
specific to one shop or whether a transition is abrupt, so those items go to a judge
model that has to give a reason. What a program can decide exactly stays in code.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .providers import PROJECT_ROOT, ProviderConfigError, ProviderResponseError
from .style import QUESTION as _QUESTION


JUDGE_PROMPT_VERSION = "acceptance-judge-v1"
JUDGE_PROMPT_PATH = PROJECT_ROOT / "prompts" / "acceptance_judge" / "v1" / "system.md"

AUDIENCE_LABEL = {"store_account": "门店号", "personal_shop": "个人店", "unknown": "未知"}

# “吗” closing a clause is a question even without a question mark.


class AcceptanceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    kind: Literal["program", "judge"]
    # False: judged and reported, but a failure does not fail the draft (not yet confirmed by the user).
    counts: bool = True
    check: Literal["no_ni", "no_question", "max_length"] | None = None
    limit: int | None = None


class AcceptanceItems(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    source: str
    # Things we may say about ourselves beyond config/product_facts; not a fabrication when used.
    extra_our_facts: list[str] = Field(default_factory=list)
    items: list[AcceptanceItem]


class AcceptanceCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    shop_name: str
    audience: str
    prompt_version: str | None = None
    # The workflow fields the drafter saw; enough to draft again for this shop.
    state: dict[str, Any]
    sent_draft: str
    user_opinion: str = ""
    # Item id -> what the user's written opinion implies. Used to check the judge, not the draft.
    user_marks: dict[str, bool] = Field(default_factory=dict)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    reason: str
    passed: bool


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[Verdict]


class Judge(Protocol):
    provider_name: str
    model_name: str

    def judge(self, system: str, user: str) -> JudgeOutput: ...


class QwenJudge:
    provider_name = "qwen"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ProviderConfigError("DASHSCOPE_API_KEY 尚未配置")
        self.model_name = model
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def judge(self, system: str, user: str) -> JudgeOutput:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "acceptance_verdicts",
                    "strict": True,
                    "schema": JudgeOutput.model_json_schema(),
                },
            },
            "enable_thinking": False,
            "temperature": 0,
        }
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions", headers=self._headers, json=payload
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message.content is not text")
            return JudgeOutput.model_validate_json(content)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError(f"裁判返回结果无法通过结构化校验: {exc}") from exc


class MockJudge:
    """Passes everything. For checking the plumbing and the report layout, not the drafts."""

    provider_name = "mock"
    model_name = "pass-all-mock-v1"

    def judge(self, system: str, user: str) -> JudgeOutput:
        ids = re.findall(r"^\[([^\]]+)\]", user.split("【验收条目】")[-1], flags=re.M)
        return JudgeOutput(
            verdicts=[Verdict(item_id=i, reason="模拟裁判，没有真的判定", passed=True) for i in ids]
        )


def judge_from_env(name: str | None = None) -> Judge:
    load_dotenv(PROJECT_ROOT / ".env")
    selected = (name or os.getenv("AGENT_LLM_PROVIDER", "mock")).strip().lower()
    if selected == "mock":
        return MockJudge()
    if selected != "qwen":
        raise ProviderConfigError(f"不支持的 AGENT_LLM_PROVIDER: {selected}")
    return QwenJudge(
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        model=os.getenv("QWEN_MODEL", "qwen3.7-plus-2026-05-26"),
        base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        timeout_seconds=float(os.getenv("QWEN_TIMEOUT_SECONDS", "60")),
    )


def load_items(path: Path) -> AcceptanceItems:
    return AcceptanceItems.model_validate_json(path.read_text(encoding="utf-8"))


def load_cases(path: Path) -> list[AcceptanceCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [AcceptanceCase.model_validate(item) for item in raw["cases"]]


def program_verdict(item: AcceptanceItem, draft: str) -> Verdict:
    text = draft.strip()
    if item.check == "no_ni":
        hit = "你" in text
        reason = "出现了“你”" if hit else "没有出现“你”"
    elif item.check == "no_question":
        found = _QUESTION.search(text)
        hit = found is not None
        reason = f"有问句：…{text[max(0, found.start() - 12):found.end()]}" if found else "没有问句"
    elif item.check == "max_length":
        hit = len(text) > (item.limit or 0)
        reason = f"{len(text)} 字，上限 {item.limit} 字"
    else:
        raise ValueError(f"条目 {item.id} 标为程序判，但没有可用的 check")
    return Verdict(item_id=item.id, reason=reason, passed=not hit)


def build_judge_prompt(
    case: AcceptanceCase, draft: str, items: list[AcceptanceItem], our_facts: list[str]
) -> str:
    state = case.state
    shop = [
        f"店名：{case.shop_name}",
        f"账号类型：{AUDIENCE_LABEL.get(case.audience, '未知')}",
        f"简介与定位：{state.get('shop_note') or '（无）'}",
        f"近期内容：{state.get('video_text') or '（无）'}",
        f"其他记录：{state.get('purchase_signal_note') or '（无）'}",
    ]
    return "\n".join(
        [
            "【这家店的资料】",
            *shop,
            "",
            "【我方可以说的事实】",
            *[f"- {fact}" for fact in our_facts],
            "",
            "【待判定的私信草稿】",
            draft.strip(),
            "",
            "【验收条目】",
            *[f"[{item.id}] {item.text}" for item in items],
        ]
    )


@dataclass(frozen=True)
class AcceptanceReport:
    summary: dict[str, Any]
    cases: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "cases": self.cases}


def judge_draft(
    case: AcceptanceCase, draft: str, items: AcceptanceItems, judge: Judge, our_facts: list[str]
) -> dict[str, Any]:
    """Judge one draft against every item. A failed judge call is recorded, never raised."""
    by_id: dict[str, Verdict] = {
        item.id: program_verdict(item, draft) for item in items.items if item.kind == "program"
    }
    judged = [item for item in items.items if item.kind == "judge"]
    error = None
    try:
        output = judge.judge(
            JUDGE_PROMPT_PATH.read_text(encoding="utf-8"),
            build_judge_prompt(case, draft, judged, [*our_facts, *items.extra_our_facts]),
        )
        # The judge tends to copy the brackets the items are listed with.
        returned = {verdict.item_id.strip().strip("[]【】"): verdict for verdict in output.verdicts}
        missing = [item.id for item in judged if item.id not in returned]
        if missing:
            raise ProviderResponseError(f"裁判漏判了条目：{'、'.join(missing)}")
        by_id.update({item.id: returned[item.id] for item in judged})
    except ProviderResponseError as exc:
        error = str(exc)

    verdicts = [
        {
            "item_id": item.id,
            "kind": item.kind,
            "counts": item.counts,
            "passed": by_id[item.id].passed if item.id in by_id else None,
            "reason": by_id[item.id].reason if item.id in by_id else None,
        }
        for item in items.items
    ]
    failed = [v["item_id"] for v in verdicts if v["counts"] and v["passed"] is False]
    marks = [
        {"item_id": item_id, "user": expected, "judge": by_id[item_id].passed}
        for item_id, expected in case.user_marks.items()
        if item_id in by_id
    ]
    return {
        "case_id": case.case_id,
        "shop_name": case.shop_name,
        "audience": case.audience,
        "draft": draft,
        "verdicts": verdicts,
        # An unjudged item leaves the draft unproven, so it cannot count as passed.
        "passed": error is None and not failed,
        "failed_items": failed,
        "user_marks": marks,
        "error": error,
    }


def summarize(results: list[dict[str, Any]], items: AcceptanceItems, judge: Judge) -> dict[str, Any]:
    per_item = {}
    for item in items.items:
        seen = [v for r in results for v in r["verdicts"] if v["item_id"] == item.id and v["passed"] is not None]
        per_item[item.id] = {
            "passed": sum(1 for v in seen if v["passed"]),
            "judged": len(seen),
            "kind": item.kind,
            "counts": item.counts,
        }
    marks = [{**mark, "shop_name": r["shop_name"]} for r in results for mark in r["user_marks"]]
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0,
        "per_item": per_item,
        "judge_vs_user": {
            "agree": sum(1 for m in marks if m["user"] == m["judge"]),
            "total": len(marks),
            "disagreements": [m for m in marks if m["user"] != m["judge"]],
        },
        "judge_errors": sum(1 for r in results if r["error"]),
        "judge_provider": judge.provider_name,
        "judge_model": judge.model_name,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "items_version": items.version,
    }
