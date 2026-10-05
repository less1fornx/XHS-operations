from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from packages.agent_core.edits import EDIT_REASONS, END_REASONS


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
        end_reasons: dict[str, int] = {}
        edit_reasons: dict[str, int] = {}
        for review_type, raw in conn.execute(
            "select review_type, reason from human_reviews where review_type in ('end','content_edit') and reason like '{%'"
        ):
            for key in json.loads(raw).get("reasons") or []:
                bucket = end_reasons if review_type == "end" else edit_reasons
                bucket[key] = bucket.get(key, 0) + 1
        has_notes = one("select count(*) from sqlite_master where type='table' and name='feedback_notes'")
        open_notes = [
            {"area": area, "text": text, "created_at": created_at}
            for area, text, created_at in (
                conn.execute(
                    "select area, text, created_at from feedback_notes where status='open' order by created_at"
                ) if has_notes else []
            )
        ]
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
        "open_notes": open_notes,
        "end_reasons": end_reasons,
        "edit_reasons": edit_reasons,
    }


FEEDBACK_AREAS = {
    "read": "读店（读图读错、读漏）",
    "tier": "分档（高中低判得不对）",
    "draft": "草稿（文案问题）",
    "send": "发送（打开页面、搜索、复制）",
    "reply": "记录回复",
    "page": "页面不好用、看不懂",
    "other": "其他",
}


def stage_rows(stats: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per stage: what counts, where the user gives that feedback, and how far along it is."""
    read, tier, draft = stats["read"], stats["tier"], stats["draft"]
    send, reply, evals = stats["send"], stats["reply"], stats["eval"]

    def rated(key: str, rate: float | None, sample: int) -> dict[str, Any]:
        need, bar = TARGETS[key]
        state = "collecting" if sample < need else ("met" if (rate or 0) >= bar else "not_met")
        return {"sample": sample, "need": need, "rate": rate, "bar": bar, "state": state}

    def counted(done: int, need: int) -> dict[str, Any]:
        return {"sample": done, "need": need, "rate": None, "bar": None,
                "state": "met" if done >= need else "collecting"}

    return [
        {"key": "read", "label": "读店", "measure": "小红书号识别到的比例",
         "how": "批量初筛的结果卡片上：核对小红书号，认错的店铺类型直接改。", "view": "screening",
         "detail": f"读成 {read['shops_read']} 家，失败 {read['shops_failed']} 家，你改正 {read['corrected']} 次",
         **rated("xhs_id_rate", read["xhs_id_rate"], read["shops_read"])},
        {"key": "tier", "label": "分档", "measure": "你同意系统分档的比例（参考项，现阶段重点是起草）",
         "how": "批量初筛的结果卡片上：每家店点“同意”或“不同意”，不同意时写一句理由。", "view": "screening",
         "detail": f"同意 {tier['agree']} 家，不同意 {tier['disagree']} 家",
         **rated("tier_agree_rate", tier["tier_agree_rate"], tier["marked"])},
        {"key": "draft", "label": "起草", "measure": "不用改就能发的草稿比例",
         "how": "候选工作流里审文案时：不满意就直接改，改完选修改原因。", "view": "workflow",
         "detail": f"通过 {draft['approved']} 条，没改 {draft['unedited']} 条，通过后又改 {draft['later_edits']} 次",
         **rated("draft_unedited_rate", draft["draft_unedited_rate"], draft["approved"])},
        {"key": "send", "label": "发送", "measure": "真实发送的家数",
         "how": "候选工作流里点“通过并现在去发”，在小红书发完回来点“已发送成功”。", "view": "workflow",
         "detail": f"已发送 {send['sent']} 家", **counted(send["sent"], MIN_SENT)},
        {"key": "reply", "label": "记录回复", "measure": "有结果记录的家数",
         "how": "候选工作流的“等回复”分组里：对方回复后上传私信截图或粘贴原话，选阶段。", "view": "workflow",
         "detail": f"有回复 {reply['replied']} 家，15 天未回复 {reply['no_reply']} 家",
         **counted(reply["replied"] + reply["no_reply"], MIN_SENT)},
        {"key": "eval", "label": "评测集", "measure": "真正考模型的题数",
         "how": "攒够真实店铺后，由你挑有代表性的，告诉 Claude 每家的预期。", "view": None,
         "detail": f"共 {evals['cases']} 题", **counted(evals["model_cases"], MIN_EVAL_MODEL_CASES)},
    ]


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
    def _named(counts: dict[str, int], names: dict[str, str]) -> str:
        return "、".join(f"{names.get(k, k)} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "还没有记录"

    lines += [
        f"不联系的原因：{_named(stats.get('end_reasons') or {}, END_REASONS)}",
        f"改草稿的原因：{_named(stats.get('edit_reasons') or {}, EDIT_REASONS)}",
    ]
    notes = stats.get("open_notes") or []
    lines += ["", f"用户留下的未处理意见：{len(notes)} 条"]
    lines += [f"  [{FEEDBACK_AREAS.get(n['area'], n['area'])}] {n['text']}" for n in notes]
    return "\n".join(lines)


def main() -> None:
    db_path = Path(os.getenv("AGENT_RUN_DB_PATH", str(DEFAULT_DB)))
    print(render(collect(db_path)))


if __name__ == "__main__":
    main()
