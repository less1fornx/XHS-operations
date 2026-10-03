# Agent运营工作台

面向小红书人工运营的本地 Agent 学习项目。

第一版目标：筛选西装定制店，保存公开依据，生成针对当前视频的评论或私信草稿，由人工审核后在小红书官方界面操作，并记录结果与风险状态。

## 当前边界

- 不自动登录、抓取或发送小红书私信。
- 不把联系方式、二维码、网址或站外下单引导自动写入消息。
- 陌生店铺 B2B 私信是主要业务场景，但必须有店铺/内容证据、经过人工审核，并由人工在小红书官方页面发送；系统不提供自动发送能力。
- 每 24 小时最多 5 家符合条件的私信、3 家评论；这是内部保守上限，不是平台安全承诺；两类触达不能作用于同一家店铺的同一天。
- 发现发送失败、验证码、限制或禁言提示时，冻结队列并要求人工确认。
- 人工审核后可以进入定时队列；到点提醒并打开官方页面，最终发送仍由人工完成。

## 目录

```text
agent-app/
├── apps/web/                 # React 前端运营工作台
├── services/api/             # FastAPI 业务 API
├── services/worker/          # Agent 工作流与异步任务
├── packages/domain/          # 领域对象、状态机、配额策略
├── packages/ui/              # 共享 UI 规范与组件约定
├── labs/phase0-langgraph/    # LangGraph 无模型学习实验
├── docs/                     # 架构与学习文档
├── data/                     # 本地开发数据，不提交真实凭据
└── infra/                    # Docker、数据库和部署配置
```

最终架构基线见 [docs/architecture-final.md](docs/architecture-final.md)；已确认的长期需求见 [docs/project-memory.md](docs/project-memory.md)；历史架构说明见 [docs/architecture.md](docs/architecture.md)，阶段学习记录见 [docs/phase-plan.md](docs/phase-plan.md)。运行时版本见 [docs/node-runtime.md](docs/node-runtime.md)。

## Phase 1 本地运行

先在一个终端启动 API：

一条命令同时启动后台和前端（会先检查数据库结构，按 Ctrl+C 同时停止）：

```bash
cd /Users/askvenht/AI/agent-app
./scripts/dev.sh
```

端口被占用时可以换端口：`API_PORT=8001 WEB_PORT=5174 ./scripts/dev.sh`。

也可以分别手动启动：

```bash
cd /Users/askvenht/AI/agent-app
uv run alembic upgrade head
PYTHONPATH=labs/phase0-langgraph/src .venv/bin/uvicorn services.api.app.main:app --host 127.0.0.1 --port 8000
```

再在另一个终端启动前端：

```bash
cd /Users/askvenht/AI/agent-app/apps/web
export PATH=/Users/askvenht/.local/node/node-v24.21.0-darwin-arm64/bin:$PATH
npm run dev -- --host 127.0.0.1
```

浏览器打开 `http://127.0.0.1:5173`。当前版本用于学习和跑通流程，业务数据保存到 `data/agent_ops.db`，LangGraph 检查点保存到 `data/agent_checkpoints.sqlite`；小红书最终发送仍由人工在官方界面完成。

Phase 1D 再启动一个本地 Worker：

```bash
cd /Users/askvenht/AI/agent-app
PYTHONPATH=. uv run python -m services.worker.worker
```

Worker 只领取到期提醒并输出一次性提醒事件，不会登录、打开页面或发送消息。前端的“打开官方页面并记录”按钮负责记录人工打开动作，只有记录为 `opened` 后才能确认 `sent`。

## 预留的结果反馈通道

当前不要求提供真实成交数据。以后有回复、意向、成交或未成交结果时，可以通过
`POST /api/outcomes/import` 批量写入；同一个 `run_id` 重复导入会更新原记录，不会产生重复样本。

```json
{
  "items": [
    {
      "run_id": "工作流编号",
      "reply_status": "replied",
      "intent_stage": "quotation",
      "conversion_status": "won",
      "converted_at": "2026-10-02T10:30:00+08:00",
      "order_value_note": "可选的人工金额备注",
      "need_tags": ["针织内搭"],
      "attraction_tags": ["一件起订", "产品匹配"],
      "objection_tags": ["交期"],
      "human_notes": "人工补充说明",
      "source": "manual_import"
    }
  ]
}
```

枚举值：

- `reply_status`: `no_reply | replied | declined | blocked`
- `intent_stage`: `none | interested | needs_details | sample | quotation | negotiation`
- `conversion_status`: `open | won | lost`

只有已经人工确认发送的任务可以记录结果；成交记录必须同时提供已回复状态、有效意向阶段和成交时间。查询接口为 `GET /api/outcomes`。前端反馈窗口将在真实运营阶段复用这份数据契约。

## Phase 2 本地评测

第一版使用 mock provider 学习完整链路，不产生模型费用：

```bash
cd /Users/askvenht/AI/agent-app
PYTHONPATH=. uv run python -m services.worker.evaluate_phase2 --provider mock
```

准备好百炼 API Key 后，在本机创建 `.env`，参考 `.env.example` 填写并将
`AGENT_LLM_PROVIDER` 改为 `qwen`，再运行：

```bash
PYTHONPATH=. uv run python -m services.worker.evaluate_phase2 --provider qwen
```

评测报告保存到 `outputs/`。Phase 2 学习步骤见
[docs/phase2-agent-design-learning.md](docs/phase2-agent-design-learning.md)。
