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


SCREENING_RULES_VERSION = "screening-rules-v1"

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


# What a field becomes when the model leaves it out or returns null (user decision
# 2026-10-05: record it as empty and flag it, do not fail the whole shop).
_BLANK_VALUES: dict[str, object] = {
    "shop_name": "", "bio_text": "", "account_kind": "unknown", "shop_type": "other",
    "knitwear_present": "unknown", "price_mentions": [], "fabric_mentions": [],
    "high_end_mentions": [], "store_mentions": [], "recent_post_dates": [],
    "pinned_post_dates": [], "content_topics": [], "unclear_fields": [],
    "wedding_mentions": [], "daily_mentions": [],
}
_FIELD_NAMES_ZH = {
    "shop_name": "店铺名称", "bio_text": "简介", "account_kind": "账号类型", "shop_type": "店铺类型",
    "knitwear_present": "是否已有内搭", "price_mentions": "价格", "fabric_mentions": "面料",
    "high_end_mentions": "高端用语", "store_mentions": "门店", "recent_post_dates": "笔记日期",
    "content_topics": "笔记标题",
}


def fill_missing_fields(data: object) -> object:
    """Replace absent or null fields with an empty value and list them in unclear_fields."""
    if not isinstance(data, dict):
        return data
    filled = dict(data)
    missing = [name for name, blank in _BLANK_VALUES.items() if filled.get(name) is None]
    for name in missing:
        blank = _BLANK_VALUES[name]
        filled[name] = list(blank) if isinstance(blank, list) else blank
    flagged = [_FIELD_NAMES_ZH[name] + "（模型没有返回）" for name in missing if name in _FIELD_NAMES_ZH]
    if flagged:
        filled["unclear_fields"] = [*filled["unclear_fields"], *flagged]
    return filled


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
    # Verbatim wording about occasions. Wedding suits are worn with shirts, which we
    # do not make, so a wedding-only shop is a poor fit (user 2026-10-06).
    wedding_mentions: list[str] = Field(default_factory=list, max_length=10)
    daily_mentions: list[str] = Field(default_factory=list, max_length=10)
    unclear_fields: list[str] = Field(max_length=20)

    @field_validator("shop_type", mode="before")
    @classmethod
    def unknown_type_is_other(cls, value: object) -> object:
        return "other" if value == "unknown" else value

    @model_validator(mode="before")
    @classmethod
    def trim_to_limits(cls, data: object) -> object:
        return trim_model_output(cls, fill_missing_fields(data))


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
    # Facts kept for later comparison with outcomes. They change nothing today
    # (user decision 2026-10-06): a fact moves into ranking only once data supports it.
    recorded: dict[str, str] = Field(default_factory=dict)
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


# --- Price --------------------------------------------------------------------

# Our inner layers sell at roughly 500 (basic tee) to 600-800 (long sleeve); a shop
# whose dearest suit is under this cannot plausibly add one (user 2026-10-06).
SUIT_PRICE_GATE = 1600
STALE_AFTER_DAYS = 90
ACTIVE_WITHIN_DAYS = 30

_AMOUNT = re.compile(r"(?:[¥￥]\s*)?(\d+(?:\.\d+)?)\s*([kKwW万千]?)\s*(\+|元?\s*起|元)?")
_NOT_A_PRICE = ("券", "优惠", "立减", "满减", "红包", "折", "定金", "订金", "粉丝")
_UNIT = {"k": 1000, "K": 1000, "千": 1000, "w": 10000, "W": 10000, "万": 10000, "": 1}


def parse_prices(mentions: list[str]) -> list[tuple[int, bool]]:
    """Prices read from screenshot text as (amount, is_starting_price).

    "2000+起" and "1k+" are floors: the shop charges at least that, the top is unknown.
    Coupon and deposit amounts, weights and counts are not prices.
    """
    found: list[tuple[int, bool]] = []
    for mention in mentions:
        if any(word in mention for word in _NOT_A_PRICE):
            continue
        for match in _AMOUNT.finditer(mention):
            number, unit, tail = match.group(1), match.group(2), match.group(3) or ""
            marked = match.group(0).lstrip().startswith(("¥", "￥")) or unit or tail
            amount = int(float(number) * _UNIT[unit])
            if marked and 100 <= amount <= 500_000:
                found.append((amount, "+" in tail or "起" in tail))
    return found


def visible_prices(mentions: list[str]) -> list[int]:
    return [amount for amount, _ in parse_prices(mentions)]


# --- Three layers: gate, ranking, record-only (rules v1) ------------------------

NOT_PROACTIVE_TYPES = ("supplier", "hobbyist", "other")
SECONDARY_TYPES = ("content_creator", "staff_account")
TYPE_NAMES_ZH = {
    "suit_custom": "西装定制店", "menswear": "成衣品牌或男装店", "apparel_studio": "服装工作室",
    "content_creator": "内容博主", "staff_account": "店员个人号", "hobbyist": "个人爱好者",
    "supplier": "上游供应商", "other": "与服装无关",
}


def _excluded(reason: str, recorded: dict[str, str] | None = None) -> ShopScreening:
    return ShopScreening(
        tier="excluded", tier_reasons=[reason], prerequisite_met=False, signals={},
        recorded=recorded or {}, latest_post_age_days=None, content_angles=[],
    )


SCENE_NAMES_ZH = {"wedding": "主要做婚礼西装", "daily": "日常为主", "both": "婚礼和日常都做", "unknown": "看不出来"}


def main_scene(extraction: ShopExtraction) -> str:
    """Decided by the program from the copied wording, not guessed by the model.
    Wedding-only needs clear evidence: at least two wedding mentions and no daily one."""
    wedding, daily = len(extraction.wedding_mentions), len(extraction.daily_mentions)
    if wedding and daily:
        return "both"
    if daily:
        return "daily"
    if wedding >= 2:
        return "wedding"
    return "unknown"


def recorded_facts(extraction: ShopExtraction) -> dict[str, str]:
    stores = len(extraction.store_mentions)
    return {
        "置顶笔记": "有" if extraction.pinned_post_dates else "截图里没看到",
        "账号": {"enterprise_verified": "企业认证号", "personal": "个人号"}.get(extraction.account_kind, "未知"),
        "门店信息": f"{stores} 条" if stores else "截图里没看到",
        "内搭或针织内容": {"yes": "出现过", "no": "没出现", "unknown": "未知"}[extraction.knitwear_present],
        "提到面料": "；".join(extraction.fabric_mentions[:3]) or "没有",
    }


def screen_shop(extraction: ShopExtraction, today: date) -> ShopScreening:
    """Gate first (may we contact at all), then how early to look at it. Never a win probability."""
    recorded = recorded_facts(extraction)

    # Layer 1: gate.
    if extraction.shop_type in NOT_PROACTIVE_TYPES:
        return _excluded(f"门槛没过：识别为{TYPE_NAMES_ZH[extraction.shop_type]}，不主动联系（认错了可以在上面改）", recorded)
    prices = parse_prices(extraction.price_mentions)
    exact_only = bool(prices) and not any(is_floor for _, is_floor in prices)
    top = max((amount for amount, _ in prices), default=None)
    if exact_only and top is not None and top < SUIT_PRICE_GATE:
        return _excluded(f"门槛没过：看得到的最高价格 {top} 元，低于 {SUIT_PRICE_GATE} 元", recorded)

    # Layer 2: ranking facts.
    ages = [age for age in (post_age_days(t, today) for t in extraction.recent_post_dates) if age is not None]
    latest = min(ages) if ages else None
    price_ok = top is not None and top >= SUIT_PRICE_GATE
    signals = {
        "A_active": Signal(
            value="unknown" if latest is None else ("yes" if latest <= ACTIVE_WITHIN_DAYS else "no"),
            evidence=extraction.recent_post_dates[:5],
        ),
        "C_price": Signal(
            value="yes" if price_ok else "unknown",
            evidence=extraction.price_mentions[:5],
        ),
    }
    is_suit_shop = extraction.shop_type == "suit_custom"
    reasons: list[str] = ["门槛通过" if is_suit_shop else f"识别为{TYPE_NAMES_ZH[extraction.shop_type]}，不是西装定制店，排在后面，由你决定"]
    reasons.append(
        f"价格看得见且够高（最高 {top} 元）" if price_ok
        else ("只看到起步价，够不够高不确定，建议补一张带价格的截图" if prices else "截图里没有价格，建议补一张带价格的截图")
    )
    reasons.append(
        "活跃情况未知" if latest is None
        else (f"最近 {latest} 天前更新" if latest <= STALE_AFTER_DAYS else f"最近一次更新在 {latest} 天前，超过 3 个月，消息可能没人看")
    )

    scene = main_scene(extraction)
    if scene == "wedding":
        reasons.append("主要做婚礼西装：婚礼一般配衬衫，我们不提供，排在后面（依据：" + "、".join(extraction.wedding_mentions[:3]) + "）")
    elif scene != "unknown":
        reasons.append(SCENE_NAMES_ZH[scene])
    signals["E_scene"] = Signal(
        value={"wedding": "no", "daily": "yes", "both": "yes", "unknown": "unknown"}[scene],
        evidence=(extraction.daily_mentions + extraction.wedding_mentions)[:5],
    )

    if scene == "wedding" or (latest is not None and latest > STALE_AFTER_DAYS):
        tier: Tier = "low"
    elif is_suit_shop and price_ok and latest is not None and latest <= ACTIVE_WITHIN_DAYS:
        tier = "high"
    else:
        tier = "medium"

    return ShopScreening(
        tier=tier,
        tier_reasons=reasons,
        prerequisite_met=is_suit_shop,
        signals=signals,
        recorded=recorded,
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
    """Earlier = look at it first: tier, then visible-and-high price, then most recently active.
    Recorded-only facts (pinned notes, fabric, account kind) deliberately play no part."""
    price_seen = screening.signals.get("C_price") is not None and screening.signals["C_price"].value == "yes"
    age = screening.latest_post_age_days if screening.latest_post_age_days is not None else 10_000
    return (TIER_ORDER[screening.tier], 0 if price_seen else 1, age)


def assign_routes(ordered: list[ShopScreening], dm_slots: int, comment_slots: int) -> None:
    """Hand out today's remaining quota in rank order: DMs first, then comments.

    The tier decides how early a shop is seen, not which route it gets (user 2026-10-05:
    either route can fit). Stale and excluded shops get no suggestion.
    """
    for screening in ordered:
        if screening.tier in ("low", "excluded"):
            screening.suggested_route = "none"
        elif dm_slots > 0:
            screening.suggested_route, dm_slots = "dm", dm_slots - 1
        elif comment_slots > 0:
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


# The two shapes a profile link takes: the web address bar, and the app's share link.
_PROFILE_URL = re.compile(
    r"https://(?:www\.xiaohongshu\.com/user/profile/[0-9A-Za-z]+|xhslink\.com/[0-9A-Za-z]+)[0-9A-Za-z/_\-?=&%.~+]*"
)


PROFILE_URL_HINT = "这不是小红书主页地址。电脑上复制对方主页的地址栏；手机上在对方主页点“分享—复制链接”"


def normalize_profile_url(value: str | None) -> str:
    """Pull the shop's profile link out of what the human pasted (the app's share text wraps
    the link in a sentence). Anything that is not a Xiaohongshu profile link comes back empty."""
    match = _PROFILE_URL.search((value or "").replace("http://", "https://"))
    return match.group(0) if match else ""


def audience_of(extraction: ShopExtraction) -> str:
    """Program rule, not a model guess: verified or multi-store accounts are read by
    staff; a personal account running a shop is read by the maker (user 2026-10-05)."""
    if extraction.account_kind == "enterprise_verified" or len(extraction.store_mentions) >= 2:
        return "store_account"
    if extraction.account_kind == "personal":
        return "personal_shop"
    return "unknown"


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
    scene = main_scene(extraction)
    if scene != "unknown":
        signal_lines.append(f"穿着场景：{SCENE_NAMES_ZH[scene]}")
    if extraction.daily_mentions:
        signal_lines.append("日常相关内容：" + "；".join(extraction.daily_mentions[:5]))
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
        "audience": audience_of(extraction),
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
    "PROFILE_URL_HINT",
    "normalize_profile_url",
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

