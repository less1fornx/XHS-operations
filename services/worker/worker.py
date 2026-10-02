from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from services.api.app.storage import RunStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "agent_ops.db"


def run_once(store: RunStore) -> list[dict[str, object]]:
    reminders = store.claim_due_reminders()
    for reminder in reminders:
        print(
            json.dumps(
                {"event": "reminder_ready", "reminder": reminder},
                ensure_ascii=False,
            ),
            flush=True,
        )
    return reminders


def main() -> None:
    parser = argparse.ArgumentParser(description="提醒人工打开官方页面并记录发送链路")
    parser.add_argument("--once", action="store_true", help="只执行一次轮询")
    parser.add_argument("--interval", type=int, default=30, help="轮询间隔秒数")
    args = parser.parse_args()

    store = RunStore(Path(os.getenv("AGENT_RUN_DB_PATH", str(DEFAULT_DB))))
    if args.once:
        run_once(store)
        return
    while True:
        run_once(store)
        time.sleep(max(args.interval, 5))


if __name__ == "__main__":
    main()
