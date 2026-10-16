from __future__ import annotations

from datetime import datetime, timedelta, timezone

from packages.agent_core.revisit import apply_action, category_of, revisit_rows

NOW = datetime(2026, 12, 20, tzinfo=timezone.utc)


def record(name: str, days_ago: int, stage: str = "sent", reply: dict | None = None, **state) -> dict:
    sent = (NOW - timedelta(days=days_ago)).isoformat()
    return {"run_id": name, "stage": stage, "sent_at": sent,
            "state": {"shop_name": name, "lead_id": name, "channel": "dm", **({"reply": reply} if reply else {}), **state}}


def test_shops_are_grouped_by_how_they_answered() -> None:
    assert category_of("replied", {"channel": "dm", "reply": {"stage": "wants_info", "ask_tags": ["moq"]}}) == "interested"
    assert category_of("read_no_reply", {"channel": "dm"}) == "no_reply"
    assert category_of("sent", {"channel": "dm"}) == "no_reply"
    assert category_of("replied", {"channel": "dm", "reply": {"stage": "replied_no_intent", "ask_tags": ["auto_reply"]}}) == "gatekeeper"
    assert category_of("replied", {"channel": "dm", "reply": {"stage": "replied_no_intent", "ask_tags": ["not_needed"]}}) == "refused"
    # An automatic reply followed by a real "no" is a refusal.
    assert category_of("replied", {"channel": "dm", "reply": {"stage": "replied_no_intent", "ask_tags": ["auto_reply", "not_needed"]}}) == "refused"
    # Never: blocked, asked us to stop, a comment, or already a customer.
    assert category_of("blocked", {"channel": "dm"}) is None
    assert category_of("sent", {"channel": "dm", "contact_history": {"do_not_contact": True}}) is None
    assert category_of("sent", {"channel": "comment"}) is None
    assert category_of("replied", {"channel": "dm", "reply": {"stage": "won"}}) is None


def test_due_list_follows_the_agreed_order_and_intervals() -> None:
    rows = revisit_rows([
        record("拒绝-70天", 70, "replied", {"stage": "replied_no_intent", "ask_tags": ["not_needed"]}),
        record("拒绝-190天", 190, "replied", {"stage": "replied_no_intent", "ask_tags": ["not_needed"]}),
        record("不回-70天", 70, "read_no_reply"),
        record("客服-70天", 70, "replied", {"stage": "replied_no_intent", "ask_tags": ["auto_reply"]}),
        record("兴趣-65天", 65, "replied", {"stage": "wants_info", "ask_tags": ["moq"]}),
        record("不回-10天", 10),
        record("没发出去", 90) | {"sent_at": None},
    ], NOW)
    # Interest first, then silence, then the gatekeeper, refusals last; a refusal waits half a year.
    assert [row["shop_name"] for row in rows["due"]] == ["兴趣-65天", "不回-70天", "客服-70天", "拒绝-190天"]
    assert [row["shop_name"] for row in rows["upcoming"]] == ["不回-10天", "拒绝-70天"]
    assert rows["upcoming"][0]["waiting_for_reply"] is True


def test_decisions_move_or_remove_the_shop() -> None:
    base = record("不回", 70, "read_no_reply")
    skipped = {**base, "state": {**base["state"], "revisit": apply_action(None, "no_reply", "skip", NOW, 70)}}
    assert revisit_rows([skipped], NOW)["due"] == [] and len(revisit_rows([skipped], NOW)["upcoming"]) == 1
    assert revisit_rows([skipped], NOW + timedelta(days=61))["due"][0]["shop_name"] == "不回"

    stopped = {**base, "state": {**base["state"], "revisit": apply_action(None, "no_reply", "stop", NOW, 70)}}
    assert revisit_rows([stopped], NOW + timedelta(days=400)) == {"due": [], "upcoming": []}

    # Silence may be revisited once; interest twice. After the limit the shop never comes back.
    once = {**base, "state": {**base["state"], "revisit": apply_action(None, "no_reply", "sent", NOW, 70)}}
    assert revisit_rows([once], NOW + timedelta(days=400)) == {"due": [], "upcoming": []}
    keen = record("兴趣", 65, "replied", {"stage": "wants_info"})
    first = apply_action(None, "interested", "sent", NOW, 65)
    again = revisit_rows([{**keen, "state": {**keen["state"], "revisit": first}}], NOW + timedelta(days=61))
    assert again["due"][0]["revisit_count"] == 1
    second = apply_action(first, "interested", "sent", NOW + timedelta(days=61), 61)
    assert revisit_rows([{**keen, "state": {**keen["state"], "revisit": second}}], NOW + timedelta(days=400)) == {"due": [], "upcoming": []}


def test_a_message_that_would_not_send_is_not_counted_and_keeps_the_gap() -> None:
    base = record("不回", 70, "read_no_reply")
    failed = apply_action(None, "no_reply", "cannot_send", NOW, 70)
    assert failed.get("count") is None and failed["log"][0]["days_since_contact"] == 70
    row = {**base, "state": {**base["state"], "revisit": failed}}
    assert revisit_rows([row], NOW + timedelta(days=29))["due"] == []
    back = revisit_rows([row], NOW + timedelta(days=31))["due"][0]
    assert back["revisit_count"] == 0 and back["cannot_send_count"] == 1


def test_only_the_latest_message_to_a_shop_counts() -> None:
    old = record("同一家", 200, "read_no_reply")
    new = {**record("同一家-新", 5), "state": {**record("同一家-新", 5)["state"], "lead_id": "同一家"}}
    rows = revisit_rows([old, new], NOW)
    assert rows["due"] == [] and [row["run_id"] for row in rows["upcoming"]] == ["同一家-新"]
