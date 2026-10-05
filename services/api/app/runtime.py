"""Wiring: database connections, model providers and the two workflow graphs. Built once, here."""

from __future__ import annotations

import os
import sqlite3
from threading import Lock
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .storage import RunStore


PROJECT_ROOT = Path(__file__).resolve().parents[3]

from packages.workflows.lead_workflow import build_graph
from packages.agent_core.providers import provider_from_env
from packages.agent_core.style import first_message_style_issues
from services.worker.lead_generation import audience_for
from services.worker.lead_generation import build_suggester, load_product_facts
from packages.agent_core.screening import (
    today_in_shanghai,
)
from packages.agent_core.vision import shop_extractor_from_env
from services.worker.screening import make_read_shop
from packages.workflows.screening_workflow import build_batch_graph


DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RUN_DB_PATH = Path(os.getenv("AGENT_RUN_DB_PATH", str(DATA_DIR / "agent_ops.db")))
CHECKPOINT_DB_PATH = Path(
    os.getenv("AGENT_CHECKPOINT_DB_PATH", str(DATA_DIR / "agent_checkpoints.sqlite"))
)
checkpoint_connection = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)
checkpointer = SqliteSaver(checkpoint_connection)
checkpointer.setup()
store = RunStore(RUN_DB_PATH)
llm_provider = provider_from_env()
def check_first_message(state: dict[str, Any]) -> list[str]:
    """Structure checks for a first cold DM only; comments and replies follow other rules."""
    if state.get("channel") != "dm" or state.get("contact_basis", "cold_outreach") != "cold_outreach":
        return []
    return first_message_style_issues(state.get("draft") or "", audience_for(state))


graph = build_graph(
    checkpointer=checkpointer,
    suggester=(suggester := build_suggester(store, llm_provider, load_product_facts())),
    draft_checker=check_first_message,
)
graph_lock = Lock()
shop_extractor = shop_extractor_from_env()
read_shop = make_read_shop(store, shop_extractor)
batch_graph = build_batch_graph(
    read_shop,
    lambda name, xhs_id="": store.is_do_not_contact(name, None, xhs_id),
    today_in_shanghai,
    checkpointer=checkpointer,
)
batch_lock = Lock()
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
REVIEW_TIMEOUT_HOURS = float(os.getenv("AGENT_REVIEW_TIMEOUT_HOURS", "48"))
