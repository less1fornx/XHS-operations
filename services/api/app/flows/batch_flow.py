"""Shared logic for screening batches: payloads and turning approved shops into runs."""

from __future__ import annotations

from typing import Any

from langgraph.types import Command


from ..runtime import (  # noqa: E402
    store,
    graph,
    graph_lock,
)
from packages.agent_core.screening import (  # noqa: E402
    ShopExtraction,
    ShopScreening,
    to_lead_context,
)
from ..flows.run_flow import (  # noqa: E402
    RunCreate,
    config_for,
    save_result,
    start_run,
)


def batch_config(batch_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": f"batch-{batch_id}"}}


def batch_payload(batch_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": batch_id,
        "status": state.get("status", "processing"),
        "created_at": state.get("created_at"),
        "dm_slots": state.get("dm_slots"),
        "comment_slots": state.get("comment_slots"),
        "results": state.get("results", []),
        "ranked": state.get("ranked", []),
        "review": state.get("review"),
        "drafts": state.get("drafts", []),
        "inbox": state.get("inbox"),
    }


def draft_selected_shops(batch_id: str, selected: list[tuple[dict[str, Any], str]]) -> None:
    """Hand each approved shop to the existing single-shop workflow, route preset."""
    for item, route in selected:
        extraction = ShopExtraction.model_validate(item["extraction"])
        screening = ShopScreening.model_validate(item["screening"])
        context = to_lead_context(extraction, screening)
        basis = "批量初筛入选（{}）：{}".format(
            screening.rules_version, "；".join(screening.tier_reasons)
        )
        if screening.content_angles:
            basis += "。建议切入点：" + "；".join(screening.content_angles)
        record = {"shop_key": item["shop_key"], "shop_name": extraction.shop_name, "route": route}
        try:
            run = start_run(
                RunCreate(
                    **context,
                    profile_url=item.get("profile_url") or None,
                    contact_basis="cold_outreach",
                    contact_basis_note=basis,
                    screening_snapshot={**screening.model_dump(), "account_kind": extraction.account_kind},
                )
            )
            with graph_lock:
                state = graph.invoke(Command(resume={"requested_channel": route}), config_for(run["id"]))
            save_result(run["id"], state, "route_decided", before_state=run["state"])
            record.update(run_id=run["id"], status="drafted")
        except Exception as exc:  # one shop failing must not stop the others
            record.update(status="failed", error=str(exc))
        payload = store.get_batch(batch_id) or {}
        payload["drafts"] = [*payload.get("drafts", []), record]
        if len(payload["drafts"]) == len(selected):
            payload["status"] = "drafted"
        store.save_batch(batch_id, payload.get("status", "drafting"), payload)
