from typing import Any, Callable, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


Channel = Literal["comment", "dm", "none"]
ContactBasis = Literal["cold_outreach", "prior_conversation", "inbound_inquiry", "explicit_permission"]


class LeadState(TypedDict, total=False):
    """The shared state for one lead through the business workflow."""

    lead_id: str
    evidence_id: str
    task_id: str
    workflow_run_id: str
    shop_name: str
    profile_url: str
    contact_basis: ContactBasis
    contact_basis_note: str
    video_text: str
    evidence: list[str]
    fit_score: int
    suggested_channel: Channel
    requested_channel: Channel
    channel: Channel
    draft_override: str
    draft: str
    risk_flags: list[str]
    route_review_status: Literal["not_started", "pending", "approved", "rejected"]
    content_review_status: Literal["not_started", "pending", "approved", "rejected"]
    task_status: Literal["new", "blocked", "scheduled", "opened", "sent", "failed", "read_no_reply"]
    scheduled_for: str
    trace: list[str]
    # Phase 2B: human-entered shop context and the model's structured suggestion.
    shop_type: str
    audience: str
    shop_note: str
    activity_note: str
    purchase_signal_note: str
    model_status: Literal["succeeded", "policy_rejected", "failed"]
    model_error: str
    model_route: Literal["dm", "comment", "reject"]
    model_route_reason: str
    model_summary: str
    model_points: list[dict[str, str]]
    model_product_match: list[str]
    model_draft: str
    model_uncertainties: list[str]
    model_quality_flags: list[str]
    model_meta: dict[str, Any]
    # Phase 2C: how the shop was tiered when it was picked, kept for the contact snapshot.
    screening_snapshot: dict[str, Any]
    xhs_id: str
    contact_history: dict[str, Any] | None
    draft_edits: list[dict[str, Any]]


Suggester = Callable[..., dict]


def collect_evidence(state: LeadState) -> dict:
    text = state.get("video_text", "")
    keywords = ("西装", "定制", "面料", "内搭", "试穿", "版型")
    if any(keyword in text for keyword in keywords):
        summary = "视频包含西装定制、面料、内搭或版型相关信息"
    else:
        summary = "视频中未发现西装定制相关关键词"
    evidence = [summary, f"视频原文：{text}"]
    return {"evidence": evidence, "trace": state.get("trace", []) + ["collect_evidence"]}


def score_lead(state: LeadState) -> dict:
    text = state.get("video_text", "")
    keywords = ("西装", "定制", "面料", "内搭", "试穿", "版型")
    score = 40 + sum(10 for keyword in keywords if keyword in text)
    return {"fit_score": min(score, 100), "trace": state["trace"] + ["score_lead"]}


def route_after_score(state: LeadState) -> Literal["dm", "comment", "reject"]:
    score = state.get("fit_score", 0)
    if score >= 85:
        return "dm"
    if score >= 60:
        return "comment"
    return "reject"


def suggest_dm(state: LeadState) -> dict:
    return {
        "suggested_channel": "dm",
        "route_review_status": "pending",
        "trace": state["trace"] + ["suggest_dm"],
    }


def suggest_comment(state: LeadState) -> dict:
    return {
        "suggested_channel": "comment",
        "route_review_status": "pending",
        "trace": state["trace"] + ["suggest_comment"],
    }


def make_model_suggest(suggester: Suggester):
    """Wrap the injected model call as a graph node.

    The graph stays free of provider and database imports; the caller decides
    which model runs and where the invocation is recorded.
    """

    def model_suggest(state: LeadState) -> dict:
        update = suggester(state)
        return {
            **update,
            "route_review_status": "pending",
            "trace": state.get("trace", []) + ["model_suggest"],
        }

    return model_suggest


def route_after_model(state: LeadState) -> Literal["review", "reject"]:
    # A deterministic do-not-contact reject ends here. A model reject or a model
    # failure still goes to the human, who owns the final route decision.
    return "reject" if state.get("model_status") == "policy_rejected" else "review"


def reject_lead(state: LeadState) -> dict:
    return {
        "suggested_channel": "none",
        "channel": "none",
        "route_review_status": "rejected",
        "task_status": "blocked",
        "trace": state["trace"] + ["reject_lead"],
    }


def human_route_review(state: LeadState) -> Command:
    """Let a human accept or override the Agent's suggested channel."""
    decision = interrupt(
        {
            "type": "route_review",
            "message": "请选择最终触达方式：采用建议、改为评论、改为私信或暂不触达",
            "shop_name": state["shop_name"],
            "fit_score": state.get("fit_score"),
            "suggested_channel": state["suggested_channel"],
            "model_status": state.get("model_status"),
            "model_route_reason": state.get("model_route_reason"),
            "options": ["dm", "comment", "none"],
        }
    )
    selected = decision.get("requested_channel", state["suggested_channel"])
    if selected not in ("dm", "comment"):
        return Command(
            update={
                "requested_channel": "none",
                "channel": "none",
                "route_review_status": "rejected",
                "task_status": "blocked",
                "trace": state["trace"] + ["route_review_rejected"],
            },
            goto=END,
        )
    return Command(
        update={
            "requested_channel": selected,
            "channel": selected,
            "route_review_status": "approved",
            "trace": state["trace"] + ["route_review_approved"],
        },
        goto="draft_message",
    )


def make_model_redraft(suggester: Suggester):
    """Draft for the human-selected route, calling the model again only if needed."""

    def draft_message(state: LeadState) -> dict:
        update: dict = {}
        if state.get("draft_override"):
            draft = state["draft_override"]
        elif state.get("model_status") == "succeeded" and state.get("model_route") == state["channel"]:
            draft = state.get("model_draft", "")
        else:
            # The human overrode the suggestion (or the first call failed): ask the
            # model for a draft on the route the human chose. The human's route
            # decision itself is never changed by this call.
            update = suggester(state, state["channel"])
            update.pop("suggested_channel", None)
            draft = update.get("model_draft", "") if update.get("model_status") == "succeeded" else ""
        return {
            **update,
            "draft": draft,
            "content_review_status": "pending",
            "trace": state["trace"] + ["draft_message"],
        }

    return draft_message


def draft_message(state: LeadState) -> dict:
    if state.get("draft_override"):
        draft = state["draft_override"]
    elif state["channel"] == "comment":
        topic = state.get("video_text", "当前内容").replace("\n", " ")[:18]
        draft = f"这条内容里的{topic}细节很有参考价值，肩线和驳头比例处理得很利落。"
    else:
        draft = f"你好，看到你发布的内容里有{state['shop_name']}的定制案例，想先交流一下你们主要服务的客户场景。"
    return {
        "draft": draft,
        "content_review_status": "pending",
        "trace": state["trace"] + ["draft_message"],
    }


def policy_flags(state: LeadState) -> list[str]:
    draft = state.get("draft", "")
    flags: list[str] = []
    hard_block_terms = (
        "微信", "二维码", "网址", "手机号", "电话号码", "邮箱", "加我", "看主页",
        "主页有", "联系方式", "外链", "淘宝", "京东", "抖音", "快手", "拼多多",
        "微博",
    )
    if any(term in draft for term in hard_block_terms):
        flags.append("off_platform_or_contact_lead")
    if state.get("channel") == "comment":
        commercial_comment_terms = (
            "供货", "批发", "拿货", "进货", "报价", "下单", "招商", "加盟", "合作",
            "供应", "货源", "私信", "一件起订",
        )
        if any(term in draft for term in commercial_comment_terms):
            flags.append("commercial_solicitation_in_comment")
    return flags


def policy_gate(state: LeadState) -> dict:
    flags = policy_flags(state)
    return {"risk_flags": flags, "trace": state["trace"] + ["policy_gate"]}


def route_after_policy(state: LeadState) -> Literal["review", "blocked"]:
    return "blocked" if state.get("risk_flags") else "review"


def blocked_lead(state: LeadState) -> dict:
    return {
        "content_review_status": "rejected",
        "task_status": "blocked",
        "trace": state["trace"] + ["blocked_lead"],
    }


def final_policy_gate(state: LeadState) -> dict:
    """Recheck the exact human-edited text before it can be scheduled."""
    return {
        "risk_flags": policy_flags(state),
        "trace": state["trace"] + ["final_policy_gate"],
    }


def human_content_review(state: LeadState) -> Command:
    """Pause for final copy approval; scheduling happens only after approval."""
    decision = interrupt(
        {
            "type": "content_review",
            "message": "请审核最终内容并决定是否加入定时队列",
            "shop_name": state["shop_name"],
            "channel": state["channel"],
            "draft": state["draft"],
            "risk_flags": state.get("risk_flags", []),
            "scheduled_for": state.get("scheduled_for", ""),
        }
    )
    if not decision.get("approved"):
        return Command(
            update={
                "content_review_status": "rejected",
                "task_status": "blocked",
                "trace": state["trace"] + ["content_review_rejected"],
            },
            goto=END,
        )
    return Command(
        update={
            "draft": decision.get("edited_text", state["draft"]),
            "scheduled_for": decision.get("scheduled_for", state.get("scheduled_for", "")),
            "content_review_status": "approved",
            "trace": state["trace"] + ["content_review_approved"],
        },
        goto="final_policy_gate",
    )


def schedule_reminder(state: LeadState) -> dict:
    return {
        "task_status": "scheduled",
        "trace": state["trace"] + ["schedule_reminder"],
    }


def build_graph(checkpointer=None, suggester: Suggester | None = None):
    builder = StateGraph(LeadState)
    if suggester is None:
        builder.add_node("collect_evidence", collect_evidence)
        builder.add_node("score_lead", score_lead)
        builder.add_node("suggest_dm", suggest_dm)
        builder.add_node("suggest_comment", suggest_comment)
    else:
        builder.add_node("model_suggest", make_model_suggest(suggester))
    builder.add_node("route_review", human_route_review)
    builder.add_node(
        "draft_message", draft_message if suggester is None else make_model_redraft(suggester)
    )
    builder.add_node("policy_gate", policy_gate)
    builder.add_node("final_policy_gate", final_policy_gate)
    builder.add_node("content_review", human_content_review)
    builder.add_node("schedule_reminder", schedule_reminder)
    builder.add_node("reject", reject_lead)
    builder.add_node("blocked", blocked_lead)

    if suggester is None:
        builder.add_edge(START, "collect_evidence")
        builder.add_edge("collect_evidence", "score_lead")
        builder.add_conditional_edges(
            "score_lead",
            route_after_score,
            {"dm": "suggest_dm", "comment": "suggest_comment", "reject": "reject"},
        )
        builder.add_edge("suggest_dm", "route_review")
        builder.add_edge("suggest_comment", "route_review")
    else:
        builder.add_edge(START, "model_suggest")
        builder.add_conditional_edges(
            "model_suggest", route_after_model, {"review": "route_review", "reject": "reject"}
        )
    builder.add_edge("draft_message", "policy_gate")
    builder.add_conditional_edges(
        "policy_gate", route_after_policy, {"review": "content_review", "blocked": "blocked"}
    )
    builder.add_conditional_edges(
        "final_policy_gate", route_after_policy, {"review": "schedule_reminder", "blocked": "blocked"}
    )
    builder.add_edge("reject", END)
    builder.add_edge("blocked", END)
    builder.add_edge("schedule_reminder", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
