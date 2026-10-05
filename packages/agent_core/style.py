"""Checks on a first-message draft that a program can do without a model.

User decisions 2026-10-05: the first DM earns a reply instead of presenting our
strengths, and it must not end by asking whether the shop "has a need". The
prompt asks for this; these checks tell the reviewer when the model did not comply.
"""

from __future__ import annotations

import re

# Closing lines that hand the decision to the reader.
_NEED_QUESTION = re.compile(r"(是否|有没有|有无|需不需要|要不要|是否有|是否考虑)[^。？?！!\n]{0,14}(需求|兴趣|意向|考虑|了解|计划|合作)")
# Our own credentials; more than one in a first message reads as a pitch.
_SELF_CLAIMS = ("200", "品质有保障", "存货压力", "一件起订", "有订单再下单")
_GENERIC_PRAISE = ("令人印象深刻", "非常专业", "很专业", "很有质感", "很显质感", "质感出众", "设计感很强")
# The prompt asks for 110 and 150; models count loosely, so only flag a clear overrun.
LENGTH_LIMIT = {"store_account": 130, "personal_shop": 170, "unknown": 130}


def first_message_style_issues(draft: str, audience: str = "unknown") -> list[str]:
    """Return human-readable problems; an empty list means the draft follows the structure."""
    issues: list[str] = []
    text = draft.strip()
    last = re.split(r"[。！!\n]", text.rstrip("？?"))[-1] if text else ""
    if _NEED_QUESTION.search(last) or _NEED_QUESTION.search(text[-40:]):
        issues.append("结尾在问对方有没有需求，把主动权交给了对方")
    claims = [word for word in _SELF_CLAIMS if word in text]
    if len(claims) > 1:
        issues.append("罗列了多条我方卖点：" + "、".join(claims))
    generic = [word for word in _GENERIC_PRAISE if word in text]
    if generic:
        issues.append("用了放在谁身上都成立的夸奖：" + "、".join(generic))
    limit = LENGTH_LIMIT.get(audience, LENGTH_LIMIT["unknown"])
    if len(text) > limit:
        issues.append(f"长度 {len(text)} 字，超过 {limit} 字")
    if not text.rstrip().endswith(("？", "?")):
        issues.append("结尾不是一个问题")
    return issues
