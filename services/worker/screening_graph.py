"""Batch screening workflow (Phase 2C).

Shape, as designed with the user on 2026-10-03:

    START --Send x N--> read_shop  (one branch per shop, run in parallel)
                           |        results merge into one list (operator.add)
                           v
                         rank       (deterministic rules v0, today's quota)
                           |
                           v
                     batch_review   (interrupt: the human approves the list)
                           |
                          END

User decisions encoded here:
- #1 read every shop first, compare only at the end; each shop is read on its own.
- #2 a failed shop is skipped and reported with its reason; the batch continues.
- #3 the model reports what it sees; the program ranks with versioned rules.
- #4 one human checkpoint for the whole batch, not one per shop.
"""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, Callable, TypedDict

import httpx
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from packages.agent_core.screening import (
    ShopExtraction,
    assign_routes,
    rank_key,
    screen_shop,
)


class ShopInput(TypedDict):
    batch_id: str
    shop_key: str
    image_paths: list[str]


class BatchState(TypedDict, total=False):
    batch_id: str
    shops: list[ShopInput]
    dm_slots: int
    comment_slots: int
    # Each parallel branch appends one entry; operator.add merges the branches.
    results: Annotated[list[dict[str, Any]], operator.add]
    ranked: list[dict[str, Any]]
    review: dict[str, Any]
    status: str


ReadShop = Callable[[ShopInput], ShopExtraction]


def classify_error(exc: Exception) -> str:
    """system: network or service trouble, retrying may help.
    unreadable: the screenshots themselves could not be turned into facts."""
    return "system" if isinstance(exc.__cause__, httpx.HTTPError) else "unreadable"


def rank_results(
    results: list[dict[str, Any]],
    today: date,
    is_do_not_contact: Callable[[str], bool],
    dm_slots: int,
    comment_slots: int,
) -> list[dict[str, Any]]:
    """Tier, order and route every readable shop. Also used after a human correction."""
    seen: set[str] = set()
    scored: list[tuple[tuple, dict[str, Any], Any]] = []
    for result in sorted(results, key=lambda item: item["shop_key"]):
        if result["status"] != "read":
            continue
        extraction = ShopExtraction.model_validate(result["extraction"])
        screening = screen_shop(extraction, today)
        name = extraction.shop_name.strip()
        # Deterministic checks the model never gets a say in.
        if is_do_not_contact(name):
            screening.tier = "excluded"
            screening.tier_reasons = ["已在勿扰名单中"]
        elif name in seen:
            screening.tier = "excluded"
            screening.tier_reasons = ["本批中重复上传的店铺"]
        seen.add(name)
        scored.append((rank_key(screening, extraction), result, screening))

    scored.sort(key=lambda item: item[0])
    assign_routes([item[2] for item in scored], dm_slots, comment_slots)
    return [
        {**result, "screening": screening.model_dump(), "rank": index + 1}
        for index, (_, result, screening) in enumerate(scored)
    ]


def build_batch_graph(
    read_shop_fn: ReadShop,
    is_do_not_contact: Callable[[str], bool],
    today_fn: Callable[[], date],
    checkpointer=None,
):
    def fan_out(state: BatchState) -> list[Send]:
        return [Send("read_shop", shop) for shop in state["shops"]]

    def read_shop(shop: ShopInput) -> dict:
        try:
            extraction = read_shop_fn(shop)
        except Exception as exc:  # decision #2: isolate the failure, keep going
            return {
                "results": [
                    {
                        **shop,
                        "status": "failed",
                        "error": str(exc),
                        "error_kind": classify_error(exc),
                    }
                ]
            }
        return {"results": [{**shop, "status": "read", "extraction": extraction.model_dump()}]}

    def rank(state: BatchState) -> dict:
        ranked = rank_results(
            state["results"], today_fn(), is_do_not_contact, state["dm_slots"], state["comment_slots"]
        )
        return {"ranked": ranked, "status": "awaiting_review"}

    def batch_review(state: BatchState) -> dict:
        decision = interrupt({"type": "batch_review", "batch_id": state["batch_id"]})
        return {"review": decision, "status": "reviewed"}

    builder = StateGraph(BatchState)
    builder.add_node("read_shop", read_shop)
    builder.add_node("rank", rank)
    builder.add_node("batch_review", batch_review)
    builder.add_conditional_edges(START, fan_out, ["read_shop"])
    builder.add_edge("read_shop", "rank")
    builder.add_edge("rank", "batch_review")
    builder.add_edge("batch_review", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())
