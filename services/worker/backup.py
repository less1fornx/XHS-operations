from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
BACKUP_KEEP = int(os.getenv("AGENT_BACKUP_KEEP", "14"))
# Business records and workflow checkpoints; screenshots are not backed up (10-day retention).
DATABASES = ("agent_ops.db", "agent_checkpoints.sqlite")


def backup_if_due(
    data_dir: Path = DATA_DIR, *, today: date | None = None, keep: int = BACKUP_KEEP
) -> list[Path]:
    """Copy each database once per day into data/backups and drop copies beyond `keep`."""
    stamp = (today or date.today()).strftime("%Y%m%d")
    backup_dir = Path(os.getenv("AGENT_BACKUP_DIR", str(data_dir / "backups")))
    created: list[Path] = []
    for name in DATABASES:
        source = data_dir / name
        if not source.exists():
            continue
        stem, suffix = source.stem, source.suffix
        target = backup_dir / f"{stem}-{stamp}{suffix}"
        if not target.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".partial")
            # The backup API gives a consistent copy even while the app is writing (WAL mode).
            with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(partial) as dst:
                src.backup(dst)
            dst.close()
            src.close()
            partial.replace(target)
            created.append(target)
        for old in sorted(backup_dir.glob(f"{stem}-????????{suffix}"))[:-keep]:
            old.unlink()
    return created


def main() -> None:
    created = backup_if_due()
    for path in created:
        print(f"已备份：{path.relative_to(PROJECT_ROOT)}")
    if not created:
        print("今天已经备份过。")


if __name__ == "__main__":
    main()
