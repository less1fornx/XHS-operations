from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, select

from services.api.app.models import AuditLog, RunSnapshot
from services.api.app.storage import BusinessRuleError, RunStore


def make_store(tmp_path) -> RunStore:
    store = RunStore(tmp_path / "test.db")
    SQLModel.metadata.create_all(store.engine)
    return store


def save_run(store: RunStore, run_id: str, stage: str) -> None:
    review = stage in ("route_review", "content_review")
    state = {
        "lead_id": f"lead-{run_id}",
        "evidence_id": f"evidence-{run_id}",
        "task_id": f"task-{run_id}",
        "shop_name": f"示例店铺-{run_id}",
        "video_text": "示例内容",
        "task_status": "new" if review else stage,
        "trace": [],
    }
    store.save(
        run_id,
        {"id": run_id, "stage": stage, "interrupt": {"type": stage} if review else None, "state": state},
        "run_created",
    )


def age_run(store: RunStore, run_id: str, hours: float) -> None:
    with Session(store.engine) as session:
        snapshot = session.get(RunSnapshot, run_id)
        snapshot.updated_at = datetime.now(timezone.utc) - timedelta(hours=hours)
        session.commit()


def test_human_can_end_a_run_waiting_for_review(tmp_path) -> None:
    store = make_store(tmp_path)
    save_run(store, "run-1", "route_review")

    payload = store.end_run("run-1", "人工结束任务")

    assert payload["stage"] == "blocked"
    assert payload["interrupt"] is None
    assert payload["state"]["result_note"] == "人工结束任务"
    assert store.get("run-1")["stage"] == "blocked"
    with Session(store.engine) as session:
        actions = [row.action for row in session.exec(select(AuditLog)).all()]
    assert "run_ended_by_human" in actions


def test_finished_run_cannot_be_ended_again(tmp_path) -> None:
    store = make_store(tmp_path)
    save_run(store, "run-1", "route_review")
    store.end_run("run-1", "人工结束任务")

    with pytest.raises(BusinessRuleError):
        store.end_run("run-1", "人工结束任务")


def test_only_stale_review_runs_expire(tmp_path) -> None:
    store = make_store(tmp_path)
    save_run(store, "stale", "route_review")
    save_run(store, "fresh", "content_review")
    save_run(store, "queued", "scheduled")
    age_run(store, "stale", 49)
    age_run(store, "queued", 49)

    expired = store.expire_stale_reviews(48)

    assert expired == ["stale"]
    assert store.get("stale")["stage"] == "blocked"
    assert "自动结束" in store.get("stale")["state"]["result_note"]
    assert store.get("fresh")["stage"] == "content_review"
    assert store.get("queued")["stage"] == "scheduled"


def test_ending_a_run_keeps_the_reasons(tmp_path) -> None:
    store = make_store(tmp_path)
    save_run(store, "run-1", "content_review")

    payload = store.end_run("run-1", "不联系：连锁太大，门店做不了主", reasons=["chain_hq"], note="总部在广州")

    feedback = payload["state"]["end_feedback"]
    assert feedback["stage"] == "content_review" and feedback["reasons"] == ["chain_hq"]
    assert feedback["note"] == "总部在广州"
    with Session(store.engine) as session:
        audit = session.exec(select(AuditLog).where(AuditLog.action == "run_ended_by_human")).one()
    assert json.loads(audit.after_json)["reasons"] == ["chain_hq"]


def test_reason_summary_counts_route_stage_endings_and_review_time_edits(tmp_path) -> None:
    # Found by the browser walk-through 2026-10-05: reasons given at the route stage
    # (before any task row exists) and edits made during review were missing from the summary.
    from services.worker.status_report import collect

    store = make_store(tmp_path)
    save_run(store, "run-1", "route_review")
    store.end_run("run-1", "不联系", reasons=["not_target"])
    save_run(store, "run-2", "content_review")
    with Session(store.engine) as session:
        snapshot = session.get(RunSnapshot, "run-2")
        payload = json.loads(snapshot.payload_json)
        payload["state"]["draft_edits"] = [{"reasons": ["too_long", "too_generic"]}]
        snapshot.payload_json = json.dumps(payload, ensure_ascii=False)
        session.commit()

    stats = collect(tmp_path / "test.db", tmp_path / "no-cases.json")

    assert stats["end_reasons"] == {"not_target": 1}
    assert stats["edit_reasons"] == {"too_long": 1, "too_generic": 1}
