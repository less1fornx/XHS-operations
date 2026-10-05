"""Phase 2C screening: what the model reports from screenshots, and the rules that rank shops.

Division of labour (user decision 2026-10-03, rules v0):
- The vision model only reports what is visible, quoting the screenshot text.
- This module turns those quotes into signals, a tier, an order and a suggested
  route. Every rule here is deterministic, versioned and explainable, so it can
  be changed deliberately instead of hoping the model "learns".
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import ShopType, StrictModel


SCREENING_RULES_VERSION = "screening-rules-v0"

# User decision: at most 10 shops per batch, at most 3 screenshots per shop.
MAX_SHOPS_PER_BATCH = 10
MAX_IMAGES_PER_SHOP = 3

Tier = Literal["high", "medium", "low", "excluded"]
SignalValue = Literal["yes", "no", "unknown"]
SuggestedRoute = Literal["dm", "comment", "none"]


def trim_model_output(model: type[StrictModel], data: object) -> object:
    """Cut over-long lists and text from a vision reading down to the contract's limits.

    A chain with 12 branches is a correct reading, not a format error: keep what
    fits rather than throwing the whole result away (seen 2026-10-05).
    """
    if not isinstance(data, dict):
        return data
    trimmed = dict(data)
    for name, field in model.model_fields.items():
        limit = next((m.max_length for m in field.metadata if getattr(m, "max_length", None)), None)
        value = trimmed.get(name)
        if limit and isinstance(value, (list, str)) and len(value) > limit:
            trimmed[name] = value[:limit]
    return trimmed


class ShopExtraction(StrictModel):
    """What the vision model saw. Lists hold verbatim screenshot text, not opinions."""

    # What the screenshots show. "other" means they are not a shop's profile or notes.
    page_kind: Literal["profile", "note_list", "note_detail", "other"] = "profile"
    page_summary: str = Field(default="", max_length=300)
    shop_name: str = Field(max_length=200)
    # 小红书号 as printed on the profile. It is the unique key for a shop: names
    # repeat and change, the id does not (user decision 2026-10-03).
    xhs_id: str | None = Field(default=None, max_length=100)
    account_kind: Literal["enterprise_verified", "personal", "unknown"]
    shop_type: ShopType
    bio_text: str = Field(max_length=2000)
    followers_text: str | None = Field(default=None, max_length=50)
    location_text: str | None = Field(default=None, max_length=100)
    price_mentions: list[str] = Field(max_length=20)
    fabric_mentions: list[str] = Field(max_length=20)
    high_end_mentions: list[str] = Field(max_length=20)
    store_mentions: list[str] = Field(max_length=10)
    # Dates on note cards WITHOUT the 置顶 badge. Pinned notes can be years old and
    # sit above newer ones, so they say nothing about activity (user rule 2026-10-03).
    recent_post_dates: list[str] = Field(max_length=20)
    pinned_post_dates: list[str] = Field(default_factory=list, max_length=10)
    content_topics: list[str] = Field(max_length=20)
    knitwear_present: SignalValue
    unclear_fields: list[str] = Field(max_length=20)

    @field_validator("shop_type", mode="before")
    @classmethod
    def unknown_type_is_other(cls, value: object) -> object:
        return "other" if value == "unknown" else value

    @model_validator(mode="before")
    @classmethod
    def trim_to_limits(cls, data: object) -> object:
        return trim_model_output(cls, data)


class NotShopPageError(Exception):
    """The model read the images fine, but they are not a shop profile or its notes."""


class Signal(StrictModel):
    value: SignalValue
    evidence: list[str]


class ShopScreening(StrictModel):
    """Program-made judgement for one shop, with the quotes it relied on."""

    tier: Tier
    tier_reasons: list[str]
    prerequisite_met: bool
    signals: dict[str, Signal]
    latest_post_age_days: int | None
    suggested_route: SuggestedRoute = "none"
    content_angles: list[str]
    rules_version: str = SCREENING_RULES_VERSION


# --- Signal A: activity -------------------------------------------------------

_RELATIVE = re.compile(r"(\d+)\s*(分钟|小时|天|周|个月)前")
_FULL_DATE = re.compile(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})")
_MONTH_DAY = re.compile(r"^(\d{1,2})[-./月](\d{1,2})")


def post_age_days(text: str, today: date) -> int | None:
    """Age in days of a date shown on a note card, or None if unreadable."""
    value = text.strip()
    if not value:
        return None
    if "刚刚" in value or "今天" in value:
        return 0
    if "昨天" in value:
        return 1
    if "前天" in value:
        return 2
    match = _RELATIVE.search(value)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return {"分钟": 0, "小时": 0, "天": amount, "周": amount * 7, "个月": amount * 30}[unit]
    match = _FULL_DATE.search(value)
    if match:
        try:
            return (today - date(*map(int, match.groups()))).days
        except ValueError:
            return None
    match = _MONTH_DAY.search(value)
    if match:
        try:
            shown = date(today.year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
        if shown > today:  # "12-20" seen in January belongs to last year
            shown = shown.replace(year=today.year - 1)
        return (today - shown).days
    return None


# --- Signal C: price ----------------------------------------------------------

_PRICE = re.compile(r"[¥￥]\s*(\d{3,6})|(\d{3,6})\s*元")
HIGH_PRICE_THRESHOLD = 1600  # user rule: sale price at or above 1600
LOW_PRICE_CEILING = 1000


def visible_prices(mentions: list[str]) -> list[int]:
    prices: list[int] = []
    for mention in mentions:
        for match in _PRICE.finditer(mention):
            prices.append(int(match.group(1) or match.group(2)))
    return prices


# --- Tiering ------------------------------------------------------------------

NOT_PROACTIVE_TYPES = ("supplier", "hobbyist", "other")
SECONDARY_TYPES = ("content_creator", "staff_account")


def screen_shop(extraction: ShopExtraction, today: date) -> ShopScreening:
    reasons: list[str] = []

    if extraction.shop_type in NOT_PROACTIVE_TYPES:
        return ShopScreening(
            tier="excluded",
            tier_reasons=[f"店铺类型为 {extraction.shop_type}，不作为主动私信对象"],
            prerequisite_met=False,
            signals={},
            latest_post_age_days=None,
            content_angles=[],
        )

    ages = [age for age in (post_age_days(t, today) for t in extraction.recent_post_dates) if age is not None]
    latest = min(ages) if ages else None
    prices = visible_prices(extraction.price_mentions)

    signals = {
        "A_active": Signal(
            value="unknown" if latest is None else ("yes" if latest <= 30 else "no"),
            evidence=extraction.recent_post_dates[:5],
        ),
        "B_fabric": Signal(
            value="yes" if extraction.fabric_mentions else "no",
            evidence=extraction.fabric_mentions[:5],
        ),
        "C_high_price": Signal(
            value=(
                "yes"
                if any(p >= HIGH_PRICE_THRESHOLD for p in prices) or extraction.high_end_mentions
                else ("no" if prices else "unknown")
            ),
            evidence=(extraction.price_mentions + extraction.high_end_mentions)[:5],
        ),
        "D_store": Signal(
            value="yes" if extraction.store_mentions else "unknown",
            evidence=extraction.store_mentions[:5],
        ),
    }
    hits = [name for name, signal in signals.items() if signal.value == "yes"]
    prerequisite = extraction.shop_type == "suit_custom"

    downgrades: list[str] = []
    if latest is not None and latest > 90:
        downgrades.append(f"最近一次更新在 {latest} 天前，超过 3 个月")
    if prices and max(prices) < LOW_PRICE_CEILING and not extraction.high_end_mentions:
        downgrades.append(f"可见价格最高 {max(prices)} 元，明显低于 1000 元")
    if extraction.shop_type == "menswear" and extraction.knitwear_present == "yes":
        downgrades.append("成衣品牌且已有自己的针织产品")

    if downgrades:
        tier: Tier = "low"
        reasons.extend(downgrades)
    elif (
        prerequisite
        and len(hits) >= 2
        and ("B_fabric" in hits or "C_high_price" in hits)
    ):
        tier = "high"
        reasons.append(f"主营西装定制，满足 {len(hits)} 项信号，且包含面料或客单价")
    else:
        tier = "medium"
        if not prerequisite:
            reasons.append("不是西装定制店，最高只能评为中")
        else:
            reasons.append(f"满足 {len(hits)} 项信号，未达到高匹配条件")

    return ShopScreening(
        tier=tier,
        tier_reasons=reasons,
        prerequisite_met=prerequisite,
        signals=signals,
        latest_post_age_days=latest,
        content_angles=content_angles(extraction),
    )


def content_angles(extraction: ShopExtraction) -> list[str]:
    """Suggested opening angles. Each rests on one or two historical samples: hypotheses."""
    angles: list[str] = []
    if extraction.fabric_mentions:
        angles.append("对方讲究面料：先聊纱线和材质（如亚麻、羊绒），少谈价格")
    if extraction.knitwear_present == "no":
        angles.append("店里还没有内搭：讲清内搭是西装的附加单品，客人订西装时顺带搭配，只需留一件样衣、没有存货压力")
    if extraction.knitwear_present == "yes":
        angles.append("已在搭配内搭或针织：强调款式不限、一件起订、可按客人尺寸定制")
    if extraction.account_kind == "enterprise_verified":
        angles.append("企业号：写短一些，方便客服转交负责人")
    if extraction.shop_type == "content_creator":
        angles.append("内容博主：先弄清对方是想进货还是谈推广")
    return angles


TIER_ORDER = {"high": 0, "medium": 1, "low": 2, "excluded": 3}


def rank_key(screening: ShopScreening, extraction: ShopExtraction) -> tuple:
    hits = sum(1 for signal in screening.signals.values() if signal.value == "yes")
    age = screening.latest_post_age_days if screening.latest_post_age_days is not None else 10_000
    return (TIER_ORDER[screening.tier], -hits, age, -len(extraction.fabric_mentions))


def assign_routes(ordered: list[ShopScreening], dm_slots: int, comment_slots: int) -> None:
    """Fill today's remaining quota in rank order: high tier to DM first, then comments."""
    for screening in ordered:
        if screening.tier == "high" and dm_slots > 0:
            screening.suggested_route, dm_slots = "dm", dm_slots - 1
        elif screening.tier in ("high", "medium") and comment_slots > 0:
            screening.suggested_route, comment_slots = "comment", comment_slots - 1
        else:
            screening.suggested_route = "none"


def normalize_xhs_id(value: str | None) -> str:
    """Strip the label and spaces. Case is kept for display and search; callers
    compare case-insensitively."""
    text = (value or "").strip()
    for label in ("小红书号：", "小红书号:", "小红书号"):
        if text.startswith(label):
            text = text[len(label):]
    return text.strip()


def to_lead_context(extraction: ShopExtraction, screening: ShopScreening) -> dict[str, str]:
    """Map a screened shop onto the fields the drafting workflow already accepts."""
    profile_lines = [extraction.bio_text.strip()]
    if extraction.store_mentions:
        profile_lines.append("门店：" + "；".join(extraction.store_mentions))
    if extraction.high_end_mentions:
        profile_lines.append("定位用语：" + "；".join(extraction.high_end_mentions))
    if extraction.account_kind == "enterprise_verified":
        profile_lines.append("企业认证账号")
    signal_lines = []
    if extraction.fabric_mentions:
        signal_lines.append("内容涉及面料：" + "；".join(extraction.fabric_mentions[:5]))
    if extraction.knitwear_present == "yes":
        signal_lines.append("内容中已出现针织或内搭")
    elif extraction.knitwear_present == "no":
        signal_lines.append("内容中未见针织或内搭")
    activity = (
        f"最近一次更新约 {screening.latest_post_age_days} 天前"
        if screening.latest_post_age_days is not None
        else "活跃情况未知"
    )
    if extraction.followers_text:
        activity += f"；粉丝 {extraction.followers_text}"
    return {
        "shop_name": extraction.shop_name,
        "xhs_id": normalize_xhs_id(extraction.xhs_id),
        "shop_type": extraction.shop_type,
        "shop_note": "\n".join(line for line in profile_lines if line),
        "activity_note": activity,
        "purchase_signal_note": "\n".join(signal_lines),
        "video_text": "近期笔记标题：" + "；".join(extraction.content_topics[:8])
        if extraction.content_topics
        else extraction.bio_text[:500] or extraction.shop_name,
    }


def today_in_shanghai() -> date:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


__all__ = [
    "normalize_xhs_id",
    "NotShopPageError",
    "MAX_IMAGES_PER_SHOP",
    "MAX_SHOPS_PER_BATCH",
    "SCREENING_RULES_VERSION",
    "ShopExtraction",
    "ShopScreening",
    "assign_routes",
    "post_age_days",
    "rank_key",
    "screen_shop",
    "to_lead_context",
    "today_in_shanghai",
    "visible_prices",
]

