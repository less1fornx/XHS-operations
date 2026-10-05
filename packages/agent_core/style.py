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
_GENERIC_PRAISE = ("令人印象深刻", "非常专业", "很专业", "很有质感", "很显质感", "质感出众", "设计感很强", "很见功底", "独到之处", "很细致")
# Our inference stated as their fact; fits any shop.
_PRESUMPTION = re.compile(r"对[^。，,]{0,14}(内搭|搭配|打底)[^。，,]{0,10}要求[很极较]?高|考验[^。，,]{0,8}(内搭|搭配)")
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
    if _PRESUMPTION.search(text):
        issues.append("替对方下了结论（“对内搭要求很高”这类话每家店都能套）")
    if "想请教" in text:
        issues.append("结尾用了固定起句“想请教下”")
    limit = LENGTH_LIMIT.get(audience, LENGTH_LIMIT["unknown"])
    if len(text) > limit:
        issues.append(f"长度 {len(text)} 字，超过 {limit} 字")
    if not text.rstrip().endswith(("？", "?")):
        issues.append("结尾不是一个问题")
    return issues



def sameness(drafts: list[str], n: int = 5) -> float:
    """How alike a batch of drafts is: the average share of each draft's n-character
    runs that also appear in another draft of the batch. 0 = nothing shared, 1 = identical."""
    grams = [{text[i:i + n] for i in range(max(len(text) - n + 1, 0))} for text in drafts]
    shares = []
    for index, own in enumerate(grams):
        others = set().union(*(g for j, g in enumerate(grams) if j != index)) if len(grams) > 1 else set()
        if own:
            shares.append(len(own & others) / len(own))
    return round(sum(shares) / len(shares), 3) if shares else 0.0
