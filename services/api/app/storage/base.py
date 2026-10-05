from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from sqlmodel import create_engine


DEFAULT_ACCOUNT_ID = "local-xhs-personal"
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


REVIEW_STAGES = ("route_review", "content_review")
OPEN_STAGES = (*REVIEW_STAGES, "scheduled", "opened")


class BusinessRuleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they were stored in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class StoreBase:
    """The database connection every part of the store shares."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        self.quota_lock = Lock()
