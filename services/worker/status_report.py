from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "agent_ops.db"
EVAL_CASES = PROJECT_ROOT / "evals" / "phase2_cases.v1.json"

# Minimum sample size before a rate means anything, and the bar it has to clear.
# First version proposed by Claude; the user owns these numbers (docs/STATUS.md).
TARGETS: dict[str, tuple[int, float]] = {
    "xhs_id_rate": (30, 0.90),
    "tier_agree_rate": (30, 0.80),
    "draft_unedited_rate": (20, 0.50),
}
MIN_SENT = 20
MIN_EVAL_MODEL_CASES = 15


def _rate(hit: int, total: int) -> float | None:
    return round(hit / total, 3) if total else None


def collect(db_path: Path = DEFAULT_DB, eval_cases: Path = EVAL_CASES) -> dict[str, Any]:
    """Count what has really happened, per stage, straight from the database."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731

        shops_read = shops_failed = with_xhs_id = corrected = 0
        tier_agree = tier_disagree = 0
        for (payload,) in conn.execute("select payload_json from screening_batches"):
            batch = json.loads(payload)
            for result in batch.get("results") or []:
                extraction = result.get("extraction")
                if not extraction:
                    shops_failed += 1
                    continue
                shops_read += 1
                with_xhs_id += bool(extraction.get("xhs_id"))
            corrected += len(batch.get("corrections") or [])
            for decision in (batch.get("review") or {}).get("decisions") or []:
                tier_agree += decision.get("tier_feedback") == "agree"
                tier_disagree += decision.get("tier_feedback") == "disagree"

        drafts_approved = one(
            "select count(*) from human_reviews where review_type='content' and decision='approve'"
        )
        drafts_unedited = one(
            "select count(*) from human_reviews where review_type='content' and decision='approve' "
            "and coalesce(before_text,'') = coalesce(after_text,'')"
        )
        later_edits = one("select count(*) from human_reviews where review_type='content_edit'")
        sent = one("select count(*) from contact_snapshots")
        sent_before_snapshots = one(
            "select count(*) from human_reviews where review_type='platform_result' and decision='confirm'"
        )
        replied = one("select count(*) from outreach_outcomes where reply_timing in ('normal','late')")
        no_reply = one("select count(*) from outreach_outcomes where stage='no_reply'")
        model_calls = dict(conn.execute("select status, count(*) from model_invocations group by 1"))
    finally:
        conn.close()

    cases = json.loads(eval_cases.read_text(encoding="utf-8")) if eval_cases.exists() else []
    cases = cases.get("cases", []) if isinstance(cases, dict) else cases
    no_contact = {"declined", "read_no_reply", "blocked"}
    eval_model_cases = sum(
        1 for case in cases if (case.get("input") or case).get("prior_outcome") not in no_contact
    )

    tier_marked = tier_agree + tier_disagree
    return {
        "read": {"shops_read": shops_read, "shops_failed": shops_failed, "with_xhs_id": with_xhs_id,
                 "corrected": corrected, "xhs_id_rate": _rate(with_xhs_id, shops_read)},
        "tier": {"marked": tier_marked, "agree": tier_agree, "disagree": tier_disagree,
                 "tier_agree_rate": _rate(tier_agree, tier_marked)},
        "draft": {"approved": drafts_approved, "unedited": drafts_unedited, "later_edits": later_edits,
                  "draft_unedited_rate": _rate(drafts_unedited, drafts_approved)},
        "send": {"sent": sent, "sent_before_snapshots": max(sent_before_snapshots - sent, 0)},
        "reply": {"replied": replied, "no_reply": no_reply, "reply_rate": _rate(replied, replied + no_reply)},
        "eval": {"cases": len(cases), "model_cases": eval_model_cases},
        "model_calls": model_calls,
    }


def _verdict(key: str, rate: float | None, sample: int) -> str:
    need, bar = TARGETS[key]
    if sample < need:
        return f"样本不足（{sample}/{need}），还不能下结论"
    assert rate is not None
    return f"{'达标' if rate >= bar else '未达标'}（标准 {bar:.0%}）"


def _pct(rate: float | None) -> str:
    return "—" if rate is None else f"{rate:.0%}"


def render(stats: dict[str, Any]) -> str:
    read, tier, draft = stats["read"], stats["tier"], stats["draft"]
    send, reply, evals = stats["send"], stats["reply"], stats["eval"]
    lines = [
        "项目进度（数字来自真实数据库）",
        "",
        f"① 读店　　读成 {read['shops_read']} 家，失败 {read['shops_failed']} 家，人工改正 {read['corrected']} 次",
        f"　　　　　识别到小红书号 {read['with_xhs_id']} 家（{_pct(read['xhs_id_rate'])}）"
        f" → {_verdict('xhs_id_rate', read['xhs_id_rate'], read['shops_read'])}",
        f"② 分档　　已标注 {tier['marked']} 家：同意 {tier['agree']}，不同意 {tier['disagree']}（一致率 {_pct(tier['tier_agree_rate'])}）"
        f" → {_verdict('tier_agree_rate', tier['tier_agree_rate'], tier['marked'])}",
        f"③ 起草　　审核通过 {draft['approved']} 条，其中没改就通过 {draft['unedited']} 条（{_pct(draft['draft_unedited_rate'])}），通过后又改 {draft['later_edits']} 次"
        f" → {_verdict('draft_unedited_rate', draft['draft_unedited_rate'], draft['approved'])}",
        f"④ 发送　　已发送 {send['sent']} 家（目标：先到 {MIN_SENT} 家）"
        + (f"；另有 {send['sent_before_snapshots']} 家发送于快照功能之前，不计入分析" if send["sent_before_snapshots"] else ""),
        f"⑤ 回复　　有回复 {reply['replied']} 家，15 天未回复 {reply['no_reply']} 家（回复率 {_pct(reply['reply_rate'])}）",
        f"⑥ 评测集　共 {evals['cases']} 题，其中真正考模型的 {evals['model_cases']} 题（目标：{MIN_EVAL_MODEL_CASES} 题）",
        "",
        f"模型调用累计：{json.dumps(stats['model_calls'], ensure_ascii=False)}",
    ]
    return "\n".join(lines)


def main() -> None:
    db_path = Path(os.getenv("AGENT_RUN_DB_PATH", str(DEFAULT_DB)))
    print(render(collect(db_path)))


if __name__ == "__main__":
    main()
