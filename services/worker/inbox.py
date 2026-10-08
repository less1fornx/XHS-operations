"""Screenshots dropped into a shared folder become a screening batch.

The human still browses Xiaohongshu and takes the screenshots; this only removes the
upload and the "which pictures belong together" steps (designed with the user 2026-10-06).
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from packages.agent_core.providers import ProviderResponseError
from packages.agent_core.screening import MAX_IMAGES_PER_SHOP, MAX_SHOPS_PER_BATCH, normalize_xhs_id
from packages.agent_core.vision import ScreenshotTriage

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
DONE_DIR, UNKNOWN_DIR, SEARCH_DIR = "已读取", "没认出来", "搜索结果"
RESERVED_DIRS = (DONE_DIR, UNKNOWN_DIR, SEARCH_DIR)
# A file still being copied over the network must not be read half-written.
SETTLE_SECONDS = 3.0
# Names the system or a phone gives a screenshot; they say nothing about the shop.
_AUTO_NAME = re.compile(r"^(截屏|截图|屏幕截图|屏幕快照|screenshot|screen shot|img|image|wechatimg|微信图片|mmexport|photo|snipaste)", re.I)
_NUMBERED = re.compile(r"^(?P<name>.+?)\s*[-_－—]\s*\d{1,2}$")

Identify = Callable[[Path], ScreenshotTriage]


def inbox_dir() -> Path | None:
    load_dotenv(PROJECT_ROOT / ".env")
    value = os.getenv("AGENT_INBOX_DIR", "").strip()
    return Path(value) if value else None


def list_pending(root: Path, now: float | None = None) -> list[Path]:
    """Images waiting in the folder and its one-level shop subfolders, oldest first."""
    now = time.time() if now is None else now
    found: list[Path] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or entry.name in RESERVED_DIRS:
            continue
        candidates = [entry] if entry.is_file() else [f for f in sorted(entry.iterdir()) if f.is_file()]
        for file in candidates:
            if file.name.startswith(".") or file.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            stat = file.stat()
            if stat.st_size > 0 and now - stat.st_mtime >= SETTLE_SECONDS:
                found.append(file)
    return sorted(found, key=lambda f: f.stat().st_mtime)


def explicit_group(file: Path, root: Path) -> str | None:
    """The shop a human named: a subfolder, or a file called 店名-1.png, 店名-2.png."""
    if file.parent != root:
        return file.parent.name
    match = _NUMBERED.match(file.stem)
    if match and not _AUTO_NAME.match(file.stem) and not re.search(r"\d{6,}", match.group("name")):
        return match.group("name").strip()
    return None


def _same(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _name_key(name: str, grouped: dict[str, dict]) -> str:
    """Group key for an account name. A scrolled page shows the name cut short
    ("西装先生 MR.CO..."); that is the same shop as the full name it begins (seen 2026-10-06)."""
    plain = _same(name)
    stem = re.sub(r"(\.{2,}|…+)$", "", plain)
    cut_short = stem != plain
    for key in grouped:
        if not key.startswith("name:"):
            continue
        other = key[5:]
        other_stem = re.sub(r"(\.{2,}|…+)$", "", other)
        if other == plain:
            return key
        if cut_short and len(stem) >= 4 and other_stem.startswith(stem):
            return key
        if other_stem != other and len(other_stem) >= 4 and stem.startswith(other_stem):
            return key
    return f"name:{plain}"


@dataclass
class InboxPlan:
    groups: list[dict] = field(default_factory=list)      # {"label", "files", "unused"}
    skipped: list[dict] = field(default_factory=list)     # {"file", "reason", "move_to"}
    waiting: int = 0                                       # groups left for the next import


def plan_import(root: Path, files: list[Path], identify: Identify) -> InboxPlan:
    """Decide which screenshots form which shop. Named groups are trusted; the rest are read."""
    plan = InboxPlan()
    grouped: dict[str, dict] = {}

    def add(key: str, label: str, file: Path) -> None:
        group = grouped.setdefault(key, {"label": label, "files": [], "names": []})
        group["files"].append(file)
        group["names"].append(label)

    by_id: dict[str, str] = {}      # 小红书号 -> group key, so a profile and a note of one shop meet
    for file in files:
        named = explicit_group(file, root)
        if named:
            add(f"named:{_same(named)}", named, file)
            continue
        try:
            seen = identify(file)
        except ProviderResponseError as exc:
            plan.skipped.append({"file": file, "reason": f"读取出错，先留在原处：{str(exc)[:80]}", "move_to": None})
            continue
        if seen.page_kind == "search_results":
            plan.skipped.append({"file": file, "reason": "搜索结果页，已放到“搜索结果”文件夹（初筛功能下一步支持）", "move_to": SEARCH_DIR})
        elif seen.page_kind == "dm_conversation":
            plan.skipped.append({"file": file, "reason": "私信截图不在这里读取，请在任务的“记录回复”里上传", "move_to": UNKNOWN_DIR})
        elif seen.page_kind == "other" or not seen.account_name.strip():
            plan.skipped.append({"file": file, "reason": f"认不出是哪家店：{seen.summary or '不是店铺主页或笔记'}", "move_to": UNKNOWN_DIR})
        else:
            xhs_id = normalize_xhs_id(seen.xhs_id).casefold()
            key = by_id.get(xhs_id) if xhs_id else None
            key = key or _name_key(seen.account_name, grouped)
            if xhs_id:
                by_id[xhs_id] = key
            add(key, seen.account_name.strip(), file)

    for group in grouped.values():
        # Show the fullest name seen, not a cut-short one.
        names = [n for n in group.pop("names", []) if n]
        if names:
            group["label"] = max(names, key=lambda n: (not re.search(r"(\.{2,}|…+)$", n), len(_same(n))))
    for index, group in enumerate(grouped.values()):
        if index >= MAX_SHOPS_PER_BATCH:
            plan.waiting += 1
            continue
        group["unused"] = group["files"][MAX_IMAGES_PER_SHOP:]
        group["files"] = group["files"][:MAX_IMAGES_PER_SHOP]
        plan.groups.append(group)
    return plan


def move_into(root: Path, file: Path, folder: str, stamp: str | None = None) -> Path:
    """Move a handled screenshot into a result subfolder of the inbox. Never deletes."""
    target_dir = root / folder / stamp if stamp else root / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{file.parent.name}-" if file.parent != root else ""
    target = target_dir / f"{prefix}{file.name}"
    counter = 1
    while target.exists():
        target = target_dir / f"{prefix}{file.stem}-{counter}{file.suffix}"
        counter += 1
    shutil.move(str(file), str(target))
    if file.parent != root and not any(file.parent.iterdir()):
        file.parent.rmdir()       # an emptied per-shop subfolder has done its job
    return target


def batch_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M")


# --- Screenshots pasted on the page are filed here under a name the rules understand ---

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_label(label: str) -> str:
    """A shop remark safe to use as a file name and readable by explicit_group()."""
    text = _UNSAFE.sub("", label).strip().strip(".-_ ")[:40]
    if not text or _AUTO_NAME.match(text) or re.search(r"\d{6,}", text) or text in RESERVED_DIRS:
        return ""
    return text


def save_pasted(root: Path, data: bytes, suffix: str, kind: str, label: str = "") -> Path:
    """Write one pasted screenshot into the inbox. Shop pictures become 店名-序号; search pages go to 搜索结果."""
    if kind == "search":
        folder = root / SEARCH_DIR
        folder.mkdir(parents=True, exist_ok=True)
        stem = "搜索" + datetime.now().strftime("%m%d-%H%M%S")
        target = folder / f"{stem}{suffix}"
        counter = 1
        while target.exists():
            target = folder / f"{stem}-{counter}{suffix}"
            counter += 1
    else:
        name = clean_label(label)
        if not name:
            raise ValueError("这家店的备注名不能用作文件名，请换一个（不要用纯数字或“截图”开头）")
        used = [
            int(match.group(1))
            for file in root.iterdir()
            if file.is_file() and (match := re.match(re.escape(name) + r"-(\d{1,2})$", file.stem))
        ]
        target = root / f"{name}-{max(used, default=0) + 1}{suffix}"
    target.write_bytes(data)
    return target


def pending_by_shop(root: Path, files: list[Path]) -> list[dict]:
    """Waiting screenshots as the page shows them: named shops with counts, and the unnamed rest."""
    counts: dict[str, int] = {}
    unnamed = 0
    for file in files:
        name = explicit_group(file, root)
        if name:
            counts[name] = counts.get(name, 0) + 1
        else:
            unnamed += 1
    rows = [{"label": name, "images": count} for name, count in counts.items()]
    if unnamed:
        rows.append({"label": None, "images": unnamed})
    return rows
