# Phase 0：LangGraph 学习实验

这个实验不接小红书、不接真实模型、不发送任何消息，只用四个虚构案例学习 LangGraph 的基本角色。输出内容是模拟数据，不是抓取结果。

## 你会看到什么

- `LeadState`：所有节点共享的状态。
- `collect_evidence`、`score_lead` 等：节点，每个节点只做一件事。
- `add_edge`：固定边，定义正常流转。
- `add_conditional_edges`：条件边，根据评分或风险结果分支。
- `interrupt`：人工选择路线和审核最终内容的暂停点。
- `MemorySaver`：保存状态，让暂停后的流程可以继续。
- `Command(resume=...)`：提供人工结果并恢复流程。

项目统一使用 Python 3.12.14，兼容范围锁定为 `>=3.12,<3.13`。LangGraph 当前 Python 包要求 Python 3.10 以上，因此 3.12 是本项目后续 API、Worker 和 Ollama 适配共同使用的版本。官方文档将 LangGraph 定位为有状态、多步骤 Agent 的底层编排框架。

## 第一次运行

```bash
cd /Users/askvenht/AI/agent-app
uv sync
PYTHONPATH=labs/phase0-langgraph/src .venv/bin/python labs/phase0-langgraph/run_demo.py
```

运行时会看到四个不同场景：Agent 建议私信但人工改选评论、中匹配评论进入定时队列、低匹配店铺被拒绝、人工选择私信但包含风险词的草稿被 Policy Gate 拦截。重点观察每个案例的 `trace`：

```text
collect_evidence
score_lead
suggest_dm / suggest_comment
route_review_approved
draft_message
policy_gate
content_review_approved
schedule_reminder
```

## 你的学习任务

1. 把 `route_after_score` 的高匹配阈值从 85 改成 90，观察建议路线变化。
2. 在路线审核时把高匹配案例从 `dm` 改成 `comment`。
3. 给 `policy_gate` 增加一个“重复内容”检查。
4. 在内容审核时修改草稿，观察 `draft` 和 `trace` 如何变化。
5. 画出这张图，并给每个节点写一句“为什么必须存在”。

完成这些任务后，才进入 Phase 1 的 Web、API 和数据库骨架。
