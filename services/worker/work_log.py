"""One row per shop for the "work record" table (user 2026-10-08): read-only, built from
what is already stored. Every cell is ready-to-show text, so the page and the exported
file show the same thing."""

from __future__ import annotations

from typing import Any

from packages.agent_core.edits import END_REASONS
from packages.agent_core.outcomes import ASK_TAGS, STAGES

COLUMNS = ["日期", "店名", "小红书号", "先后", "路线", "现在到哪一步", "草稿改过吗", "对方回复", "备注"]
TIER = {"high": "优先看", "medium": "其次", "low": "靠后", "excluded": "不联系"}
CHANNEL = {"dm": "私信", "comment": "评论", "none": "不联系"}
STEP = {
    "route_review": "等你选路线", "content_review": "等你审文案", "scheduled": "待发送",
    "opened": "待确认发出", "sent": "等回复", "replied": "已回复", "read_no_reply": "未回复",
    "failed": "发送异常", "blocked": "已结束",
}
DRAFTED = ("scheduled", "opened", "sent", "replied", "read_no_reply", "failed")


def _edits(state: dict[str, Any], stage: str) -> str:
    parts = []
    if state.get("redraft_count"):
        parts.append(f"自动重写 {state['redraft_count']} 次")
    if state.get("draft_instructions"):
        parts.append(f"让它重写 {len(state['draft_instructions'])} 次")
    if state.get("draft_edits"):
        parts.append(f"手改 {len(state['draft_edits'])} 次")
    return "；".join(parts) or ("没改" if stage in DRAFTED else "—")


def _reply(state: dict[str, Any], stage: str) -> str:
    reply = state.get("reply") or {}
    tags = "、".join(ASK_TAGS[tag] for tag in reply.get("ask_tags", []) if tag in ASK_TAGS)
    if reply.get("follow_up"):
        return tags or "需要跟进"
    if reply.get("replied"):
        return STAGES.get(reply.get("stage", ""), "已回复") + (f"（{tags}）" if tags else "")
    if stage == "read_no_reply":
        return "未回复"
    return "还没有" if stage == "sent" else "—"


def _note(state: dict[str, Any]) -> str:
    reply = state.get("reply") or {}
    end = state.get("end_feedback") or {}
    parts = [
        reply.get("follow_up_note") if reply.get("follow_up") else "",
        "、".join(END_REASONS.get(key, key) for key in end.get("reasons", [])),
        end.get("note") or "",
        "" if end.get("reasons") else state.get("result_note") or "",   # ending a task repeats the reasons there
    ]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return "；".join(seen)


def build_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        state, stage = record["state"], record["stage"]
        reply = state.get("reply") or {}
        follow_up = stage == "sent" and bool(reply.get("follow_up"))
        when = record.get("sent_at") or record["created_at"]
        declined = reply.get("stage") in ("replied_no_intent", "lost") or any(
            tag in ("not_needed", "has_supplier") for tag in reply.get("ask_tags", []))
        rows.append({
            "run_id": record["run_id"],
            "at": when,
            "sent": bool(record.get("sent_at")),
            "stage": stage,
            "effective": bool(reply.get("replied")) and not declined,
            "cells": [
                "",                                   # the page writes the date in local time
                state.get("shop_name") or "",
                state.get("xhs_id") or "",
                TIER.get((state.get("screening_snapshot") or {}).get("tier", ""), "—"),
                CHANNEL.get(state.get("channel") or "", "—"),
                STEP.get(stage, stage) + ("（等你跟进）" if follow_up else ""),
                _edits(state, stage),
                _reply(state, stage),
                _note(state),
            ],
        })
    return sorted(rows, key=lambda row: row["at"], reverse=True)
