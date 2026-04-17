from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterable, List, Dict, Optional

import feedparser
import requests

from .sources import Source


def _extract_author(entry, url: str, source_name: str) -> str:
    """
    按优先级提取作者名：
    1. Medium URL 里的 @username
    2. dc:creator 字段
    3. entry.author 字段
    4. 抓取文章页面 <meta name="author">
    5. 兜底：source_name
    """
    m = re.search(r"medium\.com/@([\w-]+)/", url)
    if m:
        return f"@{m.group(1)}"

    dc_creator = getattr(entry, "dc_creator", "") or ""
    if dc_creator.strip():
        return dc_creator.strip()

    author = getattr(entry, "author", "") or ""
    if author.strip():
        return author.strip()

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "aixux-digest-bot/1.0"},
            timeout=8,
            allow_redirects=True,
        )
        if resp.status_code == 200:
            m = re.search(
                r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']',
                resp.text,
                re.IGNORECASE,
            )
            if m:
                return m.group(1).strip()
    except Exception:
        pass

    return source_name


@dataclass(frozen=True)
class Item:
    title: str
    url: str
    source_name: str
    category: str
    published_at: datetime
    description: str = ""
    author: str = ""


def _parse_dt(entry) -> datetime:
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if st:
        return datetime.fromtimestamp(time.mktime(st), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


def fetch_items(sources: Iterable[Source]) -> List[Item]:
    items: List[Item] = []
    for src in sources:
        try:
            feed = feedparser.parse(src.url, request_headers={"User-Agent": "aixux-digest-bot/1.0"})
            for e in feed.entries or []:
                title = (getattr(e, "title", "") or "").strip()
                link = (getattr(e, "link", "") or "").strip()
                if not title or not link:
                    continue

                description = ""
                if hasattr(e, "summary"):
                    description = e.summary.strip()
                elif hasattr(e, "description"):
                    description = e.description.strip()

                author = _extract_author(e, link, src.name)

                items.append(
                    Item(
                        title=title,
                        url=link,
                        source_name=src.name,
                        category=src.category,
                        published_at=_parse_dt(e),
                        description=description,
                        author=author,
                    )
                )
        except Exception as e:
            print(f"  ⚠️ 抓取 {src.name} 失败: {e}")
            continue

    return items


KEYWORDS = {
    "ai": [
        "llm", "agent", "agents", "transformer", "diffusion", "multimodal",
        "reasoning", "alignment", "openai", "anthropic", "gemini", "gpt",
        "claude", "huggingface", "vibe coding", "vibe", "ai coding",
        "code generation", "ai-assisted coding", "copilot", "cursor", "replit",
    ],
    "ux": [
        "ux", "user research", "usability", "accessibility", "a11y", "hci",
        "design system", "design systems", "information architecture", "ia",
        "interaction design", "service design", "user experience", "user interface", "ui",
    ],
    "product": [
        "product design", "product management", "roadmap", "growth", "onboarding",
        "metrics", "activation", "retention", "experimentation", "pm", "product",
    ],
}

UX_EXPERTS = [
    "john maeda", "don norman", "jakob nielsen", "jared spool",
    "luke wroblewski", "stephen anderson", "alan cooper",
    "jesse james garrett", "steve krug", "whitney hess", "leah buley", "kim goodwin",
]

SOURCE_WEIGHTS = {
    "OpenAI": 1.2, "Anthropic": 1.2, "Hugging Face": 1.2, "Google AI Blog": 1.1,
    "NNg": 1.3, "UX Collective": 1.2, "Smashing (UX)": 1.1,
    "Figma": 1.2, "Mind the Product": 1.2, "Atlassian Design": 1.1,
}


def _score_text(text: str, keywords: List[str]) -> int:
    t = text.lower()
    return sum(1 for k in keywords if k in t)


def _has_ux_expert(text: str) -> bool:
    t = text.lower()
    return any(expert in t for expert in UX_EXPERTS)


# ── 偏好加成（新增）──────────────────────────────────────────────────────────

def _keyword_preference_boost(text: str, profile: dict) -> float:
    """
    关键词字符串匹配，快速计算偏好加成。
    返回值范围：0.3 ~ 1.5
    """
    high_signals = profile.get("high_quality_signals", [])
    low_signals = profile.get("low_quality_signals", [])

    if not high_signals and not low_signals:
        return 1.0

    text_lower = text.lower()
    boost = 1.0

    for signal in high_signals:
        if signal["keyword"].lower() in text_lower:
            # count 越多、weight 越高，加成越大；单信号上限 +0.3
            increment = min(0.3, 0.1 * signal["weight"] * min(signal["count"], 3))
            boost += increment

    for signal in low_signals:
        if signal["keyword"].lower() in text_lower:
            decrement = min(0.3, 0.1 * signal["weight"] * min(signal["count"], 3))
            boost -= decrement

    return round(max(0.3, min(1.5, boost)), 3)


def _ai_preference_boost(text: str, profile: dict) -> float:
    """
    用 MiniMax 做语义匹配，准确度更高但每次推送多一次 API 调用。
    USE_AI_PREFERENCE=true 时启用。
    失败时自动降级为关键词匹配，不影响正常推送。
    """
    from bot.ai_helper import call_minimax_api

    high_signals = profile.get("high_quality_signals", [])
    low_signals = profile.get("low_quality_signals", [])

    if not high_signals and not low_signals:
        return 1.0

    high_kws = [s["keyword"] for s in high_signals]
    low_kws = [s["keyword"] for s in low_signals]

    prompt = f"""以下是一篇文章的标题和摘要：
{text[:400]}

用户的历史内容偏好：
高质量信号（用户喜欢的内容类型）：{', '.join(high_kws) if high_kws else '无'}
低质量信号（用户不喜欢的内容类型）：{', '.join(low_kws) if low_kws else '无'}

请判断这篇文章与用户偏好的匹配程度，返回 0.3 到 1.5 之间的一个浮点数：
- 强烈命中高质量信号 → 1.3~1.5
- 轻微命中高质量信号 → 1.1~1.2
- 与偏好无关 → 1.0
- 轻微命中低质量信号 → 0.7~0.9
- 强烈命中低质量信号 → 0.3~0.6

只返回数字，不要任何解释或其他文字。"""

    try:
        result = call_minimax_api(prompt)
        if result:
            boost = float(result.strip())
            return round(max(0.3, min(1.5, boost)), 3)
    except Exception as e:
        print(f"  ⚠️ AI 偏好匹配失败，降级为关键词匹配: {e}")

    # 降级
    return _keyword_preference_boost(text, profile)


def get_preference_boost(item: "Item", profile: dict, use_ai: bool = False) -> float:
    """
    统一入口：根据开关选择关键词或 AI 匹配。
    profile 为空时直接返回 1.0，不影响无偏好数据时的评分。
    """
    if not profile or (
        not profile.get("high_quality_signals") and
        not profile.get("low_quality_signals")
    ):
        return 1.0

    text = f"{item.title} {item.description}"

    if use_ai:
        return _ai_preference_boost(text, profile)
    else:
        return _keyword_preference_boost(text, profile)


# ── 评分系统 ──────────────────────────────────────────────────────────────────

def _calculate_score(
    item: Item,
    max_age_days: int = 30,
    curated: set = None,
    blocked: set = None,
    preference_profile: dict = None,
    use_ai_preference: bool = False,
) -> float:
    """
    综合评分系统

    总分 = (关键词×0.3 + 时效性×0.5 + 来源权重×0.2) × 特殊加成 × 作者加成 × 偏好加成

    偏好加成（新增）：
      命中高质量信号 → 最高 ×1.5
      无偏好数据     → ×1.0（不影响）
      命中低质量信号 → 最低 ×0.3
    """
    curated = curated or set()
    blocked = blocked or set()
    preference_profile = preference_profile or {}

    category_keywords = KEYWORDS.get(item.category, [])
    keyword_score = _score_text(item.title + " " + item.description, category_keywords)

    now = datetime.now(tz=timezone.utc)
    age_hours = (now - item.published_at).total_seconds() / 3600
    max_hours = max_age_days * 24
    recency_score = max(0, 1.0 - (age_hours / max_hours))

    source_weight = SOURCE_WEIGHTS.get(item.source_name, 1.0)

    bonus = 1.0
    if item.category == "ai":
        text_lower = (item.title + " " + item.description).lower()
        if "vibe coding" in text_lower or "vibe" in text_lower:
            bonus += 0.3
    if item.category == "ux":
        if _has_ux_expert(item.title + " " + item.description):
            bonus += 0.25

    author_key = item.author.lower().strip()
    if author_key in curated:
        author_bonus = 1.5
    elif author_key in blocked:
        author_bonus = 0.3
    else:
        author_bonus = 1.0

    # 偏好加成（新增）
    preference_bonus = get_preference_boost(item, preference_profile, use_ai=use_ai_preference)

    total_score = (
        keyword_score * 0.3 +
        recency_score * 0.5 +
        source_weight * 0.2
    ) * bonus * author_bonus * preference_bonus

    return total_score


def rank_and_filter(
    items: Iterable[Item],
    max_items: int = 8,
    category_limits: Dict[str, int] = None,
    time_limit_days: Optional[int] = None
) -> List[Item]:
    if category_limits is None:
        category_limits = {"ai": 4, "ux": 4}

    filtered_items = items
    if time_limit_days is not None:
        cutoff_time = datetime.now(tz=timezone.utc) - timedelta(days=time_limit_days)
        filtered_items = [it for it in items if it.published_at >= cutoff_time]

    by_category: Dict[str, List[tuple]] = {"ai": [], "ux": []}

    for item in filtered_items:
        if item.category in by_category:
            score = _calculate_score(item, max_age_days=time_limit_days or 90)
            by_category[item.category].append((score, item.published_at, item))

    for category in by_category:
        by_category[category].sort(key=lambda x: (x[0], x[1]), reverse=True)

    result: List[Item] = []
    seen_urls = set()

    for category, limit in category_limits.items():
        count = 0
        for score, pub_time, item in by_category.get(category, []):
            if item.url in seen_urls:
                continue
            result.append(item)
            seen_urls.add(item.url)
            count += 1
            if count >= limit:
                break

    if len(result) < max_items:
        all_remaining = []
        for category in by_category:
            for score, pub_time, item in by_category[category]:
                if item.url not in seen_urls:
                    all_remaining.append((score, pub_time, item))
        all_remaining.sort(key=lambda x: (x[0], x[1]), reverse=True)
        need = max_items - len(result)
        for score, pub_time, item in all_remaining[:need]:
            result.append(item)
            seen_urls.add(item.url)

    return result[:max_items]
