from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from packages.agent_core.contracts import EvidenceItem, LeadAgentInput
from packages.agent_core.pipeline import POLICY_GATE_PROVIDER, generate_lead_suggestion
from packages.agent_core.prompting import PROMPT_VERSION, system_prompt_sha256
from packages.agent_core.providers import (
    GenerationResult,
    LLMProvider,
    ProviderResponseError,
)
from services.api.app.storage import RunStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_FACTS_PATH = PROJECT_ROOT / "config" / "product_facts.v2.json"

CONTACT_MODE_BY_BASIS = {
    "cold_outreach": "cold",
    "prior_conversation": "warm",
    "inbound_inquiry": "inbound",
    "explicit_permission": "explicit_permission",
}
ROUTE_TO_CHANNEL = {"dm": "dm", "comment": "comment", "reject": "none"}


def load_product_facts(path: Path = PRODUCT_FACTS_PATH) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8"))["facts"]


def build_agent_input(
    state: dict[str, Any], product_facts: list[str], forced_route: str | None = None
) -> LeadAgentInput:
    """Turn human-entered workflow state into the model contract."""
    basis = state.get("contact_basis", "cold_outreach")
    basis_note = (state.get("contact_basis_note") or "").strip()
    shop_note = (state.get("shop_note") or "").strip()
    video_text = (state.get("video_text") or "").strip()
    evidence = [EvidenceItem(evidence_id="e1", text=video_text[:1000], source_type="content")]
    if shop_note:
        evidence.append(EvidenceItem(evidence_id="e2", text=shop_note[:1000], source_type="profile"))
    if basis_note:
        evidence.append(EvidenceItem(evidence_id="e3", text=basis_note[:1000], source_type="manual"))
    return LeadAgentInput(
        case_id=state.get("workflow_run_id") or state.get("task_id") or "run",
        shop_name=state["shop_name"][:200],
        xhs_account=(state.get("xhs_id") or None),
        profile_url=state.get("profile_url") or None,
        shop_type=state.get("shop_type") or "suit_custom",
        activity_note=(state.get("activity_note") or "").strip()[:1000] or "人工未填写活跃情况",
        business_context_note=shop_note[:2000] or "人工未填写店铺定位",
        purchase_signal_note=(state.get("purchase_signal_note") or "").strip()[:2000] or None,
        content_text=video_text[:4000],
        evidence=evidence,
        contact_mode=CONTACT_MODE_BY_BASIS.get(basis, "cold"),
        contact_history_note=basis_note[:2000] or "此前没有互动或私信",
        prior_outcome="replied" if basis == "prior_conversation" else "none",
        product_facts=product_facts,
        human_selected_route=forced_route,
    )


def build_suggester(
    store: RunStore, provider: LLMProvider, product_facts: list[str]
) -> Callable[..., dict[str, Any]]:
    """Build the callable the workflow runs to get a suggestion or a forced-route draft."""

    def failed(message: str) -> dict[str, Any]:
        return {"model_status": "failed", "model_error": message, "suggested_channel": "none"}

    def suggest(state: dict[str, Any], forced_route: str | None = None) -> dict[str, Any]:
        try:
            agent_input = build_agent_input(state, product_facts, forced_route)
        except ValidationError as exc:
            return failed(f"录入内容不符合模型输入要求: {exc.errors()[0]['msg']}")
        try:
            result = generate_and_record(
                store,
                agent_input,
                provider,
                workflow_run_id=state.get("workflow_run_id"),
                shop_lead_id=state.get("lead_id"),
            )
        except ProviderResponseError as exc:
            return failed(str(exc))

        output = result.output
        if forced_route and output.route != forced_route:
            return failed(f"模型没有按人工选定的路线（{forced_route}）生成草稿，请人工撰写")
        gated = result.provider == POLICY_GATE_PROVIDER
        return {
            "model_status": "policy_rejected" if gated else "succeeded",
            "model_route": output.route,
            "model_route_reason": output.route_reason,
            "model_summary": output.shop_business_summary,
            "model_points": [point.model_dump() for point in output.personalization_points],
            "model_product_match": output.product_match,
            "model_draft": output.draft,
            "model_uncertainties": output.uncertainties,
            "model_quality_flags": list(output.quality_flags),
            "model_meta": {
                "provider": result.provider,
                "model": result.model,
                "prompt_version": result.prompt_version,
                "latency_ms": result.latency_ms,
            },
            "suggested_channel": ROUTE_TO_CHANNEL[output.route],
            "evidence": [f"{item.evidence_id}: {item.text}" for item in agent_input.evidence],
        }

    return suggest


def generate_and_record(
    store: RunStore,
    agent_input: LeadAgentInput,
    provider: LLMProvider,
    *,
    workflow_run_id: str | None = None,
    shop_lead_id: str | None = None,
    source: str = "production",
) -> GenerationResult:
    """Run the shared generation entry and persist one record per attempt.

    Failures are recorded before re-raising, so the caller can move the task
    into a failed / needs-human state with a traceable cause.
    """
    common = {
        "workflow_run_id": workflow_run_id,
        "shop_lead_id": shop_lead_id,
        "source": source,
        "case_id": agent_input.case_id,
        "input_json": agent_input.model_dump_json(),
        "evidence_ids_json": json.dumps(
            [item.evidence_id for item in agent_input.evidence], ensure_ascii=False
        ),
    }
    try:
        result = generate_lead_suggestion(agent_input, provider)
    except ProviderResponseError as exc:
        cause = exc.__cause__
        store.record_model_invocation(
            **common,
            status="failed",
            provider=provider.provider_name,
            model=provider.model_name,
            prompt_version=PROMPT_VERSION,
            prompt_sha256=system_prompt_sha256(),
            error_type=type(cause).__name__ if cause else type(exc).__name__,
            error_message=str(exc),
        )
        raise

    gated = result.provider == POLICY_GATE_PROVIDER
    store.record_model_invocation(
        **common,
        status="policy_rejected" if gated else "succeeded",
        provider=result.provider,
        model=result.model,
        prompt_version=None if gated else result.prompt_version,
        prompt_sha256=None if gated else system_prompt_sha256(),
        output_json=result.output.model_dump_json(),
        request_id=result.request_id,
        usage_json=json.dumps(result.usage, ensure_ascii=False),
        latency_ms=result.latency_ms,
    )
    return result
