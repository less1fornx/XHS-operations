# Claude CLI 项目协作说明

@docs/ai-collaboration-memory.md

开始实质性项目工作前，检查共同记忆中的最近交接。

如果共同记忆中的“Claude 观点”尚未填写，先执行其中的“Claude 首次审查任务”。首次审查阶段不要修改业务代码；先把独立判断写入讨论库并向用户汇报。

项目进度的唯一来源是 `docs/STATUS.md`。每次工作结束，用 `PYTHONPATH=. uv run python -m services.worker.status_report` 取最新数字并更新该页；只使用其中的阶段编号，不再新增其他编号体系。

按任务范围加载正式基线：

- 业务需求使用 `docs/project-memory.md`。
- 服务边界、数据模型或阶段调整使用 `docs/architecture-final.md`。
- 平台行为使用 `docs/xhs-compliance-rules.md`。

`docs/ai-collaboration-memory.md` 是 Claude CLI 与 Codex 的共同协作记录。Claude 只能维护其中的“Claude 观点”，不得代写或覆盖“Codex 观点”。完成代码修改后，必须在“最近交接记录”顶部追加修改、验证和遗留事项。

共同记忆中的建议不是正式需求。只有用户确认后，才能同步到正式项目记忆或架构基线。
