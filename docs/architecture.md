# Agent运营工作台架构设计

## 1. 目标与非目标

### 目标

把一次运营动作拆成可观察、可暂停、可审计的 Agent 流程：

```text
录入店铺/视频证据
  -> 账号画像与优先级评分
  -> 选择评论或私信任务
  -> 生成个性化草稿
  -> 风险检查
  -> 人工确认
  -> 进入计划队列
  -> 到点提醒并打开官方页面
  -> 人工操作并记录结果
  -> 更新配额、状态和学习数据
```

### 非目标

- 不实现自动发送、自动点赞、自动关注或模拟点击。
- 不绕过验证码、风控、禁言或平台权限。
- 不把“每天 5 条私信、3 条评论”解释为平台安全阈值。这是本应用的内部保守配额。
- 定时任务只负责提醒和打开官方页面，不代表系统获得自动发信权限。
- 第一版不依赖小红书开放平台读取全站笔记的能力；数据由人工录入、粘贴链接、导入截图或后续合规接口提供。

## 2. 技术选型

| 层 | 第一版 | 选择原因 |
|---|---|---|
| 前端 | React + Vite + TypeScript + Tailwind + shadcn/ui | 适合运营后台，组件可读、可改，便于学习前端状态和交互 |
| API | Python 3.12 + FastAPI | 与 Worker 共用确定的 Python 运行时，类型清晰，适合快速构建本地 API 和 Agent 服务 |
| Agent | LangGraph + Python 3.12 | 用有状态图表达筛选、生成、审核、暂停和恢复，适合学习 Agent 编排 |
| 数据库 | SQLite + SQLModel | 本地部署简单；后续可切换 PostgreSQL |
| 任务队列 | 第一版使用数据库任务表 | 减少基础设施；需要并发后再接 Redis/队列 |
| 模型 | LLM Provider 抽象，首选 Qwen API | 中文 B2B 场景和结构化输出先建立质量基线；以后可切换本地 Qwen，不改业务层 |
| 运行方式 | 前端、API、Worker 分进程启动 | 保留清晰边界，方便后续扩展 |

模型调用必须经过统一接口：

```text
LLMProvider
├── QwenProvider
├── MockProvider
└── OllamaProvider（后续）
```

Agent 只依赖 `LLMProvider`，不直接依赖具体厂商。这样后续从云端模型切换到本地模型时，只替换配置和适配器。

## 3. 服务边界

### Web

负责展示和人工操作：

- 今日配额和风险状态
- 店铺列表、筛选和评分依据
- 视频证据和评论上下文
- 私信/评论草稿编辑
- 发送前风险提示
- 打开小红书官方页面
- 记录人工操作结果

Web 不保存 LLM API Key，也不直接调用模型。

### API

负责业务规则和持久化：

- 店铺、视频、证据和产品资料
- 触达任务与审核状态
- 配额检查
- 勿扰名单和冷却时间
- 风险事件与审计日志
- 调用 Agent Worker 的任务接口

所有发送任务必须经过 API 的 `PolicyGate`，前端不能绕过它。

### Agent Worker

负责可重试但不可自动外发的智能任务：

1. 提取视频中的事实和主题。
2. 判断店铺与“西装定制、内搭、中高客单价、活跃度”的匹配度。
3. 生成候选评论或私信草稿。
4. 给出生成依据和不确定项。
5. 调用风险检查器。
6. 把结果提交为 `待人工审核`。
7. 对已批准任务执行排程提醒，不执行平台发送动作。

Worker 不具备“发送消息”权限。

## 4. 核心领域对象

### ShopLead

```text
id
platform = xiaohongshu
handle
display_name
profile_url
account_type
activity_score
business_fit_score
evidence_status
contact_status
do_not_contact
last_touched_at
created_at
```

### ContentEvidence

```text
id
shop_lead_id
content_url
content_type = video | note | profile
published_at
title_or_caption
observed_facts
source_snapshot
collected_by = human | approved_connector
```

### OutreachTask

```text
id
shop_lead_id
channel = dm | comment
content_evidence_id
status = draft | route_review | content_review | approved | scheduled | opened | sent | failed | blocked | rejected
draft_text
final_text
risk_level
policy_reasons
scheduled_for
sent_at
result_note
```

`scheduled_for` 只代表人工操作提醒时间，不代表系统拥有自动发送权限。

### RiskEvent

```text
id
severity = info | warning | critical
type = duplicate | off_platform | harassment | send_failure | platform_restriction | unknown
source_task_id
message
resolved_at
```

### AuditLog

记录谁在什么时候创建、修改、审核、打开官方页面、标记发送结果和冻结队列。Agent 应用要能回答：

> 这条内容为什么生成？谁批准的？依据是什么？最后发生了什么？

## 5. 配额与状态机

### 内部配额

默认使用滚动 24 小时窗口：

```text
dm_new_target_limit      = 5
comment_new_target_limit = 3
same_target_same_day     = blocked
```

评论和私信分别统计，但同一店铺当天不能进入两个新触达队列。

### 风险状态

```text
NORMAL
  -> DEGRADED   发送失败、频繁提示或无法确认结果
  -> FROZEN     明确限制、验证码、禁言、账号异常

DEGRADED
  -> NORMAL     人工确认账号状态正常
  -> FROZEN     再次失败或出现明确限制

FROZEN
  -> NORMAL     人工检查平台通知并手动恢复
```

状态变化时：

- `DEGRADED`：新私信降为 2 家、评论降为 1 家。
- `FROZEN`：新私信和新评论均为 0，队列停止。
- 不自动重试，不切换账号，不修改文本来测试平台边界。

## 6. Agent 图设计

```text
collect_evidence
      |
      v
score_lead -----> needs_more_evidence
      |
      v
suggest_dm / suggest_comment / reject
      |
      v
human_route_review
      |
      v
draft_comment_or_dm
      |
      v
policy_gate -----> blocked
      |
      v
human_content_review
      |
      v
schedule_reminder
      |
      v
open_official_page
      |
      v
record_result -----> update_quota_and_risk_state
```

每个节点输入输出结构化 JSON，避免 Agent 直接返回不可解析的长文本。每次模型输出都保存：模型、提示版本、输入证据 ID、结果和人工修改内容。

## 7. 风险策略

评论草稿必须满足：

- 引用当前视频中的具体事实或观点。
- 有行业相关价值，不直接报价。
- 不包含微信、二维码、网址、站外下单或暗号。
- 不使用“全网最低、绝对高端、保证效果”等无依据表述。
- 不贬低店铺、竞品或其他商家。

私信草稿必须满足：

- 亮明真实业务身份。
- 说明联系依据，而不是批量模板。
- 不因未回复而自动追发。
- 对方拒绝后进入勿扰名单。
- 涉及站外合作时，必须由人工根据账号实际可用的官方功能和平台规则判断，Agent 默认拦截。

## 8. 前端页面

### 概览页

显示：

- 私信 `3 / 5`
- 评论 `2 / 3`
- 总触达数量
- 当前风险状态
- 待审核任务
- 最近风险事件

### 店铺页

左侧是筛选和排序，主体是店铺表格，详情抽屉展示：

- 店铺基本资料
- 活跃度和业务匹配分
- 公开内容证据
- Agent 判断依据
- 触达历史
- 勿扰和冻结操作

### 触达审核页

左右分栏：

- 左侧：当前视频、标题、发布时间和证据
- 中间：Agent 分析和生成理由
- 右侧：评论/私信编辑器、风险检查和人工确认

按钮只允许：

- `通过审核`
- `退回重写`
- `加入勿扰`
- `打开官方页面`
- `记录已发送`
- `记录发送失败`

### 定时任务规则

- 只有完成人工路线确认和人工内容审核的任务才能进入计划队列。
- 私信和评论任务使用同一个触达时间线，不能安排在同一时间执行。
- 默认错开任务时间，间隔属于本地运营设置，不代表平台安全阈值。
- 到点任务只提醒、展示已审核草稿并打开官方页面，不自动点击发送。
- 人工标记结果后，系统才消耗对应配额并更新审计日志。
- 如果出现平台限制、验证码或发送失败，取消尚未执行的计划任务并冻结队列。

### 风险中心

集中展示失败、限制、重复、站外导流和拒绝事件。风险中心冻结后，其他页面的发送按钮都必须失效。

## 9. 部署方式

本地开发阶段：

```text
Web      http://localhost:5173
API      http://localhost:8000
API docs http://localhost:8000/docs
Worker   独立进程
Database ./data/app.db
```

生产或长期本地运行阶段再加入：

- PostgreSQL
- Redis
- Docker Compose
- 反向代理和本地 HTTPS

API Key 只放在服务端 `.env`，不进入前端、不提交 Git、不写入数据库。

## 10. 分阶段实施

每个阶段的技术选择、原因、完成标准和暂不实现范围，详见 [phase-plan.md](phase-plan.md)。

### Phase 1：架构骨架

- 建立 API、Web、Worker 三个进程。
- 建立 SQLite 表和领域状态机。
- 前端完成概览、店铺、触达审核、风险中心。
- 用假数据跑通人工审核流程。

### Phase 2：Agent 能力

- 接入 Qwen Provider 和无需密钥的 Mock Provider。
- 实现店铺评分 Agent。
- 实现视频事实提取和评论/私信草稿 Agent。
- 增加提示版本、证据引用和人工修改记录。

### Phase 3：真实人工运营

- 手动录入真实店铺和视频链接。
- 通过官方界面操作。
- 记录真实发送结果和限制事件。
- 根据结果调整内部配额，而不是探测平台上限。

### Phase 4：可替换模型和部署

- 增加 Ollama Provider。
- 增加 PostgreSQL/Redis 配置。
- 加入备份、日志、健康检查和数据导出。
