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
    # 1. Medium URL 提取
    m = re.search(r"medium\.com/@([\w-]+)/", url)
    if m:
        return f"@{m.group(1)}"

    # 2. dc:creator
    dc_creator = getattr(entry, "dc_creator", "") or ""
    if dc_creator.strip():
        return dc_creator.strip()

    # 3. entry.author
    author = getattr(entry, "author", "") or ""
    if author.strip():
        return author.strip()

    # 4. 抓取页面 <meta name="author">
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

    # 5. 兜底
    return source_name


@dataclass(frozen=True)
class Item:
    title: str
    url: str
    source_name: str
    category: str
    published_at: datetime
    description: str = ""
    author: str = ""  # 作者标识，优先具体作者名，兜底用 source_name


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

                # 提取作者（多级 fallback）
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


def _calculate_score(item: Item, max_age_days: int = 30,
                     curated: set = None, blocked: set = None) -> float:
    """
    综合评分系统

    总分 = (关键词×0.3 + 时效性×0.5 + 来源权重×0.2) × 特殊加成 × 作者加成

    作者加成：
      curated 作者 → ×1.5
      普通作者     → ×1.0
      blocked 作者 → ×0.3
    """
    curated = curated or set()
    blocked = blocked or set()

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

    total_score = (
        keyword_score * 0.3 +
        recency_score * 0.5 +
        source_weight * 0.2
    ) * bonus * author_bonus

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
