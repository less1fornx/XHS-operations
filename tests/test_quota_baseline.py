from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, select

from services.api.app.models import AuditLog, PlatformAccount
from services.api.app.storage import RunStore
from services.api.app.storage.base import BusinessRuleError
from services.api.app.storage.quota import BASELINE, CEILING, DEGRADED_BASELINE

OURS = "我们是做西装内搭的毛衫工厂，注重纱线和做工，想和贵店合作西装内搭，方便的话发几款给您看看。"


def make_store(tmp_path) -> RunStore:
    store = RunStore(tmp_path / "test.db")
    SQLModel.metadata.create_all(store.engine)
    return store


def review_task(store: RunStore, index: int) -> tuple[str, str, str]:
    """One shop waiting for its draft to be approved. The second half of every draft is the same."""
    run_id, draft = f"run-{index}", f"贵店第{index}条笔记里的面料。{OURS}"
    state = {
        "lead_id": f"lead-{index}", "evidence_id": f"evidence-{index}", "task_id": f"task-{index}",
        "shop_name": f"店{index}", "video_text": "内容", "channel": "dm", "draft": draft,
        "shop_type": "suit_custom", "task_status": "content_review", "trace": [],
    }
    store.save(run_id, {"id": run_id, "stage": "content_review", "interrupt": None, "state": state}, "draft_ready")
    return run_id, state["task_id"], draft


def fill(store: RunStore, count: int, start: int = 0) -> None:
    for index in range(start, start + count):
        run_id, task_id, draft = review_task(store, index)
        store.reserve_quota(run_id, task_id, "dm", draft, confirm_over_baseline=True)


def test_past_the_baseline_the_reviewer_is_asked_and_may_go_on(tmp_path) -> None:
    store = make_store(tmp_path)
    fill(store, BASELINE["dm"])
    run_id, task_id, draft = review_task(store, 100)

    with pytest.raises(BusinessRuleError) as refused:
        store.reserve_quota(run_id, task_id, "dm", draft)
    assert refused.value.code == "over_baseline"
    message = refused.value.message
    assert f"第 {BASELINE['dm'] + 1} 条" in message and f"基线 {BASELINE['dm']} 条" in message
    assert "% 的内容和今天已排的" in message  # the shared second half is reported
    assert store.account_summary()["dm"]["used"] == BASELINE["dm"]  # nothing was reserved

    summary = store.reserve_quota(run_id, task_id, "dm", draft, confirm_over_baseline=True)
    assert summary["dm"] == {"used": BASELINE["dm"] + 1, "limit": BASELINE["dm"], "ceiling": CEILING["dm"]}
    with Session(store.engine) as session:
        logged = session.exec(
            select(AuditLog).where(AuditLog.action == "quota_reserved", AuditLog.entity_id == task_id)
        ).one()
    record = json.loads(logged.after_json)
    assert record["over_baseline_confirmed"] is True and record["similarity_to_today"] > 0.5


def test_below_the_baseline_nothing_is_asked_and_the_ceiling_cannot_be_confirmed_away(tmp_path) -> None:
    store = make_store(tmp_path)
    run_id, task_id, draft = review_task(store, 0)
    store.reserve_quota(run_id, task_id, "dm", draft)  # first of the day: no question

    fill(store, CEILING["dm"] - 1, start=1)
    run_id, task_id, draft = review_task(store, 100)
    with pytest.raises(BusinessRuleError) as refused:
        store.reserve_quota(run_id, task_id, "dm", draft, confirm_over_baseline=True)
    assert refused.value.code == "quota_exceeded"


def test_a_degraded_account_is_reminded_earlier_but_not_stopped(tmp_path) -> None:
    store = make_store(tmp_path)
    fill(store, DEGRADED_BASELINE["dm"])
    with Session(store.engine) as session:
        account = session.exec(select(PlatformAccount)).one()
        account.status = "degraded"
        session.add(account)
        session.commit()

    run_id, task_id, draft = review_task(store, 100)
    with pytest.raises(BusinessRuleError) as refused:
        store.reserve_quota(run_id, task_id, "dm", draft)
    assert refused.value.code == "over_baseline" and "降级" in refused.value.message
    store.reserve_quota(run_id, task_id, "dm", draft, confirm_over_baseline=True)
    assert store.account_summary()["dm"]["used"] == DEGRADED_BASELINE["dm"] + 1
