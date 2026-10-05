from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from sqlmodel import SQLModel

from services.api.app.storage import RunStore
from services.worker.backup import backup_if_due
from services.worker.status_report import collect, render


def test_backup_runs_once_per_day_and_keeps_recent_copies(tmp_path: Path) -> None:
    source = tmp_path / "agent_ops.db"
    with sqlite3.connect(source) as conn:
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('一件起订')")
    backups = tmp_path / "backups"
    start = date(2026, 10, 1)

    assert [p.name for p in backup_if_due(tmp_path, today=start, keep=3)] == ["agent_ops-20261001.db"]
    assert backup_if_due(tmp_path, today=start, keep=3) == []
    for offset in range(1, 5):
        backup_if_due(tmp_path, today=start + timedelta(days=offset), keep=3)

    assert sorted(p.name for p in backups.iterdir()) == [
        "agent_ops-20261003.db", "agent_ops-20261004.db", "agent_ops-20261005.db",
    ]
    with sqlite3.connect(backups / "agent_ops-20261005.db") as copy:
        assert copy.execute("select v from t").fetchone() == ("一件起订",)


def test_status_report_counts_real_records(tmp_path: Path) -> None:
    db = tmp_path / "ops.db"
    store = RunStore(db)
    SQLModel.metadata.create_all(store.engine)
    store.save_batch("b1", "drafted", {
        "results": [
            {"shop_key": "shop_0", "status": "ok", "extraction": {"xhs_id": "abc12345"}},
            {"shop_key": "shop_1", "status": "ok", "extraction": {"xhs_id": ""}},
            {"shop_key": "shop_2", "status": "failed", "error": "x"},
        ],
        "corrections": [{"shop_key": "shop_1"}],
        "review": {"decisions": [
            {"shop_key": "shop_0", "tier_feedback": "agree"},
            {"shop_key": "shop_1", "tier_feedback": "disagree"},
        ]},
    })
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([
        {"input": {"prior_outcome": "none"}}, {"input": {"prior_outcome": "blocked"}},
    ]), encoding="utf-8")

    stats = collect(db, cases)

    assert stats["read"] == {
        "shops_read": 2, "shops_failed": 1, "with_xhs_id": 1, "corrected": 1, "xhs_id_rate": 0.5,
    }
    assert stats["tier"]["tier_agree_rate"] == 0.5
    assert stats["send"]["sent"] == 0 and stats["reply"]["reply_rate"] is None
    assert stats["eval"] == {"cases": 2, "model_cases": 1}
    assert "样本不足（2/30）" in render(stats)
