from __future__ import annotations

import os
import time
from pathlib import Path

from packages.agent_core.vision import MockShopExtractor, identify_screenshot
from services.worker.inbox import DONE_DIR, SEARCH_DIR, UNKNOWN_DIR, explicit_group, list_pending, move_into, plan_import


def put(root: Path, name: str, content: bytes, age: float = 10) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    past = time.time() - age
    os.utime(path, (past, past))
    return path


def identify(path: Path):
    return identify_screenshot(MockShopExtractor(), (path.name, path.read_bytes()))


def test_naming_rules_only_trust_names_a_human_chose(tmp_path) -> None:
    assert explicit_group(tmp_path / "睿曼-1.png", tmp_path) == "睿曼"
    assert explicit_group(tmp_path / "睿曼 - 2.png", tmp_path) == "睿曼"
    assert explicit_group(tmp_path / "萨维诺" / "随便.png", tmp_path) == "萨维诺"
    # names a computer or phone gives a screenshot must not be read as a shop
    assert explicit_group(tmp_path / "截屏2026-10-06 10.23.45.png", tmp_path) is None
    assert explicit_group(tmp_path / "Screenshot_20261006-102345.png", tmp_path) is None
    assert explicit_group(tmp_path / "IMG_4821.png", tmp_path) is None
    assert explicit_group(tmp_path / "20261006102345-1.png", tmp_path) is None


def test_pending_skips_results_hidden_files_and_files_still_arriving(tmp_path) -> None:
    put(tmp_path, "a.png", b"SHOP:A")
    put(tmp_path, "notes.txt", b"x")
    put(tmp_path, ".DS_Store", b"x")
    put(tmp_path, f"{DONE_DIR}/20261006-1000/old.png", b"SHOP:A")
    put(tmp_path, "店B/1.png", b"SHOP:whatever")
    put(tmp_path, "arriving.png", b"SHOP:C", age=0)

    assert [f.name for f in list_pending(tmp_path)] == ["a.png", "1.png"] or sorted(f.name for f in list_pending(tmp_path)) == ["1.png", "a.png"]


def test_unnamed_screenshots_are_grouped_by_the_account_in_the_picture(tmp_path) -> None:
    put(tmp_path, "截屏1.png", "SHOP:睿曼西服".encode(), age=50)
    put(tmp_path, "截屏2.png", "SHOP:萨维诺".encode(), age=40)
    put(tmp_path, "截屏3.png", "SHOP:睿曼 西服".encode(), age=30)       # same shop, spacing differs
    put(tmp_path, "截屏4.png", b"SEARCH", age=20)
    put(tmp_path, "截屏5.png", b"DM", age=15)
    put(tmp_path, "截屏6.png", b"something else", age=12)
    put(tmp_path, "备注店/x.png", b"anything", age=11)                  # a named folder is trusted, not read

    plan = plan_import(tmp_path, list_pending(tmp_path), identify)

    assert {g["label"]: [f.name for f in g["files"]] for g in plan.groups} == {
        "睿曼西服": ["截屏1.png", "截屏3.png"], "萨维诺": ["截屏2.png"], "备注店": ["x.png"],
    }
    assert {s["file"].name: s["move_to"] for s in plan.skipped} == {
        "截屏4.png": SEARCH_DIR, "截屏5.png": UNKNOWN_DIR, "截屏6.png": UNKNOWN_DIR,
    }


def test_more_than_three_pictures_of_one_shop_uses_the_first_three(tmp_path) -> None:
    for index in range(5):
        put(tmp_path, f"店-{index + 1}.png", b"x", age=100 - index)
    plan = plan_import(tmp_path, list_pending(tmp_path), identify)
    assert len(plan.groups[0]["files"]) == 3 and len(plan.groups[0]["unused"]) == 2


def test_handled_files_are_moved_never_deleted(tmp_path) -> None:
    first = put(tmp_path, "a.png", b"1")
    again = put(tmp_path, "店B/a.png", b"2")
    moved = move_into(tmp_path, first, DONE_DIR, "20261006-1000")
    other = move_into(tmp_path, again, DONE_DIR, "20261006-1000")

    assert moved.read_bytes() == b"1" and other.read_bytes() == b"2" and moved != other
    assert not first.exists() and not (tmp_path / "店B").exists()


def test_a_name_cut_short_by_scrolling_joins_the_full_name(tmp_path) -> None:
    # Real case 2026-10-06: the scrolled page showed "西装先生 MR.CO..." and was split from its own profile.
    put(tmp_path, "截屏1.png", "SHOP:西装先生 MR.CO...".encode(), age=50)
    put(tmp_path, "截屏2.png", "SHOP:西装先生 MR.COLLECTION".encode(), age=40)
    put(tmp_path, "截屏3.png", "SHOP:西装先生 MR.CO…".encode(), age=30)
    put(tmp_path, "截屏4.png", "SHOP:西装定制小店".encode(), age=20)      # shares two characters only: stays apart

    plan = plan_import(tmp_path, list_pending(tmp_path), identify)

    assert {g["label"]: len(g["files"]) for g in plan.groups} == {"西装先生 MR.COLLECTION": 3, "西装定制小店": 1}
