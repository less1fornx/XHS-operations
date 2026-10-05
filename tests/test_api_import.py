from __future__ import annotations

import importlib
import sys


def _forget_api_modules() -> None:
    """The wiring is built at import time, so drop it to rebuild against this test's paths."""
    for name in [n for n in sys.modules if n.startswith("services.api.app.") and ".storage" not in n and not n.endswith(".models")]:
        sys.modules.pop(name)


def test_backend_loads_and_graph_has_the_redraft_loop(tmp_path, monkeypatch) -> None:
    # Loading the whole backend catches wiring mistakes unit tests cannot see
    # (2026-10-05: a function was used above the line that defined it).
    monkeypatch.setenv("AGENT_RUN_DB_PATH", str(tmp_path / "ops.db"))
    monkeypatch.setenv("AGENT_CHECKPOINT_DB_PATH", str(tmp_path / "ck.sqlite"))
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AGENT_ERROR_LOG", str(tmp_path / "errors.log"))
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "mock")
    _forget_api_modules()

    main = importlib.import_module("services.api.app.main")

    nodes = set(main.graph.get_graph().nodes)
    assert {"draft_message", "check_draft", "redraft", "policy_gate", "content_review"} <= nodes
    assert main.check_first_message({"channel": "comment", "draft": "是否有需求？"}) == []
    assert main.check_first_message({"channel": "dm", "draft": "我们品质有保障，一件起订。是否有需求？"})
    _forget_api_modules()
