"""What a human changed in a draft, kept so prompts can be improved from real edits.

The reasons list is a first version proposed by Claude on 2026-10-03; the user
has not yet confirmed it. "other" plus a free note lets new reasons surface.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

EDIT_REASONS: dict[str, str] = {
    "too_long": "太长",
    "missed_shop": "没抓住这家店的特点",
    "wrong_selling_point": "卖点不对或不全",
    "not_my_tone": "语气不像我",
    "too_generic": "太像群发",
    "wrong_fact": "有事实错误",
    "other": "其他",
}


def diff_ops(before: str, after: str) -> list[dict[str, str]]:
    """Changed spans only, in reading order: what was removed and what replaced it."""
    ops: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            ops.append({"op": tag, "before": before[i1:i2], "after": after[j1:j2]})
    return ops


def describe_edit(before: str, after: str, reasons: list[str], note: str | None, stage: str, at: str) -> dict[str, Any]:
    ops = diff_ops(before, after)
    return {
        "at": at,
        "stage": stage,
        "before": before,
        "after": after,
        "ops": ops,
        "removed_chars": sum(len(op["before"]) for op in ops),
        "added_chars": sum(len(op["after"]) for op in ops),
        "reasons": [reason for reason in reasons if reason in EDIT_REASONS],
        "note": (note or "").strip() or None,
    }
