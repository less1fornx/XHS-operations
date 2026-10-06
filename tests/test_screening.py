from __future__ import annotations

import os
import time
from datetime import date

from langgraph.types import Command
from sqlmodel import Session, SQLModel, select

from packages.agent_core.screening import (
    ShopExtraction,
    assign_routes,
    rank_key,
    post_age_days,
    screen_shop,
    to_lead_context,
)
from packages.agent_core.vision import MockShopExtractor
from services.api.app.models import ModelInvocation
from services.api.app.storage import RunStore
from services.worker.screening import make_read_shop, purge_old_uploads
from packages.workflows.screening_workflow import build_batch_graph, rank_results


TODAY = date(2026, 10, 3)


def shop(**overrides) -> ShopExtraction:
    base = dict(
        shop_name="示例定制",
        account_kind="personal",
        shop_type="suit_custom",
        bio_text="西装定制",
        followers_text="1000",
        location_text=None,
        price_mentions=[],
        fabric_mentions=[],
        high_end_mentions=[],
        store_mentions=[],
        recent_post_dates=[],
        content_topics=[],
        knitwear_present="unknown",
        unclear_fields=[],
    )
    return ShopExtraction(**{**base, **overrides})


def test_post_dates_shown_on_cards_are_parsed() -> None:
    assert post_age_days("昨天 10:18", TODAY) == 1
    assert post_age_days("3天前", TODAY) == 3
    assert post_age_days("2024-05-05", TODAY) == (TODAY - date(2024, 5, 5)).days
    assert post_age_days("09-25", TODAY) == 8
    assert post_age_days("12-20", TODAY) == (TODAY - date(2025, 12, 20)).days
    assert post_age_days("赞", TODAY) is None


# --- rules v1: gate, ranking, record-only (user 2026-10-06) ---------------------


def test_visible_high_price_and_recent_activity_come_first() -> None:
    result = screen_shop(shop(price_mentions=["¥2999"], recent_post_dates=["昨天"]), TODAY)
    assert result.tier == "high" and result.signals["C_price"].value == "yes"


def test_price_at_the_gate_counts_as_enough() -> None:
    assert screen_shop(shop(price_mentions=["¥1600"], recent_post_dates=["昨天"]), TODAY).tier == "high"


def test_no_price_passes_the_gate_but_ranks_later() -> None:
    # Most shops do not print prices; unknown is not the same as low.
    result = screen_shop(shop(recent_post_dates=["昨天"]), TODAY)
    assert result.tier == "medium" and result.signals["C_price"].value == "unknown"
    assert any("补一张带价格的截图" in reason for reason in result.tier_reasons)


def test_shop_whose_dearest_price_is_below_the_gate_is_not_contacted() -> None:
    result = screen_shop(shop(price_mentions=["¥980", "套装 1280元"], recent_post_dates=["昨天"]), TODAY)
    assert result.tier == "excluded" and "1280" in result.tier_reasons[0]


def test_starting_prices_and_extras_do_not_fail_the_gate() -> None:
    # "1k+" is a floor, and a 298 tie or a 500 coupon says nothing about suit prices.
    assert screen_shop(shop(price_mentions=["1k+"]), TODAY).tier == "medium"
    mixed = screen_shop(shop(price_mentions=["¥2999", "享 298真丝领带一条", "500的专属优惠券"], recent_post_dates=["昨天"]), TODAY)
    assert mixed.tier == "high"


def test_wording_alone_no_longer_counts_as_a_high_price() -> None:
    # v0 let "高级定制" stand in for a price, so nearly every shop ranked high.
    result = screen_shop(shop(high_end_mentions=["高级定制", "全手工"], fabric_mentions=["Scabal"], recent_post_dates=["昨天"]), TODAY)
    assert result.tier == "medium"


def test_stale_shop_goes_last_whatever_else_it_shows() -> None:
    result = screen_shop(shop(price_mentions=["¥5000"], recent_post_dates=["2026-04-14"]), TODAY)
    assert result.tier == "low" and any("超过 3 个月" in reason for reason in result.tier_reasons)


def test_recorded_facts_do_not_change_the_order() -> None:
    plain = shop(price_mentions=["¥2999"], recent_post_dates=["3天前"])
    dressed = shop(price_mentions=["¥2999"], recent_post_dates=["3天前"], pinned_post_dates=["2024-05"],
                   fabric_mentions=["Loro Piana"], store_mentions=["A店", "B店"], account_kind="enterprise_verified",
                   knitwear_present="yes")
    first, second = screen_shop(plain, TODAY), screen_shop(dressed, TODAY)
    assert rank_key(first, plain) == rank_key(second, dressed)
    assert second.recorded["置顶笔记"] == "有" and second.recorded["账号"] == "企业认证号"
    assert first.recorded["置顶笔记"] == "截图里没看到"


def test_visible_price_ranks_before_unknown_price_within_a_tier() -> None:
    seen = shop(price_mentions=["¥2999"], recent_post_dates=["09-01"])      # 32 days: medium
    unseen = shop(recent_post_dates=["昨天"])                                # no price: medium
    assert rank_key(screen_shop(seen, TODAY), seen) < rank_key(screen_shop(unseen, TODAY), unseen)


def test_non_suit_shop_passes_but_is_flagged_and_ranked_later() -> None:
    result = screen_shop(shop(shop_type="content_creator", price_mentions=["¥5000"], recent_post_dates=["昨天"]), TODAY)
    assert result.tier == "medium" and "不是西装定制店" in result.tier_reasons[0]


def test_suppliers_are_excluded() -> None:
    assert screen_shop(shop(shop_type="supplier"), TODAY).tier == "excluded"


def test_routes_fill_quota_in_rank_order() -> None:
    # The tier decides who is seen first, not the route: DMs go out in rank order, then comments.
    first = [screen_shop(shop(price_mentions=["¥2999"], recent_post_dates=["昨天"]), TODAY) for _ in range(1)]
    later = [screen_shop(shop(recent_post_dates=["昨天"]), TODAY) for _ in range(3)]
    stale = [screen_shop(shop(recent_post_dates=["2026-01-01"]), TODAY)]
    ordered = first + later + stale
    assign_routes(ordered, dm_slots=2, comment_slots=1)
    assert [item.suggested_route for item in ordered] == ["dm", "dm", "comment", "none", "none"]


def test_lead_context_carries_evidence_for_drafting() -> None:
    extraction = shop(fabric_mentions=["全麻衬"], content_topics=["春夏亚麻西装"], knitwear_present="no")
    context = to_lead_context(extraction, screen_shop(extraction, TODAY))
    assert "全麻衬" in context["purchase_signal_note"]
    assert "春夏亚麻西装" in context["video_text"]


def make_store(tmp_path) -> RunStore:
    store = RunStore(tmp_path / "test.db")
    SQLModel.metadata.create_all(store.engine)
    return store


def write_images(tmp_path, *names: str) -> list[str]:
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"fake-image")
        paths.append(str(path))
    return paths


def test_batch_reads_each_shop_isolates_failures_and_waits_for_review(tmp_path) -> None:
    store = make_store(tmp_path)
    graph = build_batch_graph(
        make_read_shop(store, MockShopExtractor()),
        lambda name, xhs_id="": name == "勿扰店",
        lambda: TODAY,
    )
    shops = [
        {"batch_id": "b1", "shop_key": "shop_0", "image_paths": write_images(tmp_path, "好店.jpg")},
        {"batch_id": "b1", "shop_key": "shop_1", "image_paths": write_images(tmp_path, "fail-blurry.jpg")},
        {"batch_id": "b1", "shop_key": "shop_2", "image_paths": write_images(tmp_path, "勿扰店.jpg")},
    ]
    config = {"configurable": {"thread_id": "batch-b1"}}

    state = graph.invoke(
        {"batch_id": "b1", "shops": shops, "dm_slots": 5, "comment_slots": 3, "results": []},
        config,
    )

    assert {r["shop_key"]: r["status"] for r in state["results"]} == {
        "shop_0": "read",
        "shop_1": "failed",
        "shop_2": "read",
    }
    tiers = {r["shop_key"]: r["screening"]["tier"] for r in state["ranked"]}
    assert tiers == {"shop_0": "medium", "shop_2": "excluded"}
    assert state["ranked"][0]["screening"]["suggested_route"] == "dm"
    assert state["__interrupt__"][0].value["type"] == "batch_review"
    with Session(store.engine) as session:
        statuses = sorted(r.status for r in session.exec(select(ModelInvocation)))
    assert statuses == ["failed", "succeeded", "succeeded"]

    final = graph.invoke(Command(resume={"decisions": [{"shop_key": "shop_0", "route": "dm"}]}), config)
    assert final["status"] == "reviewed"
    assert final["review"]["decisions"][0]["route"] == "dm"


def test_old_screenshot_folders_are_purged(tmp_path) -> None:
    old, fresh = tmp_path / "old-batch", tmp_path / "fresh-batch"
    old.mkdir()
    fresh.mkdir()
    eleven_days_ago = time.time() - 11 * 86400
    os.utime(old, (eleven_days_ago, eleven_days_ago))

    removed = purge_old_uploads(tmp_path, days=10)

    assert removed == ["old-batch"]
    assert fresh.exists() and not old.exists()


def test_correcting_shop_type_re_tiers_the_shop() -> None:
    misread = shop(
        shop_type="content_creator",
        fabric_mentions=["Loro Piana"],
        high_end_mentions=["高定"],
        recent_post_dates=["昨天"],
    )
    results = [{"shop_key": "shop_0", "status": "read", "extraction": misread.model_dump()}]
    before = rank_results(results, TODAY, lambda name, xhs_id="": False, 5, 3)
    results[0]["extraction"]["shop_type"] = "suit_custom"
    after = rank_results(results, TODAY, lambda name, xhs_id="": False, 5, 3)

    assert "不是西装定制店" in before[0]["screening"]["tier_reasons"][0]
    assert after[0]["screening"]["tier_reasons"][0] == "门槛通过"
    assert after[0]["screening"]["suggested_route"] == "dm"


def test_non_shop_images_are_reported_as_not_a_shop_page(tmp_path) -> None:
    store = make_store(tmp_path)
    chat = shop(page_kind="other", page_summary="小红书私信对话界面", shop_name="", shop_type="unknown")
    graph = build_batch_graph(
        make_read_shop(store, MockShopExtractor({"chat": chat})), lambda name, xhs_id="": False, lambda: TODAY
    )
    shops = [{"batch_id": "b2", "shop_key": "shop_0", "image_paths": write_images(tmp_path, "chat.jpg")}]

    state = graph.invoke(
        {"batch_id": "b2", "shops": shops, "dm_slots": 5, "comment_slots": 3, "results": []},
        {"configurable": {"thread_id": "batch-b2"}},
    )

    result = state["results"][0]
    assert result["status"] == "failed"
    assert result["error_kind"] == "not_shop_page"
    assert "私信对话" in result["error"]
    with Session(store.engine) as session:
        assert session.exec(select(ModelInvocation)).one().status == "succeeded"


def test_pinned_notes_do_not_count_towards_activity() -> None:
    only_pinned = screen_shop(shop(pinned_post_dates=["2024-05-05", "2023-12-13"]), TODAY)
    assert only_pinned.signals["A_active"].value == "unknown"
    assert only_pinned.latest_post_age_days is None

    with_recent = screen_shop(
        shop(pinned_post_dates=["2024-05-05"], recent_post_dates=["3天前"]), TODAY
    )
    assert with_recent.signals["A_active"].value == "yes"
    assert with_recent.latest_post_age_days == 3


def test_overlong_lists_are_trimmed_not_rejected() -> None:
    # A chain listing 12 branches used to fail the whole shop with "格式不对".
    data = MockShopExtractor().extract([("连锁店.png", b"x")]).extraction.model_dump()
    data["store_mentions"] = [f"门店{i}" for i in range(12)]
    data["bio_text"] = "西" * 2500

    extraction = ShopExtraction.model_validate(data)

    assert extraction.store_mentions == [f"门店{i}" for i in range(10)]
    assert len(extraction.bio_text) == 2000


def test_missing_fields_become_blank_and_are_flagged() -> None:
    # User decision 2026-10-05: a field the model left out is recorded as empty, not a failure.
    extraction = ShopExtraction.model_validate({
        "shop_name": "某西装定制", "account_kind": "personal", "shop_type": "suit_custom",
        "fabric_mentions": None, "knitwear_present": None,
    })

    assert extraction.fabric_mentions == [] and extraction.bio_text == ""
    assert extraction.knitwear_present == "unknown"
    assert "面料（模型没有返回）" in extraction.unclear_fields
    assert "店铺名称（模型没有返回）" not in extraction.unclear_fields
