from __future__ import annotations

from datetime import datetime, timezone

from langgraph.types import Command
from sqlmodel import Session, SQLModel

from packages.workflows.lead_workflow import build_graph
from services.api.app.models import OutreachTask, ShopLead, WorkflowRun
from services.api.app.storage import DEFAULT_ACCOUNT_ID, RunStore


def test_cold_b2b_dm_reaches_human_content_review() -> None:
    graph = build_graph()
    config = {"configurable": {"thread_id": "cold-dm-test"}}
    initial = {
        "lead_id": "lead-1",
        "evidence_id": "evidence-1",
        "task_id": "task-1",
        "workflow_run_id": "cold-dm-test",
        "shop_name": "示例西服定制",
        "contact_basis": "cold_outreach",
        "contact_basis_note": "视频讨论春夏轻薄西装和客户试衣",
        "video_text": "春夏西装定制面料、版型和客户试穿案例",
        "trace": [],
        "route_review_status": "not_started",
        "content_review_status": "not_started",
        "task_status": "new",
    }

    first = graph.invoke(initial, config)
    assert first["suggested_channel"] == "dm"
    second = graph.invoke(Command(resume={"requested_channel": "dm"}), config)

    assert second["channel"] == "dm"
    assert second["risk_flags"] == []
    assert second["__interrupt__"][0].value["type"] == "content_review"


def test_outcome_feedback_import_is_upserted(tmp_path) -> None:
    store = RunStore(tmp_path / "test.db")
    SQLModel.metadata.create_all(store.engine)
    now = datetime.now(timezone.utc)

    with Session(store.engine) as session:
        session.add(
            ShopLead(
                id="lead-1",
                platform_account_id=DEFAULT_ACCOUNT_ID,
                display_name="示例西服定制",
            )
        )
        session.add(
            WorkflowRun(
                id="run-1",
                shop_lead_id="lead-1",
                thread_id="run-1",
            )
        )
        session.add(
            OutreachTask(
                id="task-1",
                shop_lead_id="lead-1",
                platform_account_id=DEFAULT_ACCOUNT_ID,
                workflow_run_id="run-1",
                channel="dm",
                status="sent",
                idempotency_key="run-1:dm",
                sent_at=now,
            )
        )
        session.commit()

    first = store.import_outcomes(
        [
            {
                "run_id": "run-1",
                "reply_status": "replied",
                "intent_stage": "needs_details",
                "conversion_status": "open",
                "need_tags": ["针织内搭"],
                "attraction_tags": ["一件起订"],
                "objection_tags": [],
                "human_notes": "对方希望先看产品资料",
            }
        ]
    )
    second = store.import_outcomes(
        [
            {
                "run_id": "run-1",
                "reply_status": "replied",
                "intent_stage": "quotation",
                "conversion_status": "won",
                "converted_at": now,
                "need_tags": ["针织内搭"],
                "attraction_tags": ["一件起订", "产品匹配"],
                "objection_tags": [],
                "human_notes": "已成交",
            }
        ]
    )

    assert first[0]["id"] == second[0]["id"]
    assert second[0]["conversion_status"] == "won"
    assert second[0]["attraction_tags"] == ["一件起订", "产品匹配"]
    assert len(store.list_outcomes()) == 1
