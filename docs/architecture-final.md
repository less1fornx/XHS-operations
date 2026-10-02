# Agent 运营工作台：最终技术架构基线

本文是后续开发的唯一架构基线。旧的阶段说明仍可作为学习记录，但如果与本文冲突，以本文为准。

## 1. 架构结论

本项目采用：

```text
React/Vite Web
        |
        v
FastAPI API / Application Service
        |
        +--> Domain Rules: 配额、状态机、风险、幂等
        |
        +--> LangGraph Worker: 分析、建议、起草、人工暂停/恢复
        |
        +--> SQLite + SQLModel: 业务数据、审计、任务队列
        |
        +--> LangGraph SQLite Checkpoint: 工作流暂停点和恢复状态
        |
        +--> LLMProvider: Qwen API / Mock / 本地 Qwen（后续替换）
```

### 最重要的边界

1. LLM 负责理解、评分、提出建议和起草，不负责突破业务规则。
2. LangGraph 负责 Agent 流程编排、暂停和恢复，不取代业务数据库。
3. FastAPI 负责权限、状态转换、配额、风险和持久化，不让前端直接决定最终状态。
4. Worker 负责执行耗时的 Agent 工作，不拥有小红书发送权限。
5. 小红书发送永远是人工在官方页面完成；系统只能提醒、打开页面和记录结果。

这是一个“代码控制流程、模型提供局部智能、人工控制外部动作”的系统，不是自由漫游式的全自动 Agent。

## 2. 对当前原型的审查

当前架构评分：**原型阶段 6/10，作为本地 MVP 的方向 8/10，尚不具备生产部署条件**。

### 已经做对的部分

- Python `3.12.14` 和 Node `24.21.0` 已固定，运行时不会随意漂移。
- Phase 0 已用 LangGraph 跑通 `State -> Node -> Conditional Edge -> Interrupt -> Resume`。
- 已有 DM、评论、拒绝三条路线。
- 已有路线人工确认和内容人工审核两个暂停点。
- 已有风险词拦截，且没有发送工具。
- 已接入 SQLite 业务快照、事件记录和 LangGraph SQLite checkpoint 的原型。
- API、Web、Agent 编排已经可以分开启动。

### 必须修正的部分

| 问题 | 当前表现 | 架构修正 |
|---|---|---|
| 数据模型 | 主要保存一份 `payload_json` 快照 | 增加领域表和关系，快照只作为调试/恢复辅助 |
| 配额 | 前端根据当前任务显示数量 | API 在事务中检查并预留配额，前端只展示结果 |
| 状态修改 | API 可以直接修改部分状态 | 所有状态变化走 Application Service 和状态机 |
| 工作流来源 | API 通过路径导入 Phase 0 实验代码 | Phase 1 完成后抽到 `packages/domain` 和 `services/worker` |
| 定时任务 | `scheduled_for` 只是字段 | 增加任务表、轮询 Worker、到点提醒和幂等处理 |
| 审计 | 当前事件保存完整 payload | 增加 actor、动作、前后状态、原因和 trace_id |
| 恢复 | 已能保存 checkpoint，但没有恢复测试 | 必须加入“重启 API 后继续审核”的自动化测试 |
| 版本迁移 | `SQLModel.metadata.create_all` 适合原型，不适合演进 | Phase 1 完成 Alembic migration |
| 测试 | 主要依赖手动 curl 和浏览器操作 | 增加状态机、配额、策略、恢复和 API 集成测试 |
| 文档 | README 曾写“内存数据” | 以本文和实际 SQLite 架构为准 |

## 3. 分层设计

### 3.1 Web 层

技术：React + Vite + TypeScript。

职责：

- 展示店铺、证据、评分、草稿、风险和任务状态。
- 提交人工路线选择、内容修改、审核和结果记录。
- 在店铺详情中提供结果与成交反馈入口，并展示周期分析、策略建议、版本对比和人工批准状态。
- 展示每日私信 `5`、评论 `3` 的内部配额。
- 展示账号 `NORMAL / DEGRADED / FROZEN` 状态。

禁止：

- 不在浏览器保存 API Key。
- 不在前端自行计算并决定是否允许触达。
- 不直接调用 LLM。
- 不实现自动点击小红书发送按钮。

### 3.2 API / Application Service 层

技术：FastAPI + Pydantic + SQLModel。

职责：

- 接收命令：创建线索、开始分析、选择路线、审核内容、排程、记录结果、冻结账号。
- 查询数据库并返回工作台视图。
- 执行配额、重复触达、勿扰名单、风险状态和状态机校验。
- 为 Worker 提供任务领取、完成和失败接口。
- 生成审计事件。

这里是业务规则的唯一入口。前端、Agent 和 Worker 都不能绕过它直接改变业务状态。

### 3.3 Domain 层

位置：`packages/domain/`。

内容：

- 枚举：`Channel`、`TaskStatus`、`RiskStatus`、`ReviewType`。
- 状态转换函数。
- 配额计算和原子预留规则。
- 重复触达、勿扰名单和冻结规则。
- 领域事件结构。

Domain 层不依赖 FastAPI、React、OpenAI 或 Ollama。

### 3.4 Agent Worker 层

位置：`services/worker/`。

职责：

- 领取 `analysis_pending` 或 `draft_pending` 任务。
- 调用 LangGraph。
- 调用结构化 LLM Provider。
- 保存模型版本、Prompt 版本、证据引用和不确定项。
- 在人工审核点暂停，不自行外发。

Worker 没有发送工具，只有分析、起草、策略检查和状态回写能力。

### 3.5 数据层

第一阶段：SQLite + SQLModel + Alembic。

长期部署：PostgreSQL + Alembic。

原因：领域模型和迁移脚本先保持不变，只有数据库连接和少量方言细节变化。

## 4. 数据库设计

### 4.1 `platform_accounts`

运营账号和平台风控状态。

```text
id                  UUID PK
platform            xiaohongshu
account_type        personal
display_name        nullable
status              normal | degraded | frozen
freeze_reason       nullable
dm_daily_limit      default 5
comment_daily_limit default 3
created_at
updated_at
```

第一版只有一个个人号，但仍建表，避免以后把账号信息硬编码在任务表里。

### 4.2 `shop_leads`

候选店铺及其业务画像。

```text
id                  UUID PK
platform_account_id UUID FK nullable
platform            xiaohongshu
handle              nullable
display_name        required
profile_url         nullable
account_type        personal | professional | unknown
activity_score      nullable
business_fit_score  nullable
contact_status      new | reviewed | contacted | replied | declined | do_not_contact
do_not_contact      boolean default false
last_touched_at     nullable
created_at
updated_at
```

约束：同一平台的规范化 `profile_url` 或 `handle` 不重复。

### 4.3 `content_evidence`

Agent 做判断时引用的公开证据。

```text
id                  UUID PK
shop_lead_id        UUID FK
content_url         nullable
content_type        video | note | profile | screenshot | manual_text
title_or_caption    nullable
observed_facts_json JSON/TEXT
source_snapshot     nullable
content_hash        nullable
published_at        nullable
collected_by        human | approved_connector
created_at
```

原则：模型只能引用已保存证据；无法确认的内容进入 `uncertainties`，不能写成事实。

### 4.4 `product_offers`

你的真实业务资料，供 Agent 生成准确内容。

```text
id                  UUID PK
name                required
category            suit | innerwear | shirt | trousers | other
selling_points_json JSON/TEXT
minimum_order_note  nullable
price_range_note    nullable
active              boolean
created_at
updated_at
```

`一件起订`是业务事实，不再作为全局硬拦截词。它可以在有证据依据的陌生 B2B DM、已有真实交流、对方主动咨询或明确同意商务联系的 DM 中使用，但仍必须经过人工审核，且不能与联系方式、二维码、网址、站外交易引导或强迫成交话术组合；评论区仍按商业招揽规则拦截。

### 4.5 `workflow_runs`

一次 Agent 编排运行。

```text
id                  UUID PK
shop_lead_id        UUID FK nullable
run_type            qualification | draft | policy_check
status              running | waiting_human | completed | failed | cancelled
graph_name          required
graph_version       required
thread_id           required unique
input_json          JSON/TEXT
output_json         JSON/TEXT nullable
error_json          JSON/TEXT nullable
started_at
finished_at nullable
created_at
updated_at
```

`thread_id` 对应 LangGraph checkpoint 的线程标识；业务表保存索引和生命周期，checkpoint 保存图恢复所需的内部状态。

### 4.6 `outreach_tasks`

评论或私信任务，是核心业务表。

```text
id                  UUID PK
shop_lead_id        UUID FK
platform_account_id UUID FK
evidence_id         UUID FK nullable
workflow_run_id     UUID FK nullable
channel             dm | comment
contact_basis       cold_outreach | prior_conversation | inbound_inquiry | explicit_permission
status              draft | route_review | content_review | approved |
                    scheduled | opened | sent | failed | blocked | rejected
suggested_channel   nullable
draft_text          nullable
final_text          nullable
risk_level          none | low | medium | high | critical
policy_reasons_json JSON/TEXT
scheduled_for       nullable
opened_at           nullable
sent_at             nullable
result_note         nullable
idempotency_key     unique
created_at
updated_at
```

约束：

- 同一店铺同一自然日只能有一个新触达任务。
- 同一任务不能同时是 DM 和评论。
- `sent` 必须先经过 `approved -> scheduled -> opened`，人工记录后才可进入。
- `blocked / rejected / sent / read_no_reply` 是终态，不能被普通接口改回草稿。
- `read_no_reply` 只能由人工在 DM 已发送后确认；确认后同步将 `shop_leads.do_not_contact` 设为 `true`，后续候选评选和触达创建都必须拒绝。
- 人工编辑后的最终文本必须再次经过 Policy Gate，人工审核不能成为绕过规则的入口。
- 同日已预留任务不能使用完全相同的最终文案；语义相似度评估留给 Phase 2 的评估集。

### 4.7 `human_reviews`

记录两次人工介入，而不是只在任务状态上留下一个结果。

```text
id                  UUID PK
task_id             UUID FK
review_type         route | content | platform_result | risk_recovery
decision            approve | override | reject | edit | confirm | freeze | unfreeze
selected_channel    nullable
before_text         nullable
after_text          nullable
reason              nullable
actor               local_user
created_at
```

### 4.8 `quota_usage`

用于服务端原子配额控制。

```text
id                  UUID PK
platform_account_id UUID FK
usage_date          DATE
dm_new_count        integer default 0
comment_new_count   integer default 0
created_at
updated_at
```

唯一键：`(platform_account_id, usage_date)`。

预留任务时必须在同一事务中锁定或更新该行；不能只依赖前端显示。

### 4.9 `risk_events`

```text
id                  UUID PK
platform_account_id UUID FK nullable
task_id             UUID FK nullable
severity            info | warning | critical
type                duplicate | off_platform | policy_hit | commercial_solicitation |
                    no_response | send_failure | captcha | platform_restriction |
                    account_frozen | unknown
message             required
metadata_json       JSON/TEXT
resolved_at         nullable
created_at
```

### 4.10 `audit_logs`

```text
id                  UUID PK
actor               agent | local_user | system
action              required
entity_type         required
entity_id           required
before_json         nullable
after_json          nullable
trace_id            nullable
created_at
```

审计目标：能够回答“谁在什么时候，根据哪些证据，批准了哪条内容，后来发生了什么”。

### 4.11 LangGraph checkpoint 数据

LangGraph 的 checkpoint 不与业务表混为一谈。开发期可以使用：

```text
data/agent_ops.db             # 业务表
data/agent_checkpoints.sqlite # LangGraph checkpoint 表
data/uploads/                 # 截图等证据文件
```

LangGraph 官方文档将 checkpointer 定义为线程级短期状态，将 Store/业务数据库用于跨线程长期数据；本项目按这个边界实现。人工中断必须依赖 checkpointer 才能恢复。

### 4.12 `outreach_outcomes`

保存人工确认的真实业务结果。不能只保存成交案例，所有已触达对象都需要结果状态，避免幸存者偏差。

```text
id                    UUID PK
outreach_task_id      UUID FK unique
shop_lead_id          UUID FK
reply_status          no_reply | replied | declined | blocked
intent_stage          none | interested | needs_details | sample | quotation | negotiation
conversion_status     open | won | lost
converted_at          nullable
order_value_note      nullable
need_tags_json        JSON/TEXT
attraction_tags_json  JSON/TEXT
objection_tags_json   JSON/TEXT
human_notes           nullable
confirmed_by          local_user
created_at
updated_at
```

这里的 `order_value_note` 第一版只做可选人工记录，不参与自动决策。成交结果属于滞后标签，不能反写为“联系当时已经存在”的采购信号。

### 4.13 `feature_snapshots`

保存做出筛选和起草决策时实际可见的特征，防止后续分析发生标签泄漏。

```text
id                    UUID PK
shop_lead_id          UUID FK
outreach_task_id      UUID FK nullable
snapshot_type         qualification | outreach
feature_schema_version required
features_json         JSON/TEXT
evidence_ids_json     JSON/TEXT
prompt_version        nullable
strategy_version      required
captured_at
```

特征分为两类：

- 店铺特征：业务类型、活跃度、价格定位证据、内容主题、可核验采购信号、历史联系结果。
- 沟通特征：触达路线、个性化证据数量、产品匹配点、文案长度、开场类型、询问方式和 Prompt 版本。

不得将成交后才获得的需求、预算或订单信息写入联系前的特征快照。

### 4.14 `strategy_analysis_runs` 与 `strategy_versions`

周期分析和最终生效策略必须分开保存。分析结果只是候选建议，不能直接修改生产规则。

```text
strategy_analysis_runs
id                    UUID PK
period_start          DATE
period_end            DATE
sample_size           integer
positive_count        integer
conversion_count      integer
metrics_json          JSON/TEXT
patterns_json         JSON/TEXT
recommendations_json  JSON/TEXT
status                draft | reviewed | accepted | rejected
created_at

strategy_versions
id                    UUID PK
version               required unique
status                draft | active | retired | rolled_back
screening_rules_json  JSON/TEXT
prompt_versions_json  JSON/TEXT
change_reason         required
source_analysis_id    UUID FK nullable
approved_by           nullable
activated_at          nullable
created_at
```

成熟系统采用 `champion/challenger` 思路：当前生效版本是 champion，新建议先作为 challenger 在固定评估集和少量真实案例中验证。只有人工批准后才能切换，且必须保留回滚路径。

## 5. 最终工作流

```text
创建 ShopLead / Evidence
        |
        v
创建 WorkflowRun
        |
        v
collect_evidence
        |
        v
score_lead
        |
        +--> needs_more_evidence -> 人工补充证据
        |
        +--> reject -> blocked
        |
        v
suggest_route
        |
        v
HUMAN_ROUTE_REVIEW
        |
        +--> none -> rejected
        |
        +--> dm/comment（陌生 B2B DM 进入强制人工审核，不自动发送）
                    |
                    v
            draft_content
                    |
                    v
            policy_gate
                    |
                    +--> blocked + RiskEvent
                    |
                    v
            HUMAN_CONTENT_REVIEW
                    |
                    +--> reject -> rejected
                    |
                    v
            reserve_quota_transaction
                    |
                    +--> quota/risk failure -> blocked
                    |
                    v
            scheduled
                    |
                    v
            reminder_worker
                    |
                    v
            human opens official page -> opened
                    |
                    v
            record_result
                    |
                    +--> sent -> update quota/account metrics
                    +--> failed/restriction -> RiskEvent and possible freeze
                    +--> read_no_reply -> do_not_contact + stop future qualification
                    |
                    v
            human records outcome / conversion feedback
                    |
                    v
            feature snapshot + periodic cohort analysis
                    |
                    v
            HUMAN_STRATEGY_REVIEW
                    |
                    +--> reject -> keep current strategy
                    +--> approve -> challenger evaluation -> versioned activation
```

### 编排原则

- 路线分支、配额、状态转换、风险词和冻结由代码决定。
- 证据摘要、匹配评分和文本草稿可以调用 LLM。
- LLM 输出必须是 Pydantic 结构化结果。
- 所有长耗时或可能失败的外部调用放在 Worker Activity/任务中。
- 任何真正的外部动作都要有人工审批和幂等键。

## 6. Agent 角色设计

第一版不做多 Agent 群聊，也不让多个 Agent 自由互相转交。采用“一个显式工作流 + 少量职责清晰的智能节点”：

| 节点 | 是否使用 LLM | 输出 |
|---|---:|---|
| EvidenceExtractor | 是 | 事实、主题、证据引用、不确定项 |
| LeadScorer | 可选 | 分数、理由、证据 ID、置信度 |
| RouteAdvisor | 否/可选 | `dm/comment/none` 建议，代码阈值最终裁决 |
| CommentDrafter | 是 | 针对当前视频的评论草稿 |
| DMDrafter | 是 | 真实身份、联系依据、低压私信草稿 |
| PolicyGate | 否 | 风险级别、命中规则、阻断原因 |
| HumanReview | 否 | 人工选择、修改和批准 |
| PatternAnalyst | 规则统计优先，LLM 可选 | 分群指标、候选规律、反例、优化建议与不确定性 |

这对应成熟系统中常见的“代码编排 + 受限 Agent 节点 + Guardrail + Human approval”结构。

## 7. 与成熟 Agent 架构的对比

### LangGraph

LangGraph 的优势是低层、显式、有状态，提供持久化、durable execution 和 human-in-the-loop。官方文档明确说明 `interrupt` 需要 checkpointer，恢复时通过 `Command` 继续图执行。它非常适合本项目的两次人工暂停、审计和长时间等待。

结论：**作为本项目的核心编排框架，保留 LangGraph。**

### OpenAI Agents SDK

OpenAI Agents SDK 把 Agent、工具、handoff、guardrail、session 和 tracing 做成轻量原语，并区分“Agents as tools”和“handoffs”。它适合模型驱动的工具调用和多 Agent 协作。

本项目暂不把它作为主编排框架，因为当前流程的关键路径是固定的业务状态机和人工审批，而不是让 LLM 自己决定下一步。后续可以借鉴它的三个思想：

- 工具必须有明确 schema。
- 输入/输出 guardrail 与 Agent 分离。
- 每次运行都有 trace、模型和工具调用记录。

### Anthropic 的 Agent 设计思想

Anthropic 建议先区分 workflow 和 agent：固定路径、可预测任务优先 workflow；只有需要模型动态决定过程时才增加 Agent 自主性。本项目的配额、风险和发送边界本来就要求可预测，所以采用 workflow-first 是正确的。

### Temporal

Temporal 用 Event History 保证长流程在崩溃、重启和多年等待后恢复，并把 Workflow 与 Activity 分开。它适合大规模、多 Worker、复杂重试和长时间任务。

本项目暂不引入 Temporal，因为当前是单机单用户、任务量很小；SQLite + LangGraph checkpoint 足够学习和验证。达到以下条件再评估 Temporal：

- 多个 Worker 并发执行。
- 任务跨天或跨周大量等待人工。
- 需要严格的重试、定时器、版本化和故障恢复。
- SQLite 已成为并发瓶颈。

### 最终取舍

```text
LangGraph：业务 Agent 图和人工中断
FastAPI：业务命令、状态机、Policy Gate、配额
SQLite/PostgreSQL：长期业务数据
Worker：耗时任务和提醒
LLMProvider：模型供应商适配
Temporal：未来规模化的可选执行底座，不提前引入
```

## 8. 完整执行路线

### Phase 0：编排认知，已完成

验收：能解释 State、Node、Edge、Conditional Edge、Interrupt、Resume 和 Checkpoint，并能看懂四个示例的 trace。

### Phase 1A：本地纵向切片，已完成

技术：React/Vite、FastAPI、LangGraph、模拟数据。

验收：页面能完成创建、选路、审核和排程。

### Phase 1B：持久化基础，已完成

必须完成：

1. 业务 SQLite 和 LangGraph checkpoint 分离。
2. `workflow_runs`、`outreach_tasks`、`human_reviews`、`audit_logs` 等领域表落地。
3. Alembic migration。(数据库迁移工具)
4. API 重启后可以继续路线审核和内容审核。
5. 事件记录可查询。

验收：已验证杀掉并重启 API 后，待审核任务仍然存在；路线审核和内容审核可以继续，正式任务表、人工审核表和审计表同步更新。

### Phase 1C：业务规则闭环

必须完成：

1. 后端原子配额：DM 5、评论 3。
2. 同一店铺同一自然日不能进入两种触达队列。
3. `NORMAL / DEGRADED / FROZEN` 账号状态。
4. 风险命中冻结队列和通知。
5. 状态机拒绝非法跳转。
6. 幂等键防止重复审批、重复排程和重复记录结果。
7. 人工确认 DM “已读无回复”后，店铺进入勿扰名单，不再参加后续评选。
8. 工作台从真实 API 展示配额、队列和人工审核动作。
9. 人工编辑后的文本重新执行策略闸门，阻止导流、评论销售招揽和其他高风险内容。
10. 个人号首次陌生 B2B DM 可以进入强制人工审核流程，但不能自动发送；5/3 仅是内部运营限额，不是平台安全保证。
11. DM 资格需要 `contact_basis` 和人工依据备注同时存在，避免通过单纯勾选绕过审核。

验收：使用 API 直接绕过前端时，规则仍然成立。

### Phase 1D：任务 Worker 和工作台（完成）

必须完成：

1. `scheduled` 任务表查询。
2. Worker 轮询到期任务。
3. 到点提醒，并由人工在官方页面打开目标页面。
4. 发送结果人工记录。
5. 工作流、队列、风险中心三个页面使用真实数据库查询。

验收：重启 Worker 不会重复提醒或重复改变状态；`scheduled -> opened -> sent` 链路可查询，Worker 没有发送权限。

本阶段已落地：

- API：`GET /api/queue` 查询 `scheduled/opened` 任务，`POST /api/runs/{id}/open` 记录人工打开官方页面，`POST /api/runs/{id}/result` 记录 `sent/failed/read_no_reply`。
- Worker：`services/worker/worker.py` 只领取到期提醒，使用 `reminder_sent_at` 和唯一事件记录保证重启幂等；它不能登录、打开页面或发送内容。
- 工作台：候选工作流、定时队列、风险记录三个页面都从 API 读取真实 SQLite 数据；前端动作完成后重新拉取状态，避免只更新浏览器内存。
- 风险闭环：验证码、限制、禁言等人工记录会冻结账号并写入风险中心；策略拦截会释放预占配额，避免被无效任务消耗。
- 学习重点：这一阶段把 Agent 的“建议与编排”和“外部副作用”分开。Agent 可以生成建议，Worker 可以提醒，只有人工确认后的工作台动作才允许改变平台侧结果。

### Phase 2：LLM Provider 和评估集

必须完成：

1. `LLMProvider` 抽象。
2. Qwen API 适配器和无需密钥的 Mock 适配器。
3. Pydantic 结构化输出。
4. Prompt 版本、模型版本、输入证据 ID 保存。
5. 固定样例集和人工标注。
6. 统计引用准确率、人工修改率、风险误放率。
7. 定义结果反馈、特征快照和策略版本的数据契约；使用模拟结果验证分析不会把成交后信息泄漏到联系前特征。

验收：模型失败、超时或输出不合规时，任务进入可解释的失败/人工复核状态。

### Phase 3：真实人工运营

必须完成：

1. 人工录入小红书公开链接、视频文字和截图。
2. 证据快照和内容哈希。
3. 店铺库、产品资料和触达历史。
4. 官方页面打开记录。
5. 发送失败、验证码、限制和禁言的冻结流程。
6. 店铺详情中的人工结果反馈窗口，覆盖未回复、拒绝、正向意向、报价、成交和未成交。
7. 每周生成一次分群分析草案；样本不足时只显示观察，不允许直接替换正式策略。
8. 策略建议经过人工审核、固定评估集和小范围 challenger 验证后，按版本启用或回滚。

验收：真实线索从证据录入到人工发送结果全程可追溯。

### Phase 4：本地模型和长期部署

必须完成：

1. Ollama Provider，与 Qwen Provider 使用同一接口并加载本地 Qwen。
2. Qwen API/本地 Qwen 对照评估。
3. PostgreSQL 迁移。
4. Redis 或更强任务队列。
5. Docker Compose、备份、日志、健康检查。

验收：一条配置可以切换模型；数据可备份恢复；服务重启后任务不丢失。

## 9. 当前明确暂停点

截至本阶段：

- Phase 0：完成。
- Phase 1A：完成。
- Phase 1B：完成。SQLite 正式领域表、Alembic 初始迁移、业务快照、审计事件和 LangGraph SQLite checkpoint 已接入，并通过重启恢复验证。
- Phase 1C：完成。后端配额、状态机、风险冻结、幂等、勿扰名单和工作台真实 API 联动已接入，并通过集成回归。
- Phase 1D：已完成。Worker 一次性提醒、`scheduled -> opened -> sent` 人工发送链路、队列查询、风险中心和重启幂等均已接入；发送权限仍只在人工工作台，Worker 不执行发送。
- Phase 2A：已完成基础骨架。已提供输入/输出契约、版本化 Prompt、Qwen/Mock Provider、证据引用校验和固定案例评测；尚未连接生产 Worker、调用记录和前端人工审核页。
- Phase 2B、3、4：尚未开始。

后续开发从 Phase 2B 开始，把已验证的模型调用接入 Worker、持久化调用记录并连接前端人工审核。

## 10. 参考资料

- [LangGraph persistence and human-in-the-loop](https://reference.langchain.com/python/langgraph/types/interrupt)
- [LangGraph durable execution](https://langchaindocs.sitemirror.store/oss/python/langgraph/durable-execution/)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [OpenAI Agents orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Temporal durable execution](https://docs.temporal.io/temporal)
- [小红书运营规则基线](xhs-compliance-rules.md)
