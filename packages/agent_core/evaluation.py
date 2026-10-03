from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import LeadAgentInput, validate_evidence_refs
from .pipeline import POLICY_GATE_PROVIDER, generate_lead_suggestion
from .providers import LLMProvider, ProviderResponseError


class ExpectedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: LeadAgentInput
    expected: ExpectedResult


@dataclass(frozen=True)
class EvaluationReport:
    summary: dict[str, Any]
    cases: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "cases": self.cases}


def load_cases(path: Path) -> list[EvaluationCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [EvaluationCase.model_validate(item) for item in raw]


def run_evaluation(
    cases: list[EvaluationCase], provider: LLMProvider
) -> EvaluationReport:
    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            generation = generate_lead_suggestion(case.input, provider)
        except ProviderResponseError as exc:
            # One failed model call must not abort the whole evaluation run.
            results.append(
                {
                    "case_id": case.input.case_id,
                    "passed": False,
                    "route_correct": False,
                    "expected_route": case.expected.route,
                    "actual_route": None,
                    "missing_required": [],
                    "forbidden_hits": [],
                    "invalid_evidence_refs": [],
                    "provider": provider.provider_name,
                    "model": provider.model_name,
                    "prompt_version": None,
                    "latency_ms": None,
                    "output": None,
                    "error": str(exc),
                }
            )
            continue
        output = generation.output
        normalized_draft = output.draft.casefold()
        missing_required = [
            item for item in case.expected.must_include if item.casefold() not in normalized_draft
        ]
        forbidden_hits = [
            item for item in case.expected.must_not_include if item.casefold() in normalized_draft
        ]
        invalid_refs = validate_evidence_refs(case.input, output)
        route_correct = output.route == case.expected.route
        passed = route_correct and not missing_required and not forbidden_hits and not invalid_refs
        results.append(
            {
                "case_id": case.input.case_id,
                "passed": passed,
                "route_correct": route_correct,
                "expected_route": case.expected.route,
                "actual_route": output.route,
                "missing_required": missing_required,
                "forbidden_hits": forbidden_hits,
                "invalid_evidence_refs": invalid_refs,
                "provider": generation.provider,
                "model": generation.model,
                "prompt_version": generation.prompt_version,
                "latency_ms": generation.latency_ms,
                "output": output.model_dump(mode="json"),
                "error": None,
            }
        )
    total = len(results)
    passed_count = sum(1 for item in results if item["passed"])
    route_correct_count = sum(1 for item in results if item["route_correct"])
    summary = {
        "total": total,
        "passed": passed_count,
        "pass_rate": passed_count / total if total else 0,
        "route_accuracy": route_correct_count / total if total else 0,
        "provider": provider.provider_name,
        "model": provider.model_name,
        "policy_gate_rejects": sum(
            1 for item in results if item["provider"] == POLICY_GATE_PROVIDER
        ),
        "provider_errors": sum(1 for item in results if item["error"]),
        "missing_required_cases": sum(1 for item in results if item["missing_required"]),
        "forbidden_hit_cases": sum(1 for item in results if item["forbidden_hits"]),
        "invalid_ref_cases": sum(1 for item in results if item["invalid_evidence_refs"]),
    }
    return EvaluationReport(summary=summary, cases=results)
