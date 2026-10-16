from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Route = Literal["dm", "comment", "reject"]
AccountType = Literal["personal", "professional", "unknown"]
ShopType = Literal[
    "suit_custom",
    "menswear",
    "apparel_studio",
    "content_creator",
    "staff_account",
    "hobbyist",
    "supplier",
    "other",
]
PriceLevel = Literal["mid_high", "high", "unknown"]
ContactMode = Literal["cold", "warm", "inbound", "explicit_permission"]
# Who most likely reads the message (user decision 2026-10-05): a store's official
# account is read by staff, a personal shop by the maker. The draft differs for each.
Audience = Literal["store_account", "personal_shop", "unknown"]
PriorOutcome = Literal["none", "replied", "declined", "read_no_reply", "blocked"]
QualityFlag = Literal[
    "insufficient_evidence",
    "unsupported_claim",
    "generic_template",
    "off_platform_reference",
    "repeat_contact",
    "commercial_comment",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)
    source_type: Literal["profile", "content", "conversation", "manual"]


class LeadAgentInput(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    shop_name: str = Field(min_length=1, max_length=200)
    xhs_account: str | None = Field(default=None, max_length=200)
    profile_url: str | None = Field(default=None, max_length=1000)
    account_type: AccountType = "unknown"
    shop_type: ShopType
    audience: Audience = "unknown"
    activity_note: str = Field(min_length=1, max_length=1000)
    price_level: PriceLevel = "unknown"
    business_context_note: str = Field(min_length=1, max_length=2000)
    purchase_signal_note: str | None = Field(default=None, max_length=2000)
    content_url: str | None = Field(default=None, max_length=1000)
    content_text: str | None = Field(default=None, max_length=4000)
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=10)
    contact_mode: ContactMode
    contact_history_note: str = Field(min_length=1, max_length=2000)
    prior_outcome: PriorOutcome
    product_facts: list[str] = Field(min_length=1, max_length=20)
    # Who we say we are is decided by the program from the shop type (user 2026-10-09):
    # a suit shop hears about suit inner layers, every other account about flat knitting.
    positioning: Literal["suit_inner", "flat_knit"] = "suit_inner"
    self_description: str = Field(default="", max_length=100)
    # Chosen by the program for a first cold DM; the draft names these strengths and no others.
    strengths_to_use: list[str] = Field(default_factory=list, max_length=4)
    brand_context: str | None = Field(default=None, max_length=2000)
    # Set only when a human has already chosen the route and needs a draft for it.
    human_selected_route: Literal["dm", "comment"] | None = None
    # Revising an earlier draft. One channel, two sources: what the human asked for
    # at review, and what the program's checks found (designed 2026-10-05).
    previous_draft: str | None = Field(default=None, max_length=2000)
    revision_request: str | None = Field(default=None, max_length=500)
    revision_issues: list[str] = Field(default_factory=list, max_length=10)


class PersonalizationPoint(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    evidence_ref: str = Field(min_length=1, max_length=100)


class LeadAgentOutput(StrictModel):
    route: Route
    route_reason: str = Field(min_length=1, max_length=1000)
    shop_business_summary: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(min_length=1, max_length=10)
    personalization_points: list[PersonalizationPoint] = Field(max_length=5)
    product_match: list[str] = Field(max_length=10)
    draft: str = Field(max_length=2000)
    quality_flags: list[QualityFlag] = Field(max_length=10)
    uncertainties: list[str] = Field(max_length=10)
    human_review_required: bool

    @model_validator(mode="after")
    def validate_route_content(self) -> "LeadAgentOutput":
        if not self.human_review_required:
            raise ValueError("所有路线都必须进入人工审核")
        if self.route == "reject" and self.draft.strip():
            raise ValueError("reject 路线的 draft 必须为空")
        if self.route != "reject" and not self.draft.strip():
            raise ValueError("dm/comment 路线必须返回可供人工审核的 draft")
        return self


def validate_evidence_refs(
    agent_input: LeadAgentInput, output: LeadAgentOutput
) -> list[str]:
    """Return invalid evidence ids instead of trusting model citations."""
    known = {item.evidence_id for item in agent_input.evidence}
    cited = [*output.evidence_refs, *(point.evidence_ref for point in output.personalization_points)]
    # Models sometimes put two ids in one field ("e1,e2"); that is still a citation of known evidence.
    refs = {part for ref in cited for part in re.split(r"[\s,，、;；]+", ref) if part}
    return sorted(refs - known)


NO_CONTACT_OUTCOMES: frozenset[str] = frozenset({"declined", "read_no_reply", "blocked"})


def check_prior_outcome_gate(agent_input: LeadAgentInput) -> str | None:
    """Return a reject reason when prior outcome forbids contact, else None.

    Deterministic do-not-contact rule. It runs before any provider call so the
    model can neither override it nor be billed for it.
    """
    if agent_input.prior_outcome in NO_CONTACT_OUTCOMES:
        return f"历史联系结果为 {agent_input.prior_outcome}，按勿扰规则停止联系"
    return None
