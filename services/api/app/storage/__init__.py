"""Everything the application stores, split by business area.

`RunStore` is still the single entry point: callers keep writing `store.end_run(...)`.
Each area's methods live in their own file so a change to one area stays in one place.
"""

from __future__ import annotations

from .base import DEFAULT_ACCOUNT_ID, LOCAL_ZONE, OPEN_STAGES, REVIEW_STAGES, BusinessRuleError, utc_now
from .batches import BatchesMixin
from .feedback import FeedbackMixin
from .leads import LeadsMixin
from .outcomes import OutcomesMixin
from .quota import QuotaMixin
from .runs import RunsMixin


class RunStore(RunsMixin, LeadsMixin, QuotaMixin, OutcomesMixin, BatchesMixin, FeedbackMixin):
    """One store over one database; the mixins only group its methods by area."""


__all__ = [
    "DEFAULT_ACCOUNT_ID", "LOCAL_ZONE", "OPEN_STAGES", "REVIEW_STAGES", "BusinessRuleError", "RunStore", "utc_now",
]
