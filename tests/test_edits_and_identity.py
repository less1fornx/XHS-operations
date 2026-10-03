from __future__ import annotations

import json

from sqlmodel import Session, SQLModel, select

from packages.agent_core.edits import describe_edit, diff_ops
from packages.agent_core.screening import ShopExtraction, normalize_xhs_id
from services.api.app.models import ContactSnapshot, HumanReview, ShopLead
from services.api.app.storage import DEFAULT_ACCOUNT_ID, RunStore
from services.worker.screening_graph import rank_results
from datetime import date


def make_store(tmp_path) -> RunStore:
    store = RunStore(tmp_path / "test.db")
    SQLModel.metadata.create_all(store.engine)
    return store


def test_diff_reports_only_the_changed_spans() -> None:
    ops = diff_ops("我们支持一件起订，欢迎了解。", "我们支持一件起订，可按尺寸定制。")
    assert ops == [{"op": "replace", "before": "欢迎了解", "after": "可按尺寸定制"}]


def test_edit_keeps_reasons_and_drops_unknown_ones() -> None:
    edit = describe_edit("很长的原稿", "短稿", ["too_long", "made_up"], " 删掉了客套话 ", "content_review", "t")
    assert edit["reasons"] == ["too_long"]
    assert edit["note"] == "删掉了客套话"
    assert edit["removed_chars"] > 0


def test_xhs_id_is_normalized() -> None:
    assert normalize_xhs_id("小红书号：Hlin97 ") == "Hlin97"
    assert normalize_xhs_id(None) == ""


def add_lead(store: RunStore, lead_id: str, name: str, handle: str | None, dnc: bool = False) -> None:
    with Session(store.engine) as session:
        session.add(ShopLead(id=lead_id, platform_account_id=DEFAULT_ACCOUNT_ID, display_name=name,
                             handle=handle, do_not_contact=dnc))
        session.commit()


def test_id_decides_identity_when_both_sides_have_one(tmp_path) -> None:
    store = make_store(tmp_path)
    add_lead(store, "lead-a", "西装定制", "111", dnc=True)

    # Same display name, different account: a different shop, not blocked.
    assert store.find_lead_id("西装定制", None, "222") is None
    assert store.is_do_not_contact("西装定制", None, "222") is False
    # Renamed account, same id: the same shop, still blocked.
    assert store.find_lead_id("换了名字的店", None, "111") == "lead-a"
    assert store.is_do_not_contact("换了名字的店", None, "111") is True


def test_name_is_the_fallback_when_an_id_is_missing(tmp_path) -> None:
    store = make_store(tmp_path)
    add_lead(store, "lead-old", "老记录没有号", None, dnc=True)
    assert store.is_do_not_contact("老记录没有号", None, "333") is True
    assert store.is_do_not_contact("老记录没有号") is True


def shop(name: str, xhs_id: str | None) -> dict:
    extraction = ShopExtraction(
        shop_name=name, xhs_id=xhs_id, account_kind="personal", shop_type="suit_custom", bio_text="",
        price_mentions=[], fabric_mentions=["全麻衬"], high_end_mentions=["全手工"], store_mentions=[],
        recent_post_dates=["昨天"], content_topics=[], knitwear_present="unknown", unclear_fields=[],
    )
    return {"shop_key": f"shop_{name}_{xhs_id}", "status": "read", "extraction": extraction.model_dump()}


def test_batch_duplicates_are_detected_by_id_not_name() -> None:
    results = [shop("同名店", "111"), shop("同名店", "222"), shop("改名店", "111")]
    ranked = rank_results(results, date(2026, 10, 3), lambda name, xhs_id="": False, 5, 3)
    tiers = {item["shop_key"]: item["screening"]["tier"] for item in ranked}
    assert list(tiers.values()).count("excluded") == 1
    assert tiers["shop_同名店_222"] == "high"


def test_editing_an_approved_draft_is_recorded(tmp_path) -> None:
    store = make_store(tmp_path)
    state = {"lead_id": "l1", "evidence_id": "e1", "task_id": "t1", "shop_name": "示例", "video_text": "x",
             "channel": "dm", "draft": "原来的文案", "model_draft": "原来的文案", "xhs_id": "abc123",
             "task_status": "scheduled", "trace": []}
    store.save("run-1", {"id": "run-1", "stage": "scheduled", "interrupt": None, "state": state}, "content_reviewed")

    payload = store.update_draft("run-1", "改过的文案", describe_edit("原来的文案", "改过的文案", ["not_my_tone"], None, "scheduled", "t"))

    assert payload["state"]["draft"] == "改过的文案"
    assert payload["state"]["draft_edits"][0]["reasons"] == ["not_my_tone"]
    with Session(store.engine) as session:
        review = session.exec(select(HumanReview).where(HumanReview.review_type == "content_edit")).one()
        lead = session.get(ShopLead, "l1")
    assert review.before_text == "原来的文案" and review.after_text == "改过的文案"
    assert lead.handle == "abc123"

    store.save_contact_snapshot("run-1", payload["state"])
    with Session(store.engine) as session:
        features = json.loads(session.exec(select(ContactSnapshot)).one().features_json)
    assert features["draft_edits"][0]["ops"][0]["after"] == "改过"
