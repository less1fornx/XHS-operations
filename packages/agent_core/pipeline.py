from __future__ import annotations

from .contracts import LeadAgentInput, LeadAgentOutput, check_prior_outcome_gate
from .prompting import build_prompt
from .providers import GenerationResult, LLMProvider


POLICY_GATE_PROVIDER = "policy_gate"
POLICY_GATE_VERSION = "prior-outcome-gate-v1"


def generate_lead_suggestion(
    agent_input: LeadAgentInput, provider: LLMProvider
) -> GenerationResult:
    """Single entry point shared by evaluation and the production worker.

    Order matters: deterministic policy gate first, model call second.
    """
    reject_reason = check_prior_outcome_gate(agent_input)
    if reject_reason is None:
        return provider.generate(agent_input, build_prompt(agent_input))

    output = LeadAgentOutput(
        route="reject",
        route_reason=reject_reason,
        shop_business_summary=agent_input.business_context_note,
        evidence_refs=[agent_input.evidence[0].evidence_id],
        personalization_points=[],
        product_match=[],
        draft="",
        quality_flags=["repeat_contact"],
        uncertainties=[],
        human_review_required=True,
    )
    return GenerationResult(
        output=output,
        provider=POLICY_GATE_PROVIDER,
        model=POLICY_GATE_VERSION,
        prompt_version="",
        request_id=None,
        usage={},
        latency_ms=0,
        raw_response={"policy_gate": POLICY_GATE_VERSION},
    )
