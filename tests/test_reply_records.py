from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, select

from packages.agent_core.outcomes import (
    keeps_do_not_contact,
    reply_timing,
    scrub_contact_info,
    wants_product_info,
)
from services.api.app.models import ContactSnapshot, OutreachOutcome, ShopLead
from services.api.app.storage import BusinessRuleError, RunStore


NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def test_reply_within_three_days_is_normal_then_late_until_fifteen() -> None:
    assert reply_timing(NOW, NOW + timedelta(days=3)) == "normal"
    assert reply_timing(NOW, NOW + timedelta(days=3, hours=1)) == "late"
    assert reply_timing(NOW, NOW + timedelta(days=15)) == "late"
    assert reply_timing(NOW, NOW + timedelta(days=16)) == "after_window"


def test_product_interest_is_derived_from_what_the_shop_asked() -> None:
    assert wants_product_info(["color_card"])
    assert wants_product_info(["visit", "price"])
    assert not wants_product_info(["visit", "handoff"])


def test_do_not_contact_follows_reply_and_stage() -> None:
    assert keeps_do_not_contact(False, "no_reply", [])
    assert keeps_do_not_contact(True, "replied_no_intent", [])
    assert keeps_do_not_contact(True, "wants_info", ["not_needed"])
    assert not keeps_do_not_contact(True, "wants_info", ["catalog"])


def test_phone_numbers_are_removed_from_reply_text() -> None:
    cleaned = scrub_contact_info("你留个电话 13800001111 我让同事加你")
    assert "13800001111" not in cleaned and "同事" in cleaned


def make_store(tmp_path) -> RunStore:
    store = RunStore(tmp_path / "test.db")
    SQLModel.metadata.create_all(store.engine)
    return store


def sent_run(store: RunStore, run_id: str = "run-1") -> dict:
    state = {
        "lead_id": f"lead-{run_id}",
        "evidence_id": f"evidence-{run_id}",
        "task_id": f"task-{run_id}",
        "shop_name": "示例定制",
        "video_text": "示例内容",
        "channel": "dm",
        "draft": "最终发出的文案",
        "model_draft": "模型原稿",
        "shop_type": "suit_custom",
        "task_status": "sent",
        "model_meta": {"prompt_version": "lead-outreach-v4", "model": "mock"},
        "screening_snapshot": {
            "tier": "high",
            "rules_version": "screening-rules-v0",
            "signals": {"B_fabric": {"value": "yes", "evidence": ["Zegna"]}},
            "content_angles": ["先聊纱线"],
        },
        "trace": [],
    }
    store.save(run_id, {"id": run_id, "stage": "sent", "interrupt": None, "state": state}, "record_result_sent")
    store.save_contact_snapshot(run_id, state)
    return state


def record(store: RunStore, run_id: str = "run-1", **overrides):
    fields = dict(
        replied=True, stage="wants_info", ask_tags=["catalog", "price"], reply_text="发点资料看看",
        replied_at=None, note=None, keep_do_not_contact=False, timing_fn=reply_timing,
    )
    return store.record_reply(run_id, **{**fields, **overrides})


def test_contact_snapshot_freezes_tier_versions_and_final_text(tmp_path) -> None:
    store = make_store(tmp_path)
    state = sent_run(store)
    state["draft"] = "后来改过的文案"
    store.save_contact_snapshot("run-1", state)  # second call must not overwrite

    with Session(store.engine) as session:
        snapshot = session.exec(select(ContactSnapshot)).one()
    assert snapshot.tier == "high"
    assert snapshot.final_text == "最终发出的文案"
    assert json.loads(snapshot.versions_json) == {
        "screening_rules": "screening-rules-v0", "prompt": "lead-outreach-v4", "model": "mock",
    }
    assert json.loads(snapshot.features_json)["draft_edited_by_human"] is True


def test_reply_is_recorded_with_tags_timing_and_stage(tmp_path) -> None:
    store = make_store(tmp_path)
    sent_run(store)

    payload = record(store)

    assert payload["stage"] == "replied"
    assert payload["state"]["reply"]["reply_timing"] == "normal"
    with Session(store.engine) as session:
        outcome = session.exec(select(OutreachOutcome)).one()
        lead = session.get(ShopLead, "lead-run-1")
    assert outcome.stage == "wants_info"
    assert json.loads(outcome.ask_tags_json) == ["catalog", "price"]
    assert lead.do_not_contact is False


def test_later_update_moves_stage_but_keeps_first_reply_time(tmp_path) -> None:
    store = make_store(tmp_path)
    sent_run(store)
    first = record(store)["state"]["reply"]["replied_at"]

    later = record(store, stage="sample", replied_at=datetime.now(timezone.utc) + timedelta(days=5))

    assert later["state"]["reply"]["stage"] == "sample"
    assert later["state"]["reply"]["replied_at"] == first


def test_unanswered_after_window_is_closed_and_late_reply_reopens(tmp_path) -> None:
    store = make_store(tmp_path)
    sent_run(store)
    with Session(store.engine) as session:
        snapshot = session.exec(select(ContactSnapshot)).one()
        snapshot.sent_at = datetime.now(timezone.utc) - timedelta(days=16)
        session.commit()

    assert store.unanswered_runs(15) == ["run-1"]
    closed = record(store, replied=False, stage="no_reply", ask_tags=[], reply_text=None,
                    keep_do_not_contact=True, system=True)
    assert closed["stage"] == "read_no_reply"
    assert store.unanswered_runs(15) == []
    with Session(store.engine) as session:
        assert session.get(ShopLead, "lead-run-1").do_not_contact is True

    reopened = record(store)
    assert reopened["stage"] == "replied"
    assert reopened["state"]["reply"]["reply_timing"] == "after_window"
    with Session(store.engine) as session:
        assert session.get(ShopLead, "lead-run-1").do_not_contact is False


def test_reply_cannot_be_recorded_before_sending(tmp_path) -> None:
    store = make_store(tmp_path)
    store.save("run-2", {"id": "run-2", "stage": "scheduled", "interrupt": None, "state": {
        "lead_id": "l", "evidence_id": "e", "task_id": "t", "shop_name": "x", "video_text": "y",
        "channel": "dm", "task_status": "scheduled", "trace": []}}, "content_reviewed")
    with pytest.raises(BusinessRuleError):
        record(store, "run-2")


def test_reply_records_join_snapshot_and_outcome(tmp_path) -> None:
    store = make_store(tmp_path)
    sent_run(store)
    record(store)

    rows = store.list_reply_records()

    assert rows[0]["tier"] == "high"
    assert rows[0]["outcome"]["stage"] == "wants_info"


def test_follow_up_keeps_waiting_and_is_not_counted_as_a_reply(tmp_path) -> None:
    store = make_store(tmp_path)
    sent_run(store)
    run_id = "run-1"
    payload = record(store, replied=False, stage="needs_followup", ask_tags=["auto_reply"],
                     reply_text="您好，欢迎咨询", note="对方是自动回复，明天再发一条",
                     keep_do_not_contact=False, follow_up=True)
    assert payload["stage"] == "sent"                      # still waiting; the 15-day rule keeps running
    reply = payload["state"]["reply"]
    assert reply["follow_up"] and not reply["replied"] and reply["follow_up_note"]
    assert run_id in store.unanswered_runs(days=0)
    # A real reply later replaces it and counts from then on.
    later = record(store, stage="wants_info", ask_tags=["price"])
    assert later["stage"] == "replied" and not later["state"]["reply"]["follow_up"]
