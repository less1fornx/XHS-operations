from __future__ import annotations

import os
import time
from datetime import date

from langgraph.types import Command
from sqlmodel import Session, SQLModel, select

from packages.agent_core.screening import (
    ShopExtraction,
    assign_routes,
    post_age_days,
    screen_shop,
    to_lead_context,
)
from packages.agent_core.vision import MockShopExtractor
from services.api.app.models import ModelInvocation
from services.api.app.storage import RunStore
from services.worker.screening import make_read_shop, purge_old_uploads
from services.worker.screening_graph import build_batch_graph


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


def test_fabric_plus_store_plus_activity_is_high() -> None:
    result = screen_shop(
        shop(fabric_mentions=["Loro Piana"], store_mentions=["线下门店"], recent_post_dates=["昨天"]),
        TODAY,
    )
    assert result.tier == "high"


def test_store_and_activity_without_fabric_or_price_is_medium() -> None:
    result = screen_shop(shop(store_mentions=["线下门店"], recent_post_dates=["昨天"]), TODAY)
    assert result.tier == "medium"


def test_visible_price_at_threshold_counts_as_high_price() -> None:
    result = screen_shop(shop(price_mentions=["¥1600"], store_mentions=["门店"]), TODAY)
    assert result.signals["C_high_price"].value == "yes"
    assert result.tier == "high"


def test_unseen_price_is_unknown_not_no() -> None:
    result = screen_shop(shop(), TODAY)
    assert result.signals["C_high_price"].value == "unknown"


def test_inactive_shop_is_low_even_with_strong_signals() -> None:
    result = screen_shop(
        shop(fabric_mentions=["Scabal"], high_end_mentions=["全手工"], recent_post_dates=["2026-04-14"]),
        TODAY,
    )
    assert result.tier == "low"


def test_non_suit_shop_is_capped_at_medium() -> None:
    result = screen_shop(
        shop(shop_type="content_creator", fabric_mentions=["Zegna"], high_end_mentions=["高定"]),
        TODAY,
    )
    assert result.tier == "medium"


def test_suppliers_are_excluded() -> None:
    assert screen_shop(shop(shop_type="supplier"), TODAY).tier == "excluded"


def test_routes_fill_quota_in_rank_order() -> None:
    high = [screen_shop(shop(fabric_mentions=["x"], store_mentions=["y"]), TODAY) for _ in range(3)]
    medium = [screen_shop(shop(store_mentions=["y"]), TODAY) for _ in range(2)]
    assign_routes(high + medium, dm_slots=2, comment_slots=2)
    assert [s.suggested_route for s in high + medium] == ["dm", "dm", "comment", "comment", "none"]


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
        lambda name: name == "勿扰店",
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
    assert tiers == {"shop_0": "high", "shop_2": "excluded"}
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
