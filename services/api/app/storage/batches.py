from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from sqlmodel import Session, select

from ..models import (
    ScreeningBatch,
)
from .base import utc_now, StoreBase


class BatchesMixin(StoreBase):
    """Screenshot screening batches."""

    def save_batch(self, batch_id: str, status: str, payload: dict[str, Any]) -> None:
        now = utc_now()
        with Session(self.engine) as session:
            batch = session.get(ScreeningBatch, batch_id)
            if batch is None:
                batch = ScreeningBatch(id=batch_id, status=status, created_at=now)
                session.add(batch)
            batch.status = status
            batch.shop_count = len(payload.get("results", []))
            batch.payload_json = json.dumps(payload, ensure_ascii=False)
            batch.updated_at = now
            session.commit()

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            batch = session.get(ScreeningBatch, batch_id)
            return json.loads(batch.payload_json) if batch else None

    def list_batches(self, limit: int = 30) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = (
                select(ScreeningBatch).order_by(ScreeningBatch.created_at.desc()).limit(limit)
            )
            return [
                {
                    "id": row.id,
                    "status": row.status,
                    "shop_count": row.shop_count,
                    # SQLite drops the timezone; stored values are UTC.
                    "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
                    "shop_names": [
                        name
                        for result in json.loads(row.payload_json).get("results", [])
                        if (name := (result.get("extraction") or {}).get("shop_name"))
                    ],
                }
                for row in session.exec(statement)
            ]
