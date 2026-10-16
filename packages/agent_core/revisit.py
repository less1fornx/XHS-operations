"""Going back to shops we already contacted (user 2026-10-10).

The program only reminds: when a shop's interval has passed it appears in a list and the
human decides. How soon, how often and in what order depends on how the shop answered
last time; someone who blocked us or asked us to stop never appears.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .outcomes import DECLINE_TAGS, REPLY_WINDOW_DAYS

# order: position in the list; days: shortest wait; limit: how many times we may go back.
CATEGORIES: dict[str, dict[str, Any]] = {
    "interested": {"order": 1, "label": "表示过兴趣", "days": 60, "limit": 2, "method": "私信，接着上次聊的内容；已经在别处谈着的就跳过"},
    "no_reply": {"order": 2, "label": "已读不回或没有回复", "days": 60, "limit": 1, "method": "私信；发不出去就在对方新笔记下评论（不带销售）"},
    "gatekeeper": {"order": 3, "label": "客服或自动回复挡回", "days": 60, "limit": 1, "method": "私信，请对方转给负责人"},
    "refused": {"order": 4, "label": "明确拒绝", "days": 180, "limit": 1, "method": "私信，必须有新的理由；没有就跳过"},
}
INTEREST_STAGES = ("wants_info", "sample", "quotation", "lost")
# The platform lets a second message through only after some unknown wait; try again in a month.
RETRY_DAYS = 30
ACTIONS = ("sent", "cannot_send", "skip", "stop")


def category_of(run_stage: str, state: dict[str, Any]) -> str | None:
    """Which revisit group a sent DM belongs to; None when the shop must not come back."""
    if state.get("channel") != "dm" or run_stage in ("blocked", "failed"):
        return None
    if (state.get("contact_history") or {}).get("do_not_contact"):
        return None
    reply = state.get("reply") or {}
    stage, tags = reply.get("stage"), reply.get("ask_tags") or []
    if stage == "won":
        return None  # a customer now; looked after outside this list
    if stage in INTEREST_STAGES:
        return "interested"
    if any(tag in DECLINE_TAGS for tag in tags):
        return "refused"  # a real "no" outranks the automatic reply that may have come first
    if "auto_reply" in tags or stage == "needs_followup":
        return "gatekeeper"
    if stage == "replied_no_intent":
        return "refused"
    return "no_reply"


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def revisit_rows(records: list[dict[str, Any]], now: datetime) -> dict[str, list[dict[str, Any]]]:
    """One row per shop from its latest sent DM, split into due now and not yet due."""
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if not record.get("sent_at"):
            continue
        state = record["state"]
        key = state.get("lead_id") or (state.get("xhs_id") or state["shop_name"]).casefold()
        if key not in latest or record["sent_at"] > latest[key]["sent_at"]:
            latest[key] = record
    due, upcoming = [], []
    for record in latest.values():
        state = record["state"]
        category = category_of(record["stage"], state)
        revisit = state.get("revisit") or {}
        if category is None or revisit.get("stopped"):
            continue
        rule = CATEGORIES[category]
        count = int(revisit.get("count") or 0)
        if count >= rule["limit"]:
            continue
        reply = state.get("reply") or {}
        sent_at = _parse(record["sent_at"])
        last_contact = max(filter(None, [sent_at, _parse(reply.get("replied_at")), _parse(revisit.get("last_sent_at"))]))
        due_at = _parse(revisit.get("next_due")) or last_contact + timedelta(days=rule["days"])
        row = {
            "run_id": record["run_id"],
            "shop_name": state["shop_name"],
            "xhs_id": state.get("xhs_id") or "",
            "profile_url": state.get("profile_url") or "",
            "category": category,
            "category_label": rule["label"],
            "order": rule["order"],
            "method": rule["method"],
            "sent_at": record["sent_at"],
            "last_contact_at": last_contact.isoformat(),
            "days_since_contact": (now - last_contact).days,
            "waiting_for_reply": category == "no_reply" and record["stage"] == "sent" and (now - sent_at).days < REPLY_WINDOW_DAYS,
            "last_reply": (reply.get("reply_text") or "").strip(),
            "ask_tags": reply.get("ask_tags") or [],
            "revisit_count": count,
            "revisit_limit": rule["limit"],
            "cannot_send_count": sum(1 for entry in revisit.get("log") or [] if entry.get("action") == "cannot_send"),
            "due_at": due_at.isoformat(),
        }
        (due if due_at <= now else upcoming).append(row)
    due.sort(key=lambda row: (row["order"], row["due_at"]))
    upcoming.sort(key=lambda row: (row["due_at"], row["order"]))
    return {"due": due, "upcoming": upcoming}


def apply_action(revisit: dict[str, Any] | None, category: str, action: str, now: datetime, days_since_contact: int) -> dict[str, Any]:
    """What the human decided for one due shop, folded into its revisit record."""
    current = dict(revisit or {})
    rule = CATEGORIES[category]
    entry = {"action": action, "at": now.isoformat(), "days_since_contact": days_since_contact}
    current["log"] = [*current.get("log", []), entry]
    if action == "sent":
        current["count"] = int(current.get("count") or 0) + 1
        current["last_sent_at"] = now.isoformat()
        current["next_due"] = (now + timedelta(days=rule["days"])).isoformat()
    elif action == "cannot_send":
        # Not a revisit: nothing reached the shop. The gap is kept to learn how long the platform makes us wait.
        current["next_due"] = (now + timedelta(days=RETRY_DAYS)).isoformat()
    elif action == "skip":
        current["next_due"] = (now + timedelta(days=rule["days"])).isoformat()
    elif action == "stop":
        current["stopped"] = True
    return current
