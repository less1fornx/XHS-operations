from pprint import pprint

from langgraph.types import Command

from phase0_graph.lead_workflow import build_graph


def show_state(label: str, state: dict) -> None:
    print(f"\\n--- {label} ---")
    pprint(
        {
            "fit_score": state.get("fit_score"),
            "suggested_channel": state.get("suggested_channel"),
            "requested_channel": state.get("requested_channel"),
            "channel": state.get("channel"),
            "draft": state.get("draft"),
            "risk_flags": state.get("risk_flags"),
            "route_review_status": state.get("route_review_status"),
            "content_review_status": state.get("content_review_status"),
            "task_status": state.get("task_status"),
            "scheduled_for": state.get("scheduled_for"),
            "trace": state.get("trace"),
        }
    )


def main() -> None:
    graph = build_graph()
    samples = [
        {
            "name": "高匹配：Agent建议私信，人工改选评论",
            "state": {
                "shop_name": "示例西装定制店",
                "video_text": "春季西装定制试穿，展示面料、版型和内搭搭配",
            },
            "human_channel": "comment",
            "approve_content": True,
            "scheduled_for": "2026-10-01 18:00 Asia/Shanghai",
        },
        {
            "name": "中匹配：采用评论建议并进入定时队列",
            "state": {
                "shop_name": "商务男装工作室",
                "video_text": "商务西装版型分享，适合通勤场景",
            },
            "human_channel": "comment",
            "approve_content": True,
            "scheduled_for": "2026-10-01 19:00 Asia/Shanghai",
        },
        {
            "name": "低匹配：直接拒绝",
            "state": {
                "shop_name": "普通家居店",
                "video_text": "春季家居收纳整理分享",
            },
            "human_channel": "none",
            "approve_content": False,
        },
        {
            "name": "高匹配：人工选择私信，但风险检查拦截",
            "state": {
                "shop_name": "高端定制店",
                "video_text": "高端西装定制面料和版型展示",
                "draft_override": "你好，欢迎加我微信了解一件起订。",
            },
            "human_channel": "dm",
            "approve_content": False,
        },
    ]

    for index, sample in enumerate(samples, start=1):
        config = {"configurable": {"thread_id": f"phase0-business-{index}"}}
        initial_state = {
            **sample["state"],
            "trace": [],
            "route_review_status": "not_started",
            "content_review_status": "not_started",
            "task_status": "new",
        }

        print(f"\\n=== 案例 {index}：{sample['name']} ===")
        result = graph.invoke(initial_state, config)
        show_state("Agent建议路线，等待人工选择", result)

        if "__interrupt__" in result:
            result = graph.invoke(
                Command(resume={"requested_channel": sample["human_channel"]}),
                config,
            )
            show_state("人工选择路线后，等待内容审核", result)

        if "__interrupt__" in result:
            result = graph.invoke(
                Command(
                    resume={
                        "approved": sample["approve_content"],
                        "edited_text": result.get("draft", ""),
                        "scheduled_for": sample.get("scheduled_for", ""),
                    }
                ),
                config,
            )
            show_state("人工审核内容后，流程结束", result)


if __name__ == "__main__":
    main()
