from __future__ import annotations

from datetime import timezone
from typing import Any

from sqlmodel import Session, select

from ..models import (
    FeedbackNote,
)
from .base import StoreBase


class FeedbackMixin(StoreBase):
    """Remarks the user leaves about the system itself."""

    def add_feedback_note(self, area: str, text: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            note = FeedbackNote(area=area, text=text)
            session.add(note)
            session.commit()
            session.refresh(note)
            return self._feedback_dict(note)

    def list_feedback_notes(self, limit: int = 50) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = select(FeedbackNote).order_by(FeedbackNote.created_at.desc()).limit(limit)
            return [self._feedback_dict(note) for note in session.exec(statement)]

    @staticmethod
    def _feedback_dict(note: FeedbackNote) -> dict[str, Any]:
        return {
            "id": note.id,
            "area": note.area,
            "text": note.text,
            "status": note.status,
            "created_at": note.created_at.replace(tzinfo=timezone.utc).isoformat(),
        }
