from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, select

from packages.agent_core.contracts import LeadAgentInput
from packages.agent_core.evaluation import load_cases
from packages.agent_core.prompting import PROMPT_VERSION, PromptBundle
from packages.agent_core.providers import (
    GenerationResult,
    MockProvider,
    ProviderResponseError,
)
from services.api.app.models import ModelInvocation
from services.api.app.storage import RunStore
from services.worker.lead_generation import generate_and_record


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FailingProvider:
    provider_name = "failing"
    model_name = "failing-v1"

    def generate(
        self, agent_input: LeadAgentInput, prompt: PromptBundle
    ) -> GenerationResult:
        try:
            raise TimeoutError("upstream timed out")
        except TimeoutError as exc:
            raise ProviderResponseError("模拟模型调用失败") from exc


def first_case() -> LeadAgentInput:
    return load_cases(PROJECT_ROOT / "evals" / "phase2_cases.v1.json")[0].input


def make_store(tmp_path) -> RunStore:
    store = RunStore(tmp_path / "test.db")
    SQLModel.metadata.create_all(store.engine)
    return store


def only_record(store: RunStore) -> ModelInvocation:
    with Session(store.engine) as session:
        return session.exec(select(ModelInvocation)).one()


def test_successful_generation_is_recorded(tmp_path) -> None:
    store = make_store(tmp_path)
    agent_input = first_case()

    result = generate_and_record(
        store, agent_input, MockProvider(), workflow_run_id="run-1", shop_lead_id="lead-1"
    )

    record = only_record(store)
    assert record.status == "succeeded"
    assert record.provider == "mock"
    assert record.workflow_run_id == "run-1"
    assert record.shop_lead_id == "lead-1"
    assert record.prompt_version == PROMPT_VERSION
    assert len(record.prompt_sha256) == 64
    assert json.loads(record.evidence_ids_json) == ["e1", "e2"]
    assert json.loads(record.output_json)["route"] == result.output.route


def test_policy_gate_reject_is_recorded_without_prompt(tmp_path) -> None:
    store = make_store(tmp_path)
    agent_input = first_case().model_copy(update={"prior_outcome": "blocked"})

    generate_and_record(store, agent_input, MockProvider())

    record = only_record(store)
    assert record.status == "policy_rejected"
    assert record.provider == "policy_gate"
    assert record.prompt_version is None
    assert json.loads(record.output_json)["route"] == "reject"


def test_provider_failure_is_recorded_then_raised(tmp_path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(ProviderResponseError):
        generate_and_record(store, first_case(), FailingProvider())

    record = only_record(store)
    assert record.status == "failed"
    assert record.error_type == "TimeoutError"
    assert record.output_json is None
