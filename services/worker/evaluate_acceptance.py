from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from packages.agent_core.acceptance import (
    AcceptanceCase,
    AcceptanceReport,
    judge_draft,
    judge_from_env,
    load_cases,
    load_items,
    summarize,
)
from packages.agent_core.pipeline import generate_lead_suggestion
from packages.agent_core.providers import (
    LLMProvider,
    ProviderConfigError,
    ProviderResponseError,
    provider_from_env,
)
from packages.agent_core.style import first_message_style_issues
from packages.workflows.lead_workflow import MAX_REDRAFTS
from services.worker.lead_generation import audience_for, build_agent_input, load_product_facts, positioning_of, strengths_for


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "agent_ops.db"
DEFAULT_ITEMS = PROJECT_ROOT / "evals" / "acceptance_items.v1.json"
DEFAULT_CASES = PROJECT_ROOT / "evals" / "acceptance_cases.v1.json"
# What the judge accepts as true about us; the same facts the drafter reads.
JUDGE_FACTS = PROJECT_ROOT / "config" / "product_facts.v7.json"

# What the drafter reads from the workflow state; nothing that identifies how to reach the shop.
STATE_FIELDS = (
    "shop_type",
    "shop_note",
    "video_text",
    "activity_note",
    "purchase_signal_note",
    "contact_basis",
    "contact_basis_note",
    "model_summary",
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def export_cases(db_path: Path, run_ids: list[str]) -> list[dict[str, Any]]:
    """Turn real runs into cases. The user's opinion and marks are filled in by hand afterwards."""
    conn = sqlite3.connect(db_path)
    try:
        cases = []
        for run_id in run_ids:
            row = conn.execute(
                "select payload_json from run_snapshots where id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise SystemExit(f"找不到这次运行：{run_id}")
            state = json.loads(row[0])["state"]
            kept = {
                key: _EMAIL.sub("（邮箱略）", value) if isinstance(value, str) else value
                for key in STATE_FIELDS
                if (value := state.get(key))
            }
            cases.append(
                AcceptanceCase(
                    case_id=f"REAL-{run_id[:8]}",
                    shop_name=state["shop_name"],
                    audience=audience_for(state),
                    prompt_version=(state.get("model_meta") or {}).get("prompt_version"),
                    state=kept,
                    sent_draft=state.get("draft") or "",
                ).model_dump(mode="json")
            )
        return cases
    finally:
        conn.close()


def generate_draft(case: AcceptanceCase, provider: LLMProvider, product_facts: list[str]) -> dict[str, Any]:
    """Draft the way the product does: first draft, then up to MAX_REDRAFTS rewrites on check failures."""
    state = {**case.state, "shop_name": case.shop_name, "audience": case.audience, "workflow_run_id": case.case_id}
    result = generate_lead_suggestion(build_agent_input(state, product_facts, "dm"), provider)
    draft, rewrites, strengths = result.output.draft, 0, strengths_for(state)
    while rewrites < MAX_REDRAFTS:
        issues = first_message_style_issues(draft, case.audience, strengths, positioning_of(state))
        if not issues:
            break
        revision = {"previous_draft": draft, "issues": issues}
        result = generate_lead_suggestion(build_agent_input(state, product_facts, "dm", revision), provider)
        draft, rewrites = result.output.draft or draft, rewrites + 1
    return {"draft": draft, "rewrites": rewrites, "positioning": positioning_of(state), "strengths": strengths, "prompt_version": result.prompt_version, "model": result.model}


def render(report: AcceptanceReport, item_texts: dict[str, str]) -> str:
    """The report as plain text: one row per shop, one column per item."""
    summary = report.summary
    ids = list(summary["per_item"])
    mark = {True: "过", False: "不过", None: "没判"}
    lines = ["店铺 | " + " | ".join(ids) + " | 结论"]
    if summary["judge_provider"] == "mock":
        lines.insert(0, "模拟裁判：模型判的条目一律显示“过”，只用来看格式，不是真实结果。\n")
    for case in report.cases:
        cells = [mark[v["passed"]] for v in case["verdicts"]]
        verdict = "裁判出错" if case["error"] else ("通过" if case["passed"] else "不通过")
        lines.append(f"{case['shop_name']} | " + " | ".join(cells) + f" | {verdict}")
    lines += ["", "各条目通过家数："]
    for item_id, stat in summary["per_item"].items():
        who = "程序判" if stat["kind"] == "program" else "模型判"
        note = "" if stat["counts"] else "，不计入通过"
        lines.append(f"  [{item_id}] {stat['passed']}/{stat['judged']}（{who}{note}）{item_texts[item_id][:28]}")
    lines += ["", "模型判为不通过的理由："]
    for case in report.cases:
        for v in case["verdicts"]:
            if v["kind"] == "judge" and v["passed"] is False:
                lines.append(f"  {case['shop_name']} [{v['item_id']}] {v['reason']}")
        if case["error"]:
            lines.append(f"  {case['shop_name']} 裁判出错：{case['error']}")
    check = summary["judge_vs_user"]
    lines += ["", f"裁判和你写的意见对照：一致 {check['agree']}/{check['total']}"]
    for miss in check["disagreements"]:
        lines.append(
            f"  不一致：{miss['shop_name']} [{miss['item_id']}] 你的意见是{mark[miss['user']]}，裁判判{mark[miss['judge']]}"
        )
    lines += ["", f"计入通过的条目全部通过：{summary['passed']}/{summary['total']} 家"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="按验收条目评测首条私信草稿")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--items", type=Path, default=DEFAULT_ITEMS)
    parser.add_argument("--provider", choices=("mock", "qwen"), default=None)
    parser.add_argument("--generate", action="store_true", help="用现行起草规则重新生成草稿再判；不加则判已发出的草稿")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--export", nargs="+", metavar="RUN_ID", help="把真实运行导出成案例，打印后退出")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    if args.export:
        print(json.dumps(export_cases(args.db, args.export), ensure_ascii=False, indent=2))
        return

    try:
        judge = judge_from_env(args.provider)
        provider = provider_from_env(args.provider) if args.generate else None
    except ProviderConfigError as exc:
        parser.exit(2, f"配置错误: {exc}\n请复制 .env.example 为 .env，并只在本机填写密钥。\n")
    items = load_items(args.items)
    product_facts = load_product_facts()
    judge_facts = load_product_facts(JUDGE_FACTS)
    results = []
    for case in load_cases(args.cases):
        generation = None
        if provider is not None:
            try:
                generation = generate_draft(case, provider, product_facts)
            except ProviderResponseError as exc:
                results.append({
                    "case_id": case.case_id, "shop_name": case.shop_name, "audience": case.audience,
                    "draft": None, "verdicts": [
                        {"item_id": i.id, "kind": i.kind, "counts": i.counts, "passed": None, "reason": None}
                        for i in items.items
                    ],
                    "passed": False, "failed_items": [], "user_marks": [], "error": f"起草失败：{exc}",
                })
                continue
        result = judge_draft(case, generation["draft"] if generation else case.sent_draft, items, judge, judge_facts)
        if generation:
            # The user's marks describe the sent draft, not a new one.
            result["user_marks"] = []
            result["generation"] = {k: v for k, v in generation.items() if k != "draft"}
        results.append(result)

    summary = {"draft_source": "generated" if args.generate else "sent", **summarize(results, items, judge)}
    report = AcceptanceReport(summary=summary, cases=results)
    output = args.output or (
        PROJECT_ROOT
        / "outputs"
        / f"acceptance-eval-{summary['draft_source']}-{judge.provider_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(render(report, {item.id: item.text for item in items.items}))
    print(f"\n完整结果（含每条理由）：{output}")


if __name__ == "__main__":
    main()
