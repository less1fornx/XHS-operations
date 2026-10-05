from __future__ import annotations

import json

from langgraph.types import Command
from sqlmodel import Session, SQLModel, select

from packages.agent_core.contracts import LeadAgentInput
from packages.agent_core.prompting import PromptBundle
from packages.agent_core.providers import GenerationResult, MockProvider, ProviderResponseError
from phase0_graph.lead_workflow import build_graph
from services.api.app.models import ModelInvocation
from services.api.app.storage import RunStore
from services.worker.lead_generation import build_agent_input, build_suggester, load_product_facts


class FailingProvider:
    provider_name = "failing"
    model_name = "failing-v1"

    def generate(
        self, agent_input: LeadAgentInput, prompt: PromptBundle
    ) -> GenerationResult:
        raise ProviderResponseError("模拟模型调用失败")


def initial_state(run_id: str, **overrides) -> dict:
    return {
        "lead_id": "lead-1",
        "evidence_id": "evidence-1",
        "task_id": "task-1",
        "workflow_run_id": run_id,
        "shop_name": "示例西服定制",
        "contact_basis": "cold_outreach",
        "contact_basis_note": "",
        "video_text": "春夏轻薄西装和客户第一次试衣",
        "shop_type": "suit_custom",
        "shop_note": "主营商务与婚礼西装定制",
        "trace": [],
        "route_review_status": "not_started",
        "content_review_status": "not_started",
        "task_status": "new",
        **overrides,
    }


def make_graph(tmp_path, provider):
    store = RunStore(tmp_path / "test.db")
    SQLModel.metadata.create_all(store.engine)
    graph = build_graph(suggester=build_suggester(store, provider, load_product_facts()))
    return store, graph


def config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def test_model_suggestion_pauses_for_route_review_and_is_recorded(tmp_path) -> None:
    store, graph = make_graph(tmp_path, MockProvider())

    first = graph.invoke(initial_state("run-dm"), config("run-dm"))

    assert first["model_status"] == "succeeded"
    assert first["suggested_channel"] == "dm"
    assert first["__interrupt__"][0].value["type"] == "route_review"
    with Session(store.engine) as session:
        record = session.exec(select(ModelInvocation)).one()
    assert record.workflow_run_id == "run-dm"
    assert record.shop_lead_id == "lead-1"
    assert json.loads(record.evidence_ids_json) == ["e1", "e2"]


def test_accepting_model_route_uses_model_draft(tmp_path) -> None:
    _, graph = make_graph(tmp_path, MockProvider())
    first = graph.invoke(initial_state("run-accept"), config("run-accept"))

    second = graph.invoke(Command(resume={"requested_channel": "dm"}), config("run-accept"))

    assert second["draft"] == first["model_draft"]
    assert "内搭" in second["draft"]
    assert second["__interrupt__"][0].value["type"] == "content_review"


def test_overriding_model_route_regenerates_draft_for_that_route(tmp_path) -> None:
    store, graph = make_graph(tmp_path, MockProvider())
    first = graph.invoke(initial_state("run-override"), config("run-override"))
    assert first["suggested_channel"] == "dm"

    second = graph.invoke(Command(resume={"requested_channel": "comment"}), config("run-override"))

    assert second["channel"] == "comment"
    assert second["model_route"] == "comment"
    assert second["suggested_channel"] == "dm"
    assert second["draft"] and "一件起订" not in second["draft"]
    assert second["__interrupt__"][0].value["type"] == "content_review"
    with Session(store.engine) as session:
        records = session.exec(select(ModelInvocation)).all()
    assert len(records) == 2
    assert json.loads(records[1].input_json)["human_selected_route"] == "comment"


def test_failed_redraft_leaves_empty_draft_for_human(tmp_path) -> None:
    _, graph = make_graph(tmp_path, FailingProvider())
    graph.invoke(initial_state("run-redraft-fail"), config("run-redraft-fail"))

    second = graph.invoke(Command(resume={"requested_channel": "dm"}), config("run-redraft-fail"))

    assert second["channel"] == "dm"
    assert second["draft"] == ""
    assert second["__interrupt__"][0].value["type"] == "content_review"


def test_model_failure_still_reaches_human_route_review(tmp_path) -> None:
    store, graph = make_graph(tmp_path, FailingProvider())

    first = graph.invoke(initial_state("run-fail"), config("run-fail"))

    assert first["model_status"] == "failed"
    assert first["suggested_channel"] == "none"
    assert first["__interrupt__"][0].value["type"] == "route_review"
    with Session(store.engine) as session:
        assert session.exec(select(ModelInvocation)).one().status == "failed"


def test_prior_conversation_maps_to_warm_contact() -> None:
    state = initial_state(
        "run-warm", contact_basis="prior_conversation", contact_basis_note="对方回复可以看看款式"
    )

    agent_input = build_agent_input(state, load_product_facts())

    assert agent_input.contact_mode == "warm"
    assert agent_input.prior_outcome == "replied"
    assert [item.evidence_id for item in agent_input.evidence] == ["e1", "e2", "e3"]


def test_old_runs_get_audience_from_the_screening_snapshot() -> None:
    from services.worker.lead_generation import build_agent_input

    state = {"shop_name": "店", "video_text": "内容", "screening_snapshot": {"account_kind": "personal"}}
    assert build_agent_input(state, ["事实"]).audience == "personal_shop"
    assert build_agent_input({**state, "audience": "store_account"}, ["事实"]).audience == "store_account"
    assert build_agent_input({"shop_name": "店", "video_text": "内容"}, ["事实"]).audience == "unknown"


def _loop_graph(drafts: list[str]):
    """A graph whose model returns the given drafts in order and whose check dislikes '需求'."""
    from langgraph.checkpoint.memory import MemorySaver
    from phase0_graph.lead_workflow import build_graph

    calls: list[dict | None] = []

    def suggester(state, forced_route=None, revision=None):
        calls.append(revision)
        draft = drafts[min(len(calls) - 1, len(drafts) - 1)]
        return {"model_status": "succeeded", "model_route": "dm", "suggested_channel": "dm", "model_draft": draft}

    checker = lambda state: ["结尾在问需求"] if "需求" in state["draft"] else []  # noqa: E731
    return build_graph(MemorySaver(), suggester, checker), calls


def test_bad_draft_is_rewritten_before_the_human_sees_it() -> None:
    graph, calls = _loop_graph(["是否有需求？", "你们一般怎么搭？"])
    graph.invoke(initial_state("loop-1"), config("loop-1"))
    state = graph.invoke(Command(resume={"requested_channel": "dm"}), config("loop-1"))

    assert state["draft"] == "你们一般怎么搭？" and state["draft_issues"] == []
    assert state["redraft_count"] == 1
    assert calls[1] == {"previous_draft": "是否有需求？", "issues": ["结尾在问需求"]}
    assert state["__interrupt__"][0].value["type"] == "content_review"


def test_redraft_loop_stops_after_two_attempts() -> None:
    graph, calls = _loop_graph(["是否有需求？"])
    graph.invoke(initial_state("loop-2"), config("loop-2"))
    state = graph.invoke(Command(resume={"requested_channel": "dm"}), config("loop-2"))

    assert state["redraft_count"] == 2 and len(calls) == 3
    assert state["draft_issues"] == ["结尾在问需求"]
    assert state["__interrupt__"][0].value["type"] == "content_review"
