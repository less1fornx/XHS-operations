from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from ..models import (
    ShopLead,
)
from .base import DEFAULT_ACCOUNT_ID, _aware, StoreBase


class LeadsMixin(StoreBase):
    """Recognising the same shop again: identity, do-not-contact, contact history."""

    @staticmethod
    def _same_shop(lead: ShopLead, name: str, url: str, xhs_id: str) -> bool:
        """The 小红书号 decides when both sides have one; name and URL are fallbacks."""
        lead_id = (lead.handle or "").strip().casefold()
        if xhs_id and lead_id:
            return xhs_id == lead_id
        if lead.display_name.strip().casefold() == name:
            return True
        return bool(url) and (lead.profile_url or "").strip().rstrip("/").casefold() == url

    def _find_lead(
        self, session: Session, display_name: str, profile_url: str | None, xhs_id: str | None
    ) -> ShopLead | None:
        name = display_name.strip().casefold()
        url = (profile_url or "").strip().rstrip("/").casefold()
        key = (xhs_id or "").strip().casefold()
        leads = session.exec(
            select(ShopLead).where(ShopLead.platform_account_id == DEFAULT_ACCOUNT_ID)
        ).all()
        # Prefer an exact id match over a name match on an older record.
        if key:
            for lead in leads:
                if (lead.handle or "").strip().casefold() == key:
                    return lead
        for lead in leads:
            if self._same_shop(lead, name, url, key):
                return lead
        return None

    def is_do_not_contact(
        self, display_name: str, profile_url: str | None = None, xhs_id: str | None = None
    ) -> bool:
        with Session(self.engine) as session:
            lead = self._find_lead(session, display_name, profile_url, xhs_id)
            return bool(lead and lead.do_not_contact)

    def find_lead_id(
        self, display_name: str, profile_url: str | None = None, xhs_id: str | None = None
    ) -> str | None:
        with Session(self.engine) as session:
            lead = self._find_lead(session, display_name, profile_url, xhs_id)
            return lead.id if lead else None

    def contact_history(
        self, display_name: str, profile_url: str | None = None, xhs_id: str | None = None
    ) -> dict[str, Any] | None:
        """Whether this account was contacted before, looked up by 小红书号 first."""
        with Session(self.engine) as session:
            lead = self._find_lead(session, display_name, profile_url, xhs_id)
            if lead is None or lead.last_touched_at is None:
                return None
            return {
                "status": lead.contact_status,
                "do_not_contact": lead.do_not_contact,
                "last_touched_at": _aware(lead.last_touched_at).isoformat(),
            }
