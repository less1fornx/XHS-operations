from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .contracts import LeadAgentInput


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_VERSION = "lead-outreach-v1"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "lead_outreach" / "v1" / "system.md"


@dataclass(frozen=True)
class PromptBundle:
    version: str
    system: str
    user: str


def build_prompt(agent_input: LeadAgentInput) -> PromptBundle:
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    user_payload = agent_input.model_dump(mode="json")
    user = (
        "以下 JSON 是本次任务唯一允许使用的业务上下文。"
        "不得补充其中不存在的店铺事实、采购需求、价格、库存或合作关系。\n\n"
        + json.dumps(user_payload, ensure_ascii=False, indent=2)
    )
    return PromptBundle(version=PROMPT_VERSION, system=system, user=user)
