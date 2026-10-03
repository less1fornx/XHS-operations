"""What happened after a message was sent: facts, what the shop asked for, and the stage.

Designed with the user on 2026-10-03. Three things are kept apart on purpose:
- facts (replied or not, when, the words) never need re-judging;
- ask tags record what the shop literally asked for, multi-select;
- stage is the human's single judgement of where this lead stands.
"Wants to learn about the product" is derived from the tags, not stored.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

OUTCOME_RULES_VERSION = "outcome-rules-v1"

# User rule: a reply within 3 days is a normal reply; up to 15 days is a late
# (secondary) reply; after 15 days without a reply the task ends as no-reply.
NORMAL_REPLY_DAYS = 3
REPLY_WINDOW_DAYS = 15

ReplyTiming = Literal["normal", "late", "after_window"]

ASK_TAG_GROUPS: dict[str, dict[str, str]] = {
    "了解产品": {
        "catalog": "要资料或画册",
        "styles": "要款式图",
        "color_card": "要色卡",
        "price": "要价格",
        "fabric": "问面料或纱线",
    },
    "了解合作方式": {
        "custom_size": "问能否按尺寸定制",
        "moq": "问起订量",
        "stock_dropship": "问现货或代发",
        "how_to_cooperate": "问怎么合作",
    },
    "了解工厂": {"visit": "问能否实地考察或有无展厅"},
    "推进联系": {"contact_request": "索要联系方式", "handoff": "说转交同事"},
    "拒绝": {"not_needed": "不需要", "has_supplier": "已有供应商"},
    "其他": {"other": "其他"},
}
ASK_TAGS: dict[str, str] = {key: label for group in ASK_TAG_GROUPS.values() for key, label in group.items()}
PRODUCT_INTEREST_TAGS = frozenset(ASK_TAG_GROUPS["了解产品"])
DECLINE_TAGS = frozenset(ASK_TAG_GROUPS["拒绝"])

STAGES: dict[str, str] = {
    "no_reply": "未回复",
    "replied_no_intent": "已回复但无意向",
    "wants_info": "想了解产品",
    "sample": "要买样衣（成本价一件）",
    "quotation": "谈报价",
    "won": "成交",
    "lost": "未成交",
}


def reply_timing(sent_at: datetime, replied_at: datetime) -> ReplyTiming:
    days = (replied_at - sent_at).total_seconds() / 86400
    if days <= NORMAL_REPLY_DAYS:
        return "normal"
    return "late" if days <= REPLY_WINDOW_DAYS else "after_window"


def wants_product_info(tags: list[str]) -> bool:
    return any(tag in PRODUCT_INTEREST_TAGS for tag in tags)


def keeps_do_not_contact(replied: bool, stage: str, tags: list[str]) -> bool:
    """A shop that never replied, declined, or showed no interest is not contacted again."""
    if not replied:
        return True
    return stage in ("replied_no_intent", "lost") or any(tag in DECLINE_TAGS for tag in tags)


_CONTACT = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d[-\s#&*]?\d{4}[-\s#&*]?\d{4}(?!\d)|\d{7,}")


def scrub_contact_info(text: str) -> str:
    """Keep the words, drop phone-like numbers. Contact details never enter the database."""
    return _CONTACT.sub("[联系方式已隐去]", text)
