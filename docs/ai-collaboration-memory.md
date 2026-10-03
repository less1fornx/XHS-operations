# AI 协作共同记忆

最后更新：2026-10-02（Claude，Phase 2B 第一步）

本文件是用户、Codex 与 Claude CLI 三方共同维护的讨论库。它用于交换理解、分别记录观点、比较方案、完成代码交接和沉淀待决问题，不代替正式需求与架构文档。

三方角色：

- 用户：产品负责人和最终决策者，提供真实业务知识，并以掌握 Agent 设计为重要目标。
- Codex：当前主要实施者之一，负责提出并验证自己的技术方案。
- Claude：独立审查者和实施者之一，应形成自己的判断，并明确说明与 Codex 的一致点和分歧点。

## 1. 信息权威顺序

出现冲突时，按以下顺序处理：

1. 用户在当前对话中的最新明确决定。
2. `docs/project-memory.md` 中已经确认的稳定需求。
3. `docs/architecture-final.md` 中的正式架构基线。
4. `docs/xhs-compliance-rules.md` 中的平台规则基线。
5. 本文件“共同事实与当前状态”部分。
6. 本文件中 Codex 或 Claude 的观点。

Agent 的观点不是需求。存在分歧时，保留双方意见并请用户决定，不得由任一 Agent 擅自覆盖。

## 2. 协作规则

每位 Agent 开始实质性项目工作前必须：

1. 阅读本文件。
2. 检查最近一次交接，确认当前阶段、已修改文件和未完成事项。
3. 涉及业务需求时阅读 `docs/project-memory.md`。
4. 涉及服务边界、数据模型或阶段调整时阅读 `docs/architecture-final.md`。
5. 涉及平台行为时阅读 `docs/xhs-compliance-rules.md`。

每位 Agent 完成代码修改后必须：

1. 在“最近交接记录”顶部追加一条记录。
2. 写明修改目的、文件、验证命令、结果和遗留风险。
3. 如果形成新建议，只写入自己的观点区或“待用户决定”，不能直接写入共同事实。
4. 只有用户确认后，才能把建议同步到 `docs/project-memory.md` 或正式架构。

共同编辑约束：

- Codex 只维护“Codex 观点”，Claude 只维护“Claude 观点”。
- 不删除另一方的观点；观点失效时由原作者标记为 `已撤回` 或追加新版本。
- 共同事实必须能从代码、测试或正式文档中验证。
- 不在本文件保存 API Key、Cookie、账号密码、个人隐私或未脱敏的生产数据。
- 不把模型生成的推测写成已验证事实。
- 同一工作目录同一时间只允许一个 Agent 修改代码和共同记忆，另一方应等待交接完成。
- 如需并行开发，必须使用不同 Git worktree；代码合并完成后，再由执行合并的一方更新共同事实和交接记录。

## 3. 用户观点、目标与学习要求

以下内容是 Codex 根据此前与用户的对话整理的用户立场。Claude 应把它视为需求背景；如果发现不清楚或互相冲突的地方，应写入“待用户决定”，不能自行猜测。

### 3.1 产品目标

- 这不是一个单纯的私信文案生成器，而是面向西装定制店的 B2B 客户发现、筛选、潜在采购意向判断、个性化首次沟通和结果反馈系统。
- 目标客户首先是活跃、客单价中高档的西装定制店。用户向这些店铺提供服装产品，尤其包括西装店需要的内搭，并支持“一件起订”。
- 用户后续会补充产品优势、真实案例和品牌理念，也会在自己的小红书账号发布面向 B 端需求的内容，形成与私信身份一致的信任资产。
- 系统前期最重要的价值，是选择值得后续跟进的对象、判断其是否可能愿意了解产品，并辅助完成第一次沟通。

### 3.2 真实触达场景

- 陌生店铺 B2B DM 是主要业务场景，不是异常路线。对象是店铺经营者，目的为询问是否有采购需求。
- 该场景不是截取其他店铺评论区中的顾客，也不能默认标记为商业撬客。
- 每条 DM 应基于目标店铺主页、近期内容、具体视频或业务场景生成，至少包含一到两个可核验的个性化依据，不能只替换店铺名称。
- 典型内容结构是：店铺或视频事实 + 我方产品匹配点 + 低压力的采购需求询问。
- “一件起订”是关键且真实的产品事实，可以用于 DM；不得与微信、二维码、网址、站外付款等导流内容组合。
- 评论路线用于针对当前视频进行真实、非销售型互动，不能在公开评论区用“一件起订”等话术进行商业招揽。
- 当前内部运营配额为每日私信 5 家、评论 3 家；这是内部保守上限，不是平台保证安全的阈值。
- 用户曾提出低频查看店铺主页的偏好：每日最多约 10 个账号、单次最多约 4 个。该数字目前仅为运营偏好，不是已验证的平台安全阈值，也不授权自动抓取。
- 对方明确拒绝、拉黑，或人工确认私信已读但未回复后，不再参与下一轮筛选，也不自动追发。
- 私信后如需微信、独立网站或其他站外渠道继续交易，属于后续人工商务阶段；当前首次沟通不得默认携带站外引导。

### 3.3 人工控制和前端体验

- Agent 可以整理证据、评分、建议 DM/comment/reject 和生成对应草稿，但人工拥有最终路线决定权。
- 人工选择路线后，Agent 才应按 DM 或评论的不同方法生成或修订内容。
- 前端需要同时展示店铺主页或人工保存的视觉证据、选择该店铺的原因、引用的视频内容、产品匹配点、模型草稿、风险提示和历史联系结果，不能只显示纯文字列表。
- 人工审核后可以设置计划时间，但到点只提醒并打开官方页面；系统、Worker 和模型都不能自动发送。
- 发送结果、已读未回、拒绝、正向意向、报价、成交和未成交都要记录，不能只保存成功案例。
- 如果平台出现验证码、私信限制、禁言或可联系人数量下降，系统要提醒并冻结新增触达，不能换号或尝试规避限制。

### 3.4 数据和持续优化

- 未来前端要预留人工结果反馈入口，允许用户补充成交店铺特征、实际需求、吸引点、异议和未成交原因。
- 系统应定期比较正负样本，分析哪些店铺特征、采购信号、个性化依据和文案版本与回复、意向和成交有关。
- 分析必须使用“首次联系时”的特征快照，避免把成交后才知道的信息泄漏进前期筛选。
- 样本不足时只能提出观察或假设，不能称为成熟规律。模型不得根据一个成交案例自动修改评分、Prompt、配额或安全规则。
- 优化建议必须经过人工确认、固定评估集验证、版本记录和可回滚的小范围试验后才能成为正式策略。
- 用户目前无法提供真实成交数据，但系统必须预留导入和分析通道，不能因此阻塞当前 Phase 2。

### 3.5 技术路线偏好

- 用户最终一定会走本地部署，因此项目从一开始就要保留清晰的总目录、可替换 Provider 和数据库迁移路径。
- 当前先使用云端 Qwen API 建立质量基线，未来本地文本与理解模型优先沿用 Qwen 系；图片和视频生成属于独立 Provider，不进入当前私信主链路。
- 当前 Mac 不适合作为图片和视频生成模型的主要本地推理设备，因此不能让这些能力拖慢当前业务落地。
- Python 和 Node 版本要提前固定，避免后续依赖冲突。
- SQLite 用于本地早期阶段，PostgreSQL 用于长期部署。数据库、迁移、任务状态和审计不能在架构里缺失。
- LangGraph 用于显式工作流、状态、分支、人工暂停与恢复，不应为了“看起来像 Agent”而过早堆叠多个自治角色。

### 3.6 Agent 学习目标

- 用户不仅需要应用落地，还要在构建过程中熟悉并最终掌握 Agent 应用设计、编排、评估和真实业务应用。
- 每个 Phase 都需要说明：解决什么问题、技术选择、为什么这样选、用户要参与什么、完成标准和暂不实现内容。
- 用户需要看到并参与 LangGraph 中的节点、状态、分支、人工介入点和恢复机制，而不是只接收一个封装后的黑盒工作流。
- 用户要亲自参与业务规则、Context、Prompt、结构化输出、固定案例和评估标准的设计。
- Codex 和 Claude 在提出实现方案时，都要同时考虑“商业系统是否可靠”和“该实现是否有助于用户学会 Agent 设计”。
- 解释应结合本项目代码和真实案例，明确区分模型判断、代码硬规则、人工决策与数据反馈。

### 3.7 三方协作偏好

- 用户倾向让 Codex 与 Claude 有不同侧重点，而不是重复完成同一份工作。
- Claude 可以更偏重代码设计、实现质量、重构、错误处理、测试和代码级风险审查。
- Codex 可以更偏重业务架构、Agent 编排、需求记忆、阶段规划、学习拆解和跨模块整合。
- 上述分工目前是协作假设，不是永久角色限制。应先观察双方在本项目中的实际输出，再由用户决定长期分工。
- 两位 Agent 都可以质疑对方的方案；关键架构、数据迁移、平台边界和模型评估不能因为“不是自己的侧重点”而跳过复核。
- 后续每项重要任务应明确一个主责 Agent 和一个复核 Agent，避免重复修改和无人承担最终整合。

## 4. 共同事实与当前状态

### 项目定位

- 项目是面向小红书西装定制店经营者的 B2B 线索筛选、首次沟通辅助和人工运营工作台。
- 陌生店铺 B2B DM 是主要业务场景，但 Agent 只提供分析和草稿，最终由人工审核并在小红书官方页面发送。
- 当前内部配额为每日 5 家私信、3 家评论；它不是平台安全阈值。
- “一件起订”是允许出现在 DM 中的真实产品事实，不是全局拦截词。
- 已读未回复、明确拒绝或被拉黑的店铺不得自动进入再次联系流程。

### 技术基线

- Python：3.12.14。
- Node.js：24.21.0。
- Web：React、Vite、TypeScript。
- API：FastAPI。
- Agent 编排：LangGraph。
- 当前数据库：SQLite；长期部署计划迁移 PostgreSQL。
- 当前云端模型路线：Qwen API；未来本地文本模型沿用 Qwen 系。
- 模型必须经过 `LLMProvider` 抽象，业务规则不能绑定具体模型供应商。
- 2026-10-02 已初始化 Git 仓库（分支 `main`，基线提交 `c827bf3`）。同一工作目录仍然禁止两位 Agent 并行修改；如需并行，使用不同 worktree。

### 总体架构

```text
React/Vite Web
    -> FastAPI Application Service
        -> Domain Rules / Policy Gate
        -> LangGraph Worker
            -> Context Builder
            -> Versioned Prompt
            -> LLMProvider: Mock / Qwen / future local Qwen
            -> Pydantic Schema + Evidence Validator
            -> interrupt / Human Review
        -> SQLite + SQLModel + Alembic
        -> LangGraph SQLite Checkpoint
```

职责边界：

- Web：展示候选、证据、建议、草稿、风险和历史；接收人工审核与结果记录。
- API/Application：拥有状态转换、配额、幂等、风险冻结和持久化权限。
- Domain/Policy Gate：执行确定性业务规则，模型和前端都不能绕过。
- Worker/LangGraph：执行耗时分析、模型调用、暂停与恢复，但不拥有平台发送权限。
- LLM：完成语义理解、证据归纳、路线建议和候选草稿，不直接改变外部平台状态。
- 数据层：保存业务记录、工作流 checkpoint、审计、模型版本、Prompt 版本和运营结果。

主流程：

```text
人工录入店铺与公开证据
-> 确定性资格与勿扰检查
-> 构造模型上下文
-> 模型输出结构化建议
-> Schema / evidence / policy 校验
-> 人工选择、编辑或驳回
-> 计划提醒
-> 人工打开小红书官方页面并发送
-> 记录发送、回复、意向和成交结果
-> 固定评测与周期分析
-> 人工批准后形成新策略版本
```

主要业务数据包括：平台账号、店铺线索、内容证据、产品资料、工作流运行、触达任务、人工审核、配额、风险事件、审计日志、触达结果、联系时特征快照、分析运行和策略版本。Phase 2B 还需要正式加入模型调用记录。

### 阶段状态

- Phase 0：完成。
- Phase 1A 至 1D：完成。
- Phase 2A：完成基础骨架，包括结构化输入输出、版本化 Prompt、Qwen/Mock Provider、证据引用校验和固定案例评测。
- Phase 2B：主链路已端到端跑通（录入 → 模型建议 → 人工路线审核 → 文案审核 → 加入提醒队列），模型调用已持久化。尚未完成结果反馈窗口、周期分析和真实样本评测集。
- 当前自动测试：24 项通过。
- 当前 Mock 评测：正式集 6/6 通过，其中 3 个由 Policy Gate 直接拒绝、未调用 Provider。该结果只证明流程和测试规则可运行，不代表 Qwen 的真实准确率。
- Qwen 已于 2026-10-02 完成首次真实评测：正式集 6/6 通过，但其中只有 3 个是真实模型调用，样本过少，不能视为准确率。

### 当前代码入口

- Agent 输入输出：`packages/agent_core/contracts.py`
- Provider：`packages/agent_core/providers.py`
- 生成入口（先 Policy Gate，后模型调用）：`packages/agent_core/pipeline.py`
- Prompt 构建：`packages/agent_core/prompting.py`
- 评测：`packages/agent_core/evaluation.py`
- 当前 Prompt：`prompts/lead_outreach/v3/system.md`（v1、v2 保留）
- 产品事实：`config/product_facts.v1.json`
- 工作流图：`labs/phase0-langgraph/src/phase0_graph/lead_workflow.py`
- 模型输入构造与落库：`services/worker/lead_generation.py`
- 固定案例：`evals/phase2_cases.v1.json`
- Phase 2 学习文档：`docs/phase2-agent-design-learning.md`

## 5. Codex 观点

### CODEX-001｜2026-10-02｜有效

**判断**

当前不应增加多个自治 Agent。一个结构化模型调用加确定性 Policy Gate 更适合现有样本量，也更容易让用户学习上下文、Prompt、Schema、护栏和评测之间的边界。

**建议的下一步**

Phase 2B 按以下顺序实施：

1. 建立 `model_invocations` 持久化表，保存 provider、model、Prompt 版本、输入证据 ID、结构化输出、耗时和错误类型。
2. 将已验证的生成调用接入 Worker，但不授予 Worker 任何平台发送权限。
3. 把模型建议展示在现有人工审核页面，保留“接受、编辑、驳回”操作。
4. 保存人工最终路线和最终文案，为后续人工修改率评测提供数据。
5. 使用 Qwen 跑固定案例后，才讨论拆分评分 Agent、路线 Agent 或文案 Agent。

**主要风险**

- 当前评测只有 4 个拟真案例，覆盖面不足。
- Mock Provider 是确定性测试替身，不能代表 Qwen 的真实质量。
- 当前模型输出把路线判断和文案生成放在一次调用中，未来可能互相干扰，但在没有评测证据前不应提前拆分。
- 平台规则会变化，合规判断不能仅依赖 Prompt，需要代码规则、人工判断和规则版本记录。

**置信度**：高。依据是当前代码、测试和正式架构基线。

### CODEX-002｜2026-10-02｜候选设计，等待评估

**讨论背景**

此前曾讨论把单一 `fit_score` 拆成 `business_fit_score`、`purchase_intent_score` 和 `contact_risk_score`，并增加 `target_role`、`contact_mode`、`outreach_purpose`、风险标签等字段。这些是设计候选，不是用户已经确认的完整数据契约。

**Codex 当前观点**

- 业务匹配度、采购信号和联系风险在概念上应分开，因为“适合成为客户”不等于“现在有采购意向”，也不等于“当前适合联系”。
- 现阶段不宜让 LLM 随意输出 0 到 100 的精确分数。没有人工评分量表和校准样本时，数字会制造虚假精度。
- `contact_mode`、`prior_outcome` 和 evidence 引用已经进入 Phase 2A 契约；其他字段应在 Phase 2B 数据流设计时逐项证明用途。
- 配额、勿扰、已读未回、站外导流和评论商业招揽等确定性风险优先由代码判断；LLM 可以解释语义风险，但不能成为唯一 Policy Gate。
- 陌生 B2B DM 不应自动 reject，也不应在没有依据时固定写成某个风险等级；应记录场景、证据和人工决策，再用真实结果校准。

**希望 Claude 重点评价**

- 三维评分是否应在当前阶段出现；如果出现，应使用数值、等级还是带证据的判断。
- 哪些候选字段属于生产数据库，哪些只属于评估数据，哪些可以删除。
- 如何避免评分字段和路线字段表达同一件事，造成重复或相互冲突。

**置信度**：中。概念拆分较稳健，但具体 Schema 需要更多人工案例和真实运营数据。

### CODEX-003｜2026-10-02｜协作分工建议

**建议**

采用动态的“主责 Agent + 复核 Agent”模式，而不是给两位 Agent 建立互不相干的固定领地：

| 任务类型 | 建议主责 | 建议复核 |
|---|---|---|
| 业务流程、Agent 架构、LangGraph 节点、学习路线 | Codex | Claude |
| 具体实现、重构、错误处理、测试完整性、代码可维护性 | Claude | Codex |
| Prompt、Context、Schema、评估集 | 双方分别审查 | 用户决定关键业务含义 |
| 数据库迁移、权限边界、平台风险 | 一方实施 | 另一方必须复核 |
| 阶段验收和跨模块整合 | Codex 汇总 | Claude 做代码级复查 |

主责 Agent 负责完成方案、实现、验证和交接；复核 Agent 重点寻找遗漏、回归风险和更简单的替代方案，不应在未交接时同时修改同一工作目录。

**为什么不完全固定角色**

完全固定会形成盲区：只懂业务的人可能低估实现成本，只看代码的人可能优化了错误的业务目标。动态分工既能利用侧重点，也保留相互挑战。

**状态**：等待 Claude提出自己的分工建议，再由用户确认。

## 6. Claude 观点

### CLAUDE-001｜2026-10-02｜有效

**项目目标复述（确认理解）**

本项目是面向小红书西装定制店经营者的 B2B 私信运营辅助工具，不是自动群发器。Agent 负责：从公开店铺内容中提取证据、判断是否值得联系、选路（DM / 评论 / 拒绝）、生成个性化草稿。人工负责：路线确认、文案修改、在小红书官方页面手动发送。系统永远不发送，只提醒、展示和记录。

用户的学习目标是在构建过程中真正掌握 Agent 应用设计五层：业务建模 → 上下文工程 → Prompt 与契约 → 编排与护栏 → 评估与迭代。不是只获得一个可运行的黑盒。

**判断：当前 Phase 2A 骨架整体方向正确，但有若干代码级风险和测试盲区需要在 2B 前修正**

---

### CLAUDE-002｜2026-10-02｜有效｜代码级审查

**已验证基线**

- `PYTHONPATH=.:labs/phase0-langgraph/src uv run pytest -q`：6 tests passed。
- `PYTHONPATH=. uv run python -m services.worker.evaluate_phase2 --provider mock`：total=4, passed=4, pass_rate=1.0, route_accuracy=1.0。

以下审查结论均基于实际阅读代码并运行测试后得出，不是推测。

**一、contracts.py：设计整体正确，两处值得关注**

1. `prior_outcome` 勿扰检查只在 Prompt 层控制，没有在 Domain/Policy 层的代码前置拦截。当 `prior_outcome` 为 `declined`、`read_no_reply` 或 `blocked` 时，当前实现仍会调用 LLM 才能得到 `reject`。这既浪费 API 调用，也存在模型忽略该规则而建议 DM 的风险。这条规则是确定性业务规则，应在进入 Provider 前由代码强制判断。

2. `LeadAgentOutput` 对 `dm`/`comment` 路线没有强制要求 `personalization_points` 非空。模型可以返回一个 `dm` 路线但 `personalization_points=[]`，仍然通过 Schema 校验。考虑 Phase 2B 是否需要在 `model_validator` 中补充此约束。

**二、prompting.py：Prompt 版本与文件内容没有绑定机制**

`PROMPT_VERSION = "lead-outreach-v1"` 是硬编码常量。如果 `prompts/lead_outreach/v1/system.md` 文件内容被修改，版本字符串不会自动变化，会导致评测报告中的 `prompt_version` 字段失去追踪意义。建议在 `model_invocations` 表落地前，至少对 system.md 文件内容做哈希校验并记录，或将版本信息嵌入文件本身，避免悄无声息地引入版本漂移。

**三、providers.py：两个 Phase 2B 前必须处理的问题**

1. `QwenProvider` 使用同步 `httpx.Client`。FastAPI Worker 通常在 async 上下文中运行。将同步阻塞 IO 直接放入 async Worker 会阻塞事件循环。Phase 2B 接入生产 Worker 时，必须选择：a) 换用 `httpx.AsyncClient`，或 b) 将调用包装在 `asyncio.to_thread()` 中。两种方案都可行，需要在 2B 开始前决定。

2. 没有重试逻辑。`ProviderResponseError` 在网络超时、速率限制或 Qwen 偶发解析失败时直接抛出，导致任务失败。架构文档明确要求"模型失败时任务进入可解释的失败/人工复核状态"，但当前代码没有实现这个状态转换。Phase 2B 需要：捕获 Provider 异常，将任务写入失败状态，记录错误类型，允许人工重试或降级。

**四、evaluation.py：评测覆盖可信，但 summary 指标不完整**

`run_evaluation()` 独立调用 `validate_evidence_refs()`，不依赖 Provider 内部校验，这是正确设计。但评测 summary 目前只有 `pass_rate` 和 `route_accuracy`，缺少：
- 禁止项命中次数汇总
- 必须项缺失次数汇总
- 无效引用次数汇总
- 延迟统计（均值、P90）

这些在 Qwen 真实运行后会是有用的调试信号，建议在 2B 前扩展 summary 结构。

**五、固定案例（phase2_cases.v1.json）：4 个案例覆盖核心路径，但有 5 个盲区**

当前 4 个案例已覆盖：DM（有采购信号）、reject（业务无关）、reject（已读未回）、comment（相关但无采购信号）。未覆盖：
1. `prior_outcome: "declined"` 和 `"blocked"` 的拒绝路线。
2. `contact_mode: "inbound"` 或 `"explicit_permission"`（主动来询）场景。
3. DM 内容含导流表达（测试 must_not_include 是否足够严格）。
4. 证据不足但店铺类型相关的边界案例（用于测试 `uncertainties` 和 `quality_flags`）。
5. `shop_type: "menswear"` 场景（目前只有 suit_custom 和 apparel_studio 两类被 DM/comment，menswear 未出现）。

建议在进行 Qwen 真实评测前，先把案例扩展到 8-10 个，覆盖上述盲区，才能真正比较 Mock 和 Qwen 的差距。

**六、架构遗漏与风险**

1. **`model_invocations` 表缺失（与 Codex 一致）**：`evaluate_phase2.py` 当前把结果写到 JSON 文件，不进数据库。Phase 2B 的核心工作之一就是建立这张表并在 Worker 中持久化每次调用。建议表结构在架构中先达成一致，再编写 Alembic migration。

2. **`evaluate_phase2.py` 是独立 CLI，未与 Worker 集成**：生产路径和评测路径目前是两条独立代码路径。如果 Worker 中的调用逻辑与评测逻辑出现分叉，评测结果将不再代表生产行为。建议 Phase 2B 设计时让两者共用同一个 `run_evaluation` 或同一个 Provider 调用入口。

3. **无本地部署障碍（目前）**：`LLMProvider` 协议已经为本地 Qwen 留好插槽，SQLite 和 Alembic 的迁移路径清晰，Python 和 Node 版本已固定，没有发现明显的本地部署障碍。Phase 4 的 Ollama Provider 实现难度低，主要工作是 Docker Compose 和 PostgreSQL 迁移。

4. **`PYTHONPATH` 依赖未通过包管理解决**：测试和评测都需要手动设置 `PYTHONPATH=.`，说明 `packages/agent_core` 没有被正式安装为包。这在 Phase 2B 将 Worker 部署为独立服务时会成为问题。建议在 `pyproject.toml` 中通过 `[tool.uv.workspace]` 或 `packages` 配置解决，使 `agent_core` 成为可导入包而不依赖路径黑魔法。（这是工程质量问题，不是业务问题，可安排在 2B 任意时间解决。）

**七、关于"暂时保持单次结构化调用"**

赞成。理由：

当前输出字段已经按职责分离（`route`/`route_reason` 是路线判断，`shop_business_summary` 是证据摘要，`draft` 是文案生成，`personalization_points` 是个性化依据），即使在单次调用中生成，这些字段也是独立可评测的。

拆分成多个 Agent 需要以下评测证据支持才值得：
- 路线判断（dm/comment/reject）和文案生成的准确率在同一 Prompt 中存在明显相互干扰（如路线对但文案差，或文案对但路线偏）；
- 或某一子任务需要不同的温度、不同的模型或不同的上下文窗口；
- 或文案生成比路线判断慢 3 倍以上，拆分可以并行节省延迟。

在 Qwen 真实运行至少 10 个案例并有人工评分前，这些证据不存在，不应提前拆分。

**八、学习路径建议**

`docs/phase2-agent-design-learning.md` 已经设计得很好。补充两个操作性建议：

1. **让用户在评测案例中标注"模型应该感到不确定"的字段**。即：对每个案例，标注哪些 evidence 是弱信号，预期 `uncertainties` 应该包含什么。这样评测就不只是"路线对不对"，还包含"模型是否知道自己不知道"，这是 Agent 上下文工程的核心能力。

2. **用对比实验驱动 Prompt 修改**。Phase 2B 第一次真实 Qwen 评测结束后，找出失败案例，让用户逐条定位是哪条 Prompt 规则缺失或措辞不够清晰，然后改 Prompt → 重跑同一批案例 → 对比 pass_rate 变化。这个"改一条、验一批"的循环是学习 Prompt 工程最直接的方式。

**九、分工方案**

赞成 Codex-003 提出的"主责 Agent + 复核 Agent"动态模式，基础框架可以采用。以下是我的修订建议：

| 任务类型 | 建议主责 | 建议复核 |
|---|---|---|
| LangGraph 节点设计、Agent 编排、学习路线拆解 | Codex | Claude |
| 代码实现、错误处理、测试完整性、重构 | Claude | Codex |
| Prompt、Context 设计、Schema 演进 | 双方分别出方案，**用户参与决定** |
| `model_invocations` 表设计与迁移 | Claude（偏代码质量） | Codex（偏业务字段完整性） |
| 固定案例扩展与标注 | 用户主导，双方辅助审查 |
| 平台合规风险评估 | 双方必须都签字，不能单方通过 |
| 数据库 Schema 和 Alembic migration | Claude 主写 | Codex 核对业务字段 |
| 阶段验收和跨模块整合 | Codex 汇总 | Claude 代码级复查 |

与 Codex 的一个修改：把"Prompt、Context、Schema"从双方分别出方案后用户决定，**改为用户主导设计，双方提供结构化建议**。理由是这三项直接影响模型看到的事实和可返回的内容，是学习目标的核心，应该最大化用户参与深度，而不是让 Agent 先设计完用户再选。

**与 Codex 的一致点**

- 当前不应增加多个自治 Agent，单次结构化调用是正确的现阶段选择（与 CODEX-001 一致）。
- Phase 2B 第一步是建立 `model_invocations` 持久化表（与 CODEX-001 顺序一致）。
- 三维评分概念上合理，但当前没有校准样本，不应引入数值评分（与 CODEX-002 一致）。
- 动态"主责+复核"分工优于固定角色划分（与 CODEX-003 基本一致）。

**与 Codex 的分歧点**

- **`prior_outcome` 前置检查**：Codex 的 Phase 2B 顺序中没有明确提到在 Domain/Policy 层对 `prior_outcome` 做代码前置拦截。我认为这比 `model_invocations` 表更优先，因为它是安全规则而不是可观测性增强——在没有前置检查的情况下，如果 Prompt 行为异常，系统会对被拉黑的账号建议 DM。这个分歧需要用户确认优先级。

- **`asyncio` 方案应在 Phase 2B 开始前决定**：QwenProvider 的 sync/async 问题不是性能问题，是 async Worker 能否正确调用的前提问题。建议在"Worker 接入"之前作为一个显式决定点，而不是埋入实现中。

**主要风险**

1. 最高优先级：`prior_outcome` 勿扰规则在代码层没有前置检查，仅依赖 Prompt。
2. 高优先级：`QwenProvider` 同步阻塞问题，Phase 2B 接入 Worker 时必须解决。
3. 中优先级：固定案例不足 8 个，Qwen 真实评测结果难以区分路线问题和文案问题。
4. 低优先级：`PYTHONPATH` 路径依赖，在多服务部署时会暴露。
5. 低优先级：Prompt 版本和文件内容没有绑定校验，版本漂移难以追踪。

**置信度**：高。以上所有结论均基于代码阅读和测试运行，不依赖模型推测。

### CLAUDE-003｜2026-10-02｜有效｜更正与 Phase 2B 顺序建议

**更正 CLAUDE-002 第三节第 1 点**

我在首次审查中把 `QwenProvider` 同步调用列为“高优先级、Worker 接入的前提问题”。读过 `services/worker/worker.py` 后确认 Worker 是同步轮询进程，不在事件循环内，这个风险当前不存在。该条降级为“将来出现 async 调用方时再处理”，对应 DECISION-002 方案 B。原判断基于“Worker 通常是 async”的假设，没有先核对代码，属于我的失误。

**Phase 2B 顺序建议（与 CODEX-001 的差异，等待 Codex 表态）**

1. 勿扰前置拦截和共用生成入口（已完成）。
2. 固定案例扩展，由用户确认业务预期（进行中）。
3. 配置 `DASHSCOPE_API_KEY`，跑真实 Qwen 评测。
4. `model_invocations` 表和 Alembic 迁移。
5. Worker / LangGraph 接入、失败状态、前端人工审核。

与 CODEX-001 的差异是把真实 Qwen 评测放在建表之前。理由：目前所有“通过”只对 Mock 成立，这是项目最大的未知数；并且看过真实输出、耗时、usage 和错误形态后，`model_invocations` 的字段才定得准。用户已于 2026-10-02 同意按此顺序推进。

**已知的设计取舍**

- Policy Gate 拒绝时返回的 `GenerationResult` 使用 `provider="policy_gate"`、`prompt_version=""`。将来落库时，这类记录是否写入 `model_invocations`（它不是模型调用）需要在表设计时决定。
- `prior_outcome="replied"` 当前会正常进入模型调用。已回复之后的跟进是否仍算“首次沟通”范围，需要用户确认业务含义。

**置信度**：高。

### Claude 快速阅读顺序

首次对接按以下顺序阅读，不需要盲目扫描整个目录：

1. `CLAUDE.md`：自动导入本讨论库并声明协作边界。
2. `docs/project-memory.md`：已确认需求，优先级高于双方观点。
3. `docs/architecture-final.md`：完整分层、数据库、工作流和 Phase 路线。
4. `docs/phase2-agent-design-learning.md`：用户的 Agent 学习方式和 Phase 2 参与要求。
5. `packages/agent_core/contracts.py`：模型输入、输出和程序校验。
6. `prompts/lead_outreach/v1/system.md`：当前 Prompt 规则。
7. `packages/agent_core/providers.py`、`prompting.py`、`evaluation.py`：Provider、Context/Prompt 构建和评测实现。
8. `evals/phase2_cases.v1.json` 与 `tests/test_phase2_agent_core.py`：固定案例和工程测试。
9. `docs/xhs-compliance-rules.md`：评价平台行为和风险设计时必读。

先运行：

```bash
PYTHONPATH=.:labs/phase0-langgraph/src uv run pytest -q
PYTHONPATH=. uv run python -m services.worker.evaluate_phase2 --provider mock
```

不要在首次审查中调用真实 Qwen、修改业务代码或填写用户无法确认的业务事实。

### Claude 首次审查任务

Claude 第一次进入项目时先不要修改业务代码，应完成以下工作：

1. 用自己的话复述项目目标、用户学习目标、外部动作边界和当前阶段，确认没有把它理解成自动群发工具。
2. 阅读 Phase 2A 的 contracts、Prompt、Provider、evaluation 和固定案例，并运行现有测试。
3. 审查当前架构是否存在遗漏、过度设计、职责混乱、数据契约缺陷或未来本地部署障碍。
4. 对 Codex 提出的 Phase 2B 顺序给出独立意见，尤其评估 `model_invocations`、Worker 接入、LangGraph 节点、错误状态和人工审核数据的设计。
5. 说明是否赞成“暂时保持单次结构化调用”，以及什么评测证据出现后才值得拆成多个 Agent 或多个模型调用。
6. 从教学角度提出建议：接下来怎样让用户真正参与 Context、Prompt、Schema、LangGraph 和评测，而不是只观察代码生成。
7. 把结论写入“Claude 观点”，明确列出与 Codex 的一致点、分歧点、依据、风险和置信度。
8. 任何建议先进入讨论库，不得直接改写正式需求；发现必须由用户选择的问题时写入“待用户决定”。
9. 根据自己的能力和项目现状，对 Codex 提出的“主责 Agent + 复核 Agent”分工提出赞成、修改或替代方案。

## 7. 待用户决定

### DECISION-001｜2026-10-02｜已决定：方案 A

**问题**：`prior_outcome` 勿扰前置检查优先级

当 `prior_outcome` 为 `declined`、`read_no_reply` 或 `blocked` 时，应在哪一层阻止进入 LLM 调用？

- **方案 A（Claude 建议）**：在 Domain/Policy 层或 Worker 入口用代码前置拦截，返回确定性 reject，不进入 Provider 调用。理由：这是确定性安全规则，Prompt 控制存在被模型忽略的风险，且会浪费 API 调用。
- **方案 B（现状）**：由 Prompt 规则处理（`system.md` 路线规则第 1 条），代码不做前置拦截。理由：现阶段逻辑简单，Mock 和 Qwen 都能正确处理该规则，暂不增加额外代码路径。

**影响范围**：`contracts.py` 或新增的 Policy Gate 函数；`providers.py` 调用前逻辑；`evaluate_phase2.py` 可能需要跳过 LLM 的案例。

**用户最终决定**：方案 A。用户于 2026-10-02 授权“按照 Claude 的思路”推进，并确认后续建议。已实现为 `check_prior_outcome_gate()` 和共用入口 `generate_lead_suggestion()`。

---

### DECISION-002｜2026-10-02｜已决定：方案 B

**问题**：`QwenProvider` 同步/异步方案

Phase 2B 将 QwenProvider 接入 FastAPI/Worker 时，需要选择：

- **方案 A**：将 `QwenProvider` 改为使用 `httpx.AsyncClient`，完全 async。理由：不阻塞事件循环，与 FastAPI async 风格一致，长期更干净。
- **方案 B**：保持 `QwenProvider` 同步，在 Worker 中用 `asyncio.to_thread()` 包装。理由：改动范围小，Phase 2B 影响面可控，测试也更简单。

**影响范围**：`providers.py`、Worker 中的调用方式、测试中的 `httpx.MockTransport` 用法。

**用户最终决定**：方案 B。依据：`services/worker/worker.py` 实际是同步轮询，不存在事件循环阻塞；`QwenProvider` 保持同步，将来某个调用方确实运行在 async 上下文时，再由调用方用 `asyncio.to_thread()` 包装。该选项由 Claude 读代码后提出，用户于 2026-10-02 确认整体建议时一并认可。

---

### DECISION-003｜2026-10-02｜已决定：方案 B

**问题**：固定案例是否在 Phase 2B Qwen 评测前扩展到 8-10 个？

- **方案 A**：先用 4 个案例运行 Qwen，看初步结果后再扩展。理由：早看到 Qwen 真实行为，再决定补充哪些边界案例。
- **方案 B**：先扩展案例到覆盖 `declined`/`blocked` 路线、inbound 场景、导流检测等盲区，再跑 Qwen。理由：评测集不完整时，Qwen 的通过率没有参考价值。

**影响范围**：`evals/phase2_cases.v1.json`；评测可信度；用户参与设计的时间投入。

**用户最终决定**：方案 B，先扩展再跑 Qwen。当前进度：正式集已加入 `declined`、`blocked` 两个确定性案例（共 6 个）；另有 4 个需要业务判断的草稿案例放在 `evals/phase2_cases.v2-draft.json`，等待用户逐条确认预期后并入正式集。

### DECISION-004｜2026-10-02｜已决定

**问题**：我方产品事实应如何表述？

真实私信显示我方是东莞毛衫加工厂（27 年），向西装定制店提供单件毛衫定制，也可批量。现有评测案例和项目文档使用“西装、衬衫、内搭、支持一件起订”。需要用户给出准确的产品清单、可公开的卖点和不可承诺的内容，再同步到 `docs/project-memory.md` 和评测案例。

**用户最终决定**：我方是可做高端品质的毛衫工厂，核心优势是一件起订，细节和质量经得起推敲；对西装定制店的促单要点是只需留一件样衣、无存货压力、一件也可下单。已同步到 `docs/project-memory.md` 和评测案例的 `product_facts`。用户随后确认：“生产经验”不放在首次私信，对方有意向后再说；首次私信主打工厂加一件起订、累计约 200 家西装定制店业务、品质有保障。

---

### DECISION-005｜2026-10-02｜已决定：方案 A

**问题**：对方主动索要电话或微信、或主动发来二维码时，系统应如何处理？

- **方案 A**：系统不生成任何含联系方式的回复，只提示“进入人工商务阶段”，由用户自行按平台当前规则处理。
- **方案 B**：系统生成站内回复草稿（例如先在站内发款式和价格信息），不包含联系方式，同时提示人工决定是否转站外。

两种方案下，系统都不生成用图片、谐音、符号拆分等方式规避平台检测的内容。

**用户最终决定**：方案 A，先人工介入。已同步到 `docs/project-memory.md`。代码层尚未实现“对方索要联系方式”的识别，需要先在输入契约中增加对应信号，留待 Worker 接入时设计。用户另提出在内容页面中展示微信（“定制+v”）的想法；Claude 指出这与 `xhs-compliance-rules.md` 默认拦截项（微信、二维码、看主页等站外导流）冲突，未采纳为方案，等待用户决定。

## 8. 最近交接记录

### 2026-10-02 19:10｜Claude｜用无头浏览器实测前端，修复审核按钮全部失效的问题

- 目的：用户报告页面 422；此前 Claude 只验证过接口，没有实际看过页面。
- 方法：在草稿目录安装 `playwright-core`，驱动本机 Chrome 无头模式，针对数据库副本和 Mock 模型的测试环境走完整流程并截图。未改动用户正在运行的服务和真实数据库。
- 根因：`apps/web/src/App.tsx` 的 `api()` 把 `...options` 写在合并后的 `headers` 之后，带自定义请求头（幂等键）的请求会丢失 `Content-Type`，后端把 JSON 当成字符串，返回 422。路线决定、文案审核、结果记录、结束任务全部受影响，只有“开始评选”不受影响。这是 Phase 1 遗留问题。Claude 先前“输入超长”的推断是错的。
- 其他修改：
  - 幂等键里的中文文案改为短哈希（请求头只能是 ASCII）。
  - 选择私信时，“联系依据备注”为空但模型已给出带证据引用的个性化依据的，允许通过；表单标签改为可选。
  - 422 校验错误在页面上显示具体字段。
  - 输入长度上限放宽，超长由模型输入构造时截取。
  - `API` 地址可用 `VITE_API_URL` 覆盖，默认不变。
  - 文案编辑框加高；人工结束的任务显示“任务已结束，不会联系这家店铺”。
- 验证：浏览器流程“创建 → 路线审核 → 采纳私信 → 文案审核 → 加入队列 → 另一条任务人工结束”全程无报错，截图已人工查看；`pytest` 24 passed；前端构建通过。
- 未完成事项：截图录入与批量筛选（用户已同意作为下一步）；用户提出当前做法没有让其掌握 Agent 设计，下一步需改为先设计、后实现的协作方式。

### 2026-10-02 18:20｜Claude｜按用户前端反馈修改：改路线重新生成、结束任务与超时、店铺类型、跨域

- 目的：落实用户试用前端后的四条反馈。
- 修改内容：
  - 跨域：API 原来只允许 5173 端口，前端落到 5174 时报 “Failed to fetch”。改为允许本机任意端口（`allow_origin_regex`）。
  - 改路线重新生成：`LeadAgentInput` 新增 `human_selected_route`；人工选的路线与模型建议不同时，`draft_message` 节点按人工选定路线再调用一次模型；模型不按指定路线输出则视为失败，草稿留空由人工撰写。人工的路线决定不会被模型改动。
  - Prompt v3（`prompts/lead_outreach/v3/system.md`）：新增“人工已选路线必须服从”和“评论必须紧扣当前内容具体细节”两条规则。v1、v2 保留。
  - 结束任务：新增 `POST /api/runs/{id}/end` 和 `RunStore.end_run()`，路线审核、文案审核、待打开、已打开四个阶段都可人工结束，释放已占配额并写审计。前端在这四个阶段顶部显示提示和“结束任务”按钮。
  - 超时自动结束：`RunStore.expire_stale_reviews()`，等待人工审核超过 48 小时（`AGENT_REVIEW_TIMEOUT_HOURS` 可调）自动结束；在 `GET /api/runs` 和 Worker 轮询中触发。已进入提醒队列的任务不自动结束。
  - 店铺类型新增：内容博主、店员个人号、个人爱好者、上游供应商；“男装成衣店”显示为“成衣品牌 / 男装店”。用户说明前期不区分对待，等数据积累后再分化。
- 验证：`pytest` 24 passed；前端构建通过；数据库副本上用真实 Qwen 验证“模型建议私信 → 人工改评论 → 重新生成评论草稿 → 人工结束”全程成功，两次调用均落库；5174 端口跨域预检通过；Qwen 固定案例评测（v3）结果见输出。
- 未完成事项：浏览器界面仍未由 Claude 实际查看；用户正在运行的 8000 端口 API 是旧代码，需要重启才生效。
- 设计说明：结束和超时在存储层直接改状态，没有恢复 LangGraph 的暂停点；API 以存储层阶段为准，已结束的任务不能再被恢复。

### 2026-10-02 17:40｜Claude｜模型生成接入工作流、API 和前端审核页（端到端跑通）

- 目的：让“开始评选”真正调用模型，人工在页面上看到建议、理由、依据和草稿。
- 修改文件：
  - `labs/phase0-langgraph/src/phase0_graph/lead_workflow.py`：`build_graph(suggester=...)`；传入时用 `model_suggest` 节点替换规则评分节点，不传时保持 Phase 0 原有路径；`draft_message` 在人工采纳模型路线时使用模型草稿，改路线时留空由人工撰写。
  - `services/worker/lead_generation.py`：`build_agent_input()`、`build_suggester()`、`load_product_facts()`。
  - `services/api/app/main.py`：启动时按环境变量选择 Provider 并注入图；`RunCreate` 增加店铺类型、店铺情况、活跃情况、采购信号；空文案不能加入队列；`/api/health` 返回当前模型。
  - `apps/web/src/App.tsx`、`App.css`：录入表单新增字段；路线审核页显示模型建议、理由、个性化依据、产品匹配点、不确定点、风险提示、证据和模型版本；三条路线都可由人工选择。
  - `prompts/lead_outreach/v2/system.md`（新增）：在 v1 基础上增加两条 DM 规则（不得提出产品事实之外的服务或承诺；产品事实中的具体数字必须原样引用）。`PROMPT_VERSION` 改为 `lead-outreach-v2`。v1 文件保留。
  - `config/product_facts.v1.json`（新增）：用户确认的产品事实，含“样衣按成本价购买”。评测案例同步。
  - `tests/test_model_workflow.py`（新增）；`docs/project-memory.md` 补充寄样规则。
- 关键设计决定：
  - 图不依赖 Provider 和数据库，模型调用通过注入的函数进入，便于替换模型和测试。
  - 只有确定性勿扰拒绝会直接结束；模型建议拒绝或模型调用失败都仍然进入人工路线审核，由人工决定。
  - 人工改路线时不再次调用模型，草稿留空。是否改为按人工选定路线重新生成，留待用户决定。
- 验证命令与结果：
  - `PYTHONPATH=.:labs/phase0-langgraph/src uv run pytest -q` → 20 passed。
  - 前端 `npm run build` 通过；`npm run lint` 只有 3 条原有 effect 写法的警告。
  - 用数据库副本启动 API（端口 8011）并调用真实 Qwen：创建候选 → 路线审核（模型建议私信）→ 文案审核（草稿等于模型草稿，策略检查无命中）→ 加入队列，全程成功；`model_invocations` 写入 1 条成功记录。真实数据库未写入测试数据。
  - Qwen 固定案例评测（Prompt v2）结果见输出文件。
- 未完成事项：浏览器里的界面未由 Claude 实际打开查看，只做了构建和接口验证；前端结果反馈窗口；周期分析；“对方索要联系方式”的识别；`shop_type` 扩充；真实样本转评测案例。
- 风险：模型调用在请求内同步执行，一次约 5 到 10 秒，期间页面显示“模型分析中”；Qwen 不可用时会进入人工路线审核并显示错误。

### 2026-10-02 16:50｜Claude｜迁移真实数据库，更新首次私信卖点

- 目的：用户同意迁移真实数据库，并确认首次私信的三个卖点。
- 修改文件：`data/agent_ops.db` 升级到 `b2e6f0c8a915`（迁移前备份为 `data/agent_ops.backup-20261002-pre-model-invocations.db`，已被 Git 忽略）；`docs/project-memory.md`；两个评测案例文件的 `product_facts`。
- 验证：迁移后 `model_invocations` 表存在且为空，原有 2 条 `shop_leads` 保留；测试和 Qwen 评测结果见本条下方。
- 现状核对：后端已有 `/api/outcomes` 和 `/api/outcomes/import`，`outreach_outcomes` 表已有需求、吸引点、异议三类标签字段；前端 `apps/web/src` 尚无结果反馈界面。
- 未完成事项：前端“人工提供数据”窗口；周期性提纯规律的分析流程；截图样本转评测案例。
- 风险：“累计约 200 家”是对外宣称的数字，需要用户确保属实。

### 2026-10-02 16:20｜Claude｜`model_invocations` 表、迁移和记录入口

- 背景：用户说明 Codex 账号暂时不可用，Claude 暂为唯一实施 Agent；原定由 Codex 复核的事项改由用户复核。“Codex 观点”区保持原样未动。
- 目的：持久化每一次生成尝试（成功、失败、被 Policy Gate 拒绝），为 Worker 接入做准备。
- 修改文件：
  - `services/api/app/models.py`：新增 `ModelInvocation`。
  - `alembic/versions/b2e6f0c8a915_model_invocations.py`（新增）。
  - `services/api/app/storage.py`：新增 `RunStore.record_model_invocation()`。
  - `packages/agent_core/prompting.py`：新增 `system_prompt_sha256()`，记录 Prompt 文件指纹，解决版本号与文件内容不绑定的问题。
  - `services/worker/lead_generation.py`（新增）：`generate_and_record()`，调用共用入口并落库；失败先记录再抛出。
  - `tests/test_model_invocations.py`（新增）。
- 关键设计决定：`agent_core` 不依赖数据库，落库放在 Worker 层；Policy Gate 拒绝也写一条记录（`status=policy_rejected`，无 Prompt 版本），便于审计“为什么没调用模型”；`source` 字段区分生产与评测。
- 验证命令：`PYTHONPATH=.:labs/phase0-langgraph/src uv run pytest -q`；在数据库副本上执行 `alembic upgrade head`、`downgrade -1`、再 `upgrade head`、`alembic check`。
- 验证结果：15 passed；副本上升级、降级、再升级均成功，`alembic check` 无模型与迁移差异。
- 未完成事项：真实数据库 `data/agent_ops.db` 尚未执行迁移（仍在 `9a4d7e2c1b30`），需要用户同意后运行 `uv run alembic upgrade head`；`generate_and_record()` 尚未接入 LangGraph 工作流和前端；人工最终路线和文案与调用记录的关联尚未设计。
- 风险或分歧：数据库迁移本应由另一方复核，现在没有复核方，请用户过目字段。
- 建议下一步：把生成步骤接入 LangGraph 工作流（替换 Phase 0 的规则建议节点），并在前端审核页展示模型建议。

### 2026-10-02 15:45｜Claude｜更正产品事实，记录 DECISION-004/005

- 目的：用户确认真实产品卖点和“对方索要联系方式”的处理方式。
- 修改文件：`docs/project-memory.md`（业务目标中的产品表述、固定运营边界新增一条，均为用户当日确认）；`evals/phase2_cases.v1.json` 与 `evals/phase2_cases.v2-draft.json`（`product_facts` 改为毛衫工厂的真实卖点）；`docs/ai-collaboration-memory.md`。未修改业务代码和 Prompt。
- 验证命令与结果：`pytest` 12 passed；Mock 评测 6/6；Qwen 评测 6/6（`outputs/phase2-evaluation-qwen-20261002-154847.json`，3 个真实调用，无调用错误）。
- 未完成事项：Prompt 角色描述仍是笼统的“服装供应”，未改；`shop_type` 尚未覆盖成衣品牌、博主等真实对象；“对方索要联系方式”的代码识别未实现。
- 风险或分歧：用户提出在内容页展示微信的想法与合规基线冲突，Claude 不建议，待用户决定。

### 2026-10-02 15:20｜Claude｜整理用户提供的 18 个真实历史触达样本

- 目的：用户提供 47 张截图（18 个账号的主页、内容、私信），用于替代我编造的草稿案例。
- 修改文件：新增 `evals/real_outreach_samples.v1-draft.json`（去标识化：不含账号名、电话、微信、二维码；原图留在用户本机，不入库）。未修改业务代码。
- 样本构成：已读不回 3、回复后无后文 1、明确拒绝 1、对方主动联系 3、有回复且有意向 10。
- 关键发现（均为观察，样本不足，不构成规律）：
  - 我方真实身份是东莞毛衫（针织）加工厂，主打单件毛衫定制；现有案例和 `product_facts` 中的“西装、衬衫、内搭”与事实不符，需要用户确认后更正。
  - 截图显示平台限制：对方关注或回复之前，24 小时内最多只能发 1 条文字消息。只发“您好”的 2 例均无回复。
  - 有意向样本中多数随后交换了站外联系方式，其中 4 例由对方先提出（索要电话或微信、发来二维码、问怎么联系）；我方曾用图片、手写、谐音和符号拆分发送电话。这些做法与当前合规基线冲突，不得作为模板。
  - 同一条长模板既得到“不需要”，也得到“发点资料看看”“怎么合作”。
  - 出现了现有 `shop_type` 未覆盖的对象：成衣品牌、内容博主、店员个人号、编织爱好者（来料加工咨询）、上游纱线供应方。
- 未完成事项：样本待用户核对；未转换为评测案例。outcome 是事后结果，不能直接当作 `expected.route`。
- 风险或分歧：见“待用户决定” DECISION-004、005。
- 建议下一步：用户确认产品事实和“对方主动索要联系方式”的处理规则后，再把样本转成评测案例并修订 Prompt。

### 2026-10-02 14:30｜Claude｜首次真实 Qwen 评测（6 个正式案例）

- 目的：用户已提供 DashScope 密钥，取得 Qwen 的第一手结果。
- 修改文件：新增本机 `.env`（权限 600，已被 `.gitignore` 忽略，未进入 Git；密钥不写入任何文档）；新增 `outputs/phase2-evaluation-qwen-20261002-142614.json`（已核对不含密钥）。未修改业务代码。
- 验证命令：`PYTHONPATH=. uv run python -m services.worker.evaluate_phase2 --provider qwen`
- 验证结果：模型 `qwen3.7-plus-2026-05-26`，6/6 通过，路线 6/6 正确；3 个由 Policy Gate 拒绝，3 个真实调用，耗时约 3.2–8.4 秒，无调用错误、无无效引用。
- 人工阅读发现（自动检查未覆盖）：XHS-2026-0001 的 DM 草稿询问“春夏面料…供应”，但 `product_facts` 只有西装、衬衫、内搭，没有面料；草稿也没有提到核心产品“内搭”。现有 `must_include`/`must_not_include` 抓不到这类“产品事实越界”和“核心卖点遗漏”。
- 未完成事项：真实调用样本只有 3 个，6/6 不能当作准确率；用户将提供真实成功案例；4 个草稿案例用户认为草率，不并入正式集。
- 风险或分歧：密钥曾出现在对话中，建议用户在百炼控制台轮换；用户指出平台规则持续变化，历史成功样板的话术可能已不合规，案例需要记录联系时间和当时规则版本。
- 建议下一步：用户提供真实案例；讨论是否增加“草稿只能引用 product_facts 中的产品”的程序校验；之后设计 `model_invocations`。

### 2026-10-02 14:10｜Claude｜Phase 2B 第一步：Git 基线、勿扰前置拦截、共用生成入口

- 目的：落实用户对 DECISION-001/002/003 的决定，开始 Phase 2B。
- 修改文件：
  - `.gitignore`（忽略 `.DS_Store`、`.tmp/`、`outputs/*.ndjson`）；初始化 Git，基线提交 `c827bf3`。
  - `packages/agent_core/contracts.py`：新增 `NO_CONTACT_OUTCOMES`、`check_prior_outcome_gate()`。
  - `packages/agent_core/pipeline.py`（新增）：`generate_lead_suggestion()`，先 Policy Gate，后 `build_prompt` + `provider.generate`。
  - `packages/agent_core/evaluation.py`：改用共用入口；单个案例的 `ProviderResponseError` 记录为失败并继续；summary 增加 `policy_gate_rejects`、`provider_errors`、`missing_required_cases`、`forbidden_hit_cases`、`invalid_ref_cases`。
  - `evals/phase2_cases.v1.json`：新增 `declined`（XHS-2026-0007）、`blocked`（XHS-2026-0008），共 6 个。
  - `evals/phase2_cases.v2-draft.json`（新增）：4 个待用户确认的草稿案例（inbound 且对方提出加微信、menswear、证据不足、已回复后的 warm 跟进）。
  - `tests/test_phase2_agent_core.py`：新增拦截、放行、评测容错测试。
  - `docs/ai-collaboration-memory.md`：填写三个 DECISION，新增 CLAUDE-003，更新共同事实中可验证的测试数、Git 状态和代码入口。
- 关键设计决定：拦截规则放在 `contracts.py`（纯领域规则），编排放在 `pipeline.py`；评测和将来的 Worker 必须都走 `generate_lead_suggestion()`，避免生产路径与评测路径分叉。
- 验证命令：
  - `PYTHONPATH=.:labs/phase0-langgraph/src uv run pytest -q`
  - `PYTHONPATH=. uv run python -m services.worker.evaluate_phase2 --provider mock`
  - `PYTHONPATH=. uv run python -m services.worker.evaluate_phase2 --provider mock --cases evals/phase2_cases.v2-draft.json`
- 验证结果：12 passed；正式集 6/6 通过，其中 3 个由 Policy Gate 拒绝；草稿集 Mock 2/4 通过（inbound 案例草稿中出现“微信”，证据不足案例被 Mock 判为 dm）。草稿集未通过是预期现象，说明这两个案例能区分规则替身和真实判断，不是回归。
- 未完成事项：草稿案例等待用户确认；`DASHSCOPE_API_KEY` 未配置，Qwen 真实评测未运行；`model_invocations`、Worker 接入、前端审核未开始；本次代码修改尚未提交，可用 `git diff` 查看。
- 风险或分歧：
  - 另一个 Claude CLI 会话（13:50 起停在 `contracts.py` 的修改确认提示）在本次修改期间仍未关闭。它如果被批准，会再插入一份同名拦截函数，请直接关闭该会话，不要批准。
  - Phase 2B 顺序与 CODEX-001 不同（Qwen 评测提前到建表之前），Codex 尚未表态。
  - Mock 的 `stopped` 分支现在不会被触发（Gate 已先拦截），保留它是为了直接调用 Mock 时行为不变。
- 建议下一步：用户确认 4 个草稿案例的预期并配置密钥；随后运行 Qwen 评测，再设计 `model_invocations`。

### 2026-10-02｜Claude｜首次审查：写入完整观点

- 目的：执行"Claude 首次审查任务"，独立阅读项目文档和 Phase 2A 代码，运行测试，形成审查结论，写入讨论库。
- 修改文件：`docs/ai-collaboration-memory.md`（仅写入"Claude 观点"和"待用户决定"，未修改 Codex 观点和正式文档）。
- 验证命令：
  - `PYTHONPATH=.:labs/phase0-langgraph/src uv run pytest -q` → 6 passed
  - `PYTHONPATH=. uv run python -m services.worker.evaluate_phase2 --provider mock` → 4/4 passed
- 关键发现：见 CLAUDE-002；五项风险按优先级排列，最高两项为 `prior_outcome` 代码前置缺失和 QwenProvider 同步阻塞问题。
- 未完成事项：三个待用户决定事项（DECISION-001、002、003）需要用户确认后才能推进 Phase 2B。
- 风险：首次审查期间未调用真实 Qwen，未修改业务代码，所有观点已标注依据。
- 建议下一步：用户审阅三个 DECISION 并给出答复；Codex 回应"与 Claude 的分歧点"中的两个差异点；双方确认后开始 Phase 2B 第一步（model_invocations 表设计）。

### 2026-10-02｜Codex｜记录差异化协作偏好

- 目的：记录用户希望两位 Agent 各有侧重点，并由双方继续给出协作建议。
- 修改文件：`docs/ai-collaboration-memory.md`。
- 候选分工：Claude 偏代码设计和实现质量；Codex 偏业务架构、Agent 编排、学习拆解与整合。
- 边界：当前只是协作假设，不限制双方交叉审查，也不在 Claude 表达观点前固化角色。
- 建议下一步：Claude 首次审查时同时评价分工方案，用户随后确认。

### 2026-10-02｜Codex｜补全三方讨论背景和 Claude 审查任务

- 目的：把此前用户与 Codex 已确认的业务、架构、本地部署和 Agent 学习目标完整交给 Claude。
- 修改文件：`docs/ai-collaboration-memory.md`。
- 关键决定：共同文件定位为用户、Codex、Claude 三方讨论库；正式需求仍以 `project-memory.md` 为准；未确认的评分和风险字段保留为候选观点。
- 验证：逐项对照项目记忆、最终架构、学习路径和 Phase 2 代码入口；未修改运行时代码。
- 风险：部分平台运营数字是内部偏好而非安全阈值，已明确标注；Claude 不应将其解释成平台许可。
- 建议下一步：Claude 先执行“首次审查任务”，写入自己的观点，再由用户比较双方建议后决定 Phase 2B。

### 2026-10-02｜Codex｜建立双 Agent 共同记忆

- 目的：让 Codex 与 Claude CLI 共享项目事实、分别陈述观点，并留下可验证的代码交接。
- 新增：`docs/ai-collaboration-memory.md`、`AGENTS.md`、`CLAUDE.md`；Claude 入口使用 `@path` 自动导入共同记忆。
- 代码行为：未修改运行时代码。
- 验证：检查共同记忆、入口文件和正式文档的引用关系；不需要运行应用测试。
- 下一位 Agent：先阅读本文件，再核对 Phase 2A 代码；Claude 应只在“Claude 观点”追加自己的判断，并在完成任何代码修改后追加交接。同一工作目录不要与 Codex 同时写入。

## 9. 交接记录模板

```text
### YYYY-MM-DD HH:MM｜Agent 名称｜简短标题

- 目的：
- 修改文件：
- 关键设计决定：
- 验证命令：
- 验证结果：
- 未完成事项：
- 风险或分歧：
- 建议下一步：
```
