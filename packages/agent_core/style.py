"""Checks on a first-message draft that a program can do without a model.

User decisions 2026-10-08 (drafting rules v9, kept in v10): the message has two parts, the
shop's content and then us (who we are, at least one strength, that we want to
work together). It addresses the reader as "您" or "贵店", ends on a statement
and asks no question. The prompt asks for this; these checks tell the reviewer
when the model did not comply.
"""

from __future__ import annotations

import json
import random
import re
from functools import lru_cache

from .prompting import PROJECT_ROOT, SYSTEM_PROMPT_PATH

# Closing lines that hand the decision to the reader.
_NEED_QUESTION = re.compile(r"(是否|有没有|有无|需不需要|要不要|是否有|是否考虑)[^。？?！!\n]{0,14}(需求|兴趣|意向|考虑|了解|计划|合作)")
QUESTION = re.compile(r"[？?]|吗(?=[。！!，,\s]|$)")
# What counts as a strength; "we are a factory" and "一件起订" do not (acceptance item 6).
_STRENGTHS = ("200", "高端面料", "纱线", "做工")
# Words that say we are here to work together.
_INTENT = ("合作", "供应", "供货", "发几款")
_GENERIC_PRAISE = ("令人印象深刻", "非常专业", "很专业", "很有质感", "很显质感", "质感出众", "设计感很强", "很见功底", "独到之处", "很细致")
# Our inference stated as their fact; fits any shop.
_PRESUMPTION = re.compile(r"对[^。，,]{0,14}(内搭|搭配|打底)[^。，,]{0,10}要求[很极较]?高|考验[^。，,]{0,8}(内搭|搭配)")
# Passing judgement on the shop, good or bad (user 2026-10-09: say what we saw, do not rate it).
_JUDGEMENT = re.compile(
    r"要求[极很较更颇]?[其为]?高|拿捏|精准|切中|钻研"
    r"|(?:很|非常|十分|相当|确实|真的|特别|极其?|颇为?)(?:实在|准|深入?|清晰|到位|讲究|用心|考究|细致|专业|出色|高级|扎实|有品位|有质感|见功底|有眼光)"
)
_QUOTED = re.compile(r"“[^”]*”|\"[^\"]*\"|「[^」]*」")
# The prompt asks for 120; models count loosely, so only flag a clear overrun.
LENGTH_LIMIT = 135
POSITIONINGS_PATH = PROJECT_ROOT / "config" / "product_facts.v7.json"


@lru_cache(maxsize=1)
def load_positionings() -> dict[str, dict]:
    """The two ways we introduce ourselves, with the strengths each may claim and the
    words that reveal them in a draft."""
    return json.loads(POSITIONINGS_PATH.read_text(encoding="utf-8"))["positionings"]


def positioning_for(shop_type: str | None) -> str:
    """A forced rule, not a model judgement (user 2026-10-09): suit shops hear about suit
    inner layers; every other kind of account hears that we are a flat-knitting factory."""
    for key, entry in load_positionings().items():
        if shop_type in entry["shop_types"]:
            return key
    return "flat_knit"


def self_description(positioning: str) -> str:
    return load_positionings()[positioning]["self"]


def pick_strengths(seed: str, positioning: str = "suit_inner") -> list[str]:
    """The strengths this shop's message names. Where there is a choice the program draws one
    or two, because the model left to itself picks the same ones every time; the draw is
    seeded by the run, so rewrites keep the same set."""
    entry = load_positionings()[positioning]
    texts = [item["text"] for item in entry["strengths"]]
    if entry["pick"] == "all":
        return texts
    rng = random.Random(seed)
    return rng.sample(texts, rng.choice((1, 2)))


def _all_strengths() -> list[tuple[str, list[str]]]:
    return [(item["text"], item["keywords"]) for entry in load_positionings().values() for item in entry["strengths"]]


# A run this long shared with a worked example in the prompt is a copied sentence, not a coincidence.
COPIED_RUN = 8


def _before_us(text: str) -> str:
    """The part about the shop plus the bridge; what follows "我们" is fixed wording by design."""
    return text[:text.index("我们")] if "我们" in text else text


@lru_cache(maxsize=1)
def _example_openings() -> tuple[str, ...]:
    lines = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").splitlines()
    return tuple(_before_us(line[1:].strip()) for line in lines if line.startswith(">"))


def copied_from_examples(draft: str) -> str:
    """The longest run the draft's opening shares with a worked example in the prompt, or ""."""
    opening = _before_us(draft.strip())
    longest = ""
    for start in range(len(opening)):
        end = start + max(len(longest) + 1, COPIED_RUN)
        while end <= len(opening) and any(opening[start:end] in example for example in _example_openings()):
            longest = opening[start:end]
            end += 1
    return longest


def first_message_style_issues(
    draft: str, audience: str = "unknown", strengths: list[str] | None = None, positioning: str | None = None
) -> list[str]:
    """Return human-readable problems; an empty list means the draft follows the structure.
    `strengths` and `positioning` are what the program set for this shop; without them any
    one strength will do and the self-description is not checked."""
    issues: list[str] = []
    text = draft.strip()
    # The part about us starts where we first name ourselves; the shop's own words come before it.
    ours = text[text.index("我们"):] if "我们" in text else ""
    last = [part for part in re.split(r"[。！!？?\n]", text) if part.strip()][-1] if text else ""
    if "你" in text:
        issues.append("称呼用了“你”，应一律用“您”或“贵店”")
    if _NEED_QUESTION.search(text[-40:]):
        issues.append("结尾在问对方有没有需求，把主动权交给了对方")
    elif QUESTION.search(text):
        issues.append("出现了问句，全文应是陈述句")
    if positioning:
        entry = load_positionings()[positioning]
        if not all(word in ours for word in entry["self_keywords"]):
            issues.append(f"自称没有按指定的写：应原样写“我们是{entry['self']}”")
        wrong = [word for word in entry["forbidden"] if word in ours]
        if wrong:
            issues.append(f"对方不是西装定制店，这条按“{entry['label']}”的定位写，不要出现：" + "、".join(wrong))
    if strengths:
        written = {text for text, words in _all_strengths() if any(word in ours for word in words)}
        # A strength worked into the bridge sentence, before "我们", still counts as said.
        anywhere = {name for name, words in _all_strengths() if any(word in text for word in words)}
        missing = [name for name in strengths if name not in anywhere]
        extra = sorted(written - set(strengths))
        if missing:
            issues.append("没有写这一条指定的实力：" + "；".join(missing))
        if extra:
            issues.append("写了没有指定的实力，删掉：" + "；".join(extra))
    elif not any(word in ours for word in _STRENGTHS):
        issues.append("没有写我方实力（约200家西装定制店、高端面料、纱线和做工，至少一项）")
    if not any(word in last for word in _INTENT):
        issues.append("结尾没有说明我们想合作")
    generic = [word for word in _GENERIC_PRAISE if word in text]
    if generic:
        issues.append("用了放在谁身上都成立的夸奖：" + "、".join(generic))
    # Their own words in quotation marks are evidence, not our verdict.
    judged = _JUDGEMENT.search(_QUOTED.sub("", _before_us(text)))
    if judged:
        issues.append(f"对对方下了评价（“{judged.group(0)}”）：只说看到了什么，不评好坏，也不说对方要求高")
    if _PRESUMPTION.search(text):
        issues.append("替对方下了结论（“对内搭要求很高”这类话每家店都能套）")
    wedding = [word for word in ("婚礼", "新郎", "礼服", "备婚", "伴郎", "婚宴", "结婚") if word in text]
    if wedding:
        issues.append("从婚礼场景切入（婚礼配衬衫，我们不做）：" + "、".join(wedding))
    stars = [word for word in ("明星", "艺人") if word in text]
    if stars:
        issues.append("提到了明星或艺人：" + "、".join(stars))
    copied = copied_from_examples(text)
    if copied:
        issues.append(f"照抄了规则示范里的句子：“{copied}”，换成按这家店的内容自己写的话")
    if len(text) > LENGTH_LIMIT:
        issues.append(f"长度 {len(text)} 字，超过 120 字")
    return issues



def shared_share(draft: str, others: list[str], n: int = 5) -> float:
    """How much of one draft also appears in the others: the share of its n-character runs
    found in any of them. 0 = nothing shared, 1 = every run appears elsewhere."""
    own = {draft[i:i + n] for i in range(max(len(draft) - n + 1, 0))}
    seen = {text[i:i + n] for text in others for i in range(max(len(text) - n + 1, 0))}
    return round(len(own & seen) / len(own), 3) if own else 0.0


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
