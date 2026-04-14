from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterable, List, Dict, Optional

import feedparser

from .sources import Source


@dataclass(frozen=True)
class Item:
    title: str
    url: str
    source_name: str
    category: str
    published_at: datetime
    description: str = ""  # 添加描述字段


def _parse_dt(entry) -> datetime:
    # feedparser returns struct_time in published_parsed/updated_parsed
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if st:
        return datetime.fromtimestamp(time.mktime(st), tz=timezone.utc)
    # Fallback: now (keeps pipeline moving)
    return datetime.now(tz=timezone.utc)


def fetch_items(sources: Iterable[Source]) -> List[Item]:
    items: List[Item] = []
    for src in sources:
        try:
            # Note: feedparser.parse() doesn't support timeout parameter
            feed = feedparser.parse(src.url, request_headers={"User-Agent": "aixux-digest-bot/1.0"})
            for e in feed.entries or []:
                title = (getattr(e, "title", "") or "").strip()
                link = (getattr(e, "link", "") or "").strip()
                if not title or not link:
                    continue
                
                # 提取描述（用于后续 AI 摘要）
                description = ""
                if hasattr(e, "summary"):
                    description = e.summary.strip()
                elif hasattr(e, "description"):
                    description = e.description.strip()
                
                items.append(
                    Item(
                        title=title,
                        url=link,
                        source_name=src.name,
                        category=src.category,
                        published_at=_parse_dt(e),
                        description=description,
                    )
                )
        except Exception as e:
            print(f"  ⚠️ 抓取 {src.name} 失败: {e}")
            continue
    
    return items


KEYWORDS = {
    "ai": [
        # 原有关键词
        "llm",
        "agent",
        "agents",
        "transformer",
        "diffusion",
        "multimodal",
        "reasoning",
        "alignment",
        "openai",
        "anthropic",
        "gemini",
        "gpt",
        "claude",
        "huggingface",
        # 新增：vibe coding 相关
        "vibe coding",
        "vibe",
        "ai coding",
        "code generation",
        "ai-assisted coding",
        "copilot",
        "cursor",
        "replit",
    ],
    "ux": [
        "ux",
        "user research",
        "usability",
        "accessibility",
        "a11y",
        "hci",
        "design system",
        "design systems",
        "information architecture",
        "ia",
        "interaction design",
        "service design",
        "user experience",
        "user interface",
        "ui",
    ],
    "product": [
        "product design",
        "product management",
        "roadmap",
        "growth",
        "onboarding",
        "metrics",
        "activation",
        "retention",
        "experimentation",
        "pm",
        "product",
    ],
}

# UX 领域著名专家名单
UX_EXPERTS = [
    "john maeda",
    "don norman",
    "jakob nielsen",
    "jared spool",
    "luke wroblewski",
    "stephen anderson",
    "alan cooper",
    "jesse james garrett",
    "steve krug",
    "whitney hess",
    "leah buley",
    "kim goodwin",
]

# 来源权重
SOURCE_WEIGHTS = {
    # AI 来源
    "OpenAI": 1.2,
    "Anthropic": 1.2,
    "Hugging Face": 1.2,
    "Google AI Blog": 1.1,
    
    # UX 来源（重点）
    "NNg": 1.3,
    "UX Collective": 1.2,
    "Smashing (UX)": 1.1,
    
    # Product 来源
    "Figma": 1.2,
    "Mind the Product": 1.2,
    "Atlassian Design": 1.1,
}


def _score_text(text: str, keywords: List[str]) -> int:
    """关键词匹配得分"""
    t = text.lower()
    return sum(1 for k in keywords if k in t)


def _has_ux_expert(text: str) -> bool:
    """检测是否提及 UX 专家"""
    t = text.lower()
    return any(expert in t for expert in UX_EXPERTS)


def _calculate_score(item: Item, max_age_days: int = 30) -> float:
    """
    综合评分系统（优化版）
    
    评分 = 关键词匹配(30%) + 时效性(50%) + 来源权重(20%)
           + UX专家加成 + vibe coding加成
    
    Args:
        item: 文章项
        max_age_days: 最大时间范围（用于计算时效性分数）
    """
    # 1. 关键词匹配得分
    category_keywords = KEYWORDS.get(item.category, [])
    keyword_score = _score_text(item.title + " " + item.description, category_keywords)
    
    # 2. 时效性得分（提高权重，越新越高）
    now = datetime.now(tz=timezone.utc)
    age_hours = (now - item.published_at).total_seconds() / 3600
    max_hours = max_age_days * 24
    # 线性递减：最新=1.0, 最旧=0.0
    recency_score = max(0, 1.0 - (age_hours / max_hours))
    
    # 3. 来源权重
    source_weight = SOURCE_WEIGHTS.get(item.source_name, 1.0)
    
    # 4. 特殊加成
    bonus = 1.0
    
    # AI 分类：vibe coding 加成
    if item.category == "ai":
        text_lower = (item.title + " " + item.description).lower()
        if "vibe coding" in text_lower or "vibe" in text_lower:
            bonus += 0.3  # +30% 加成
    
    # UX 分类：专家提及加成
    if item.category == "ux":
        if _has_ux_expert(item.title + " " + item.description):
            bonus += 0.25  # +25% 加成
    
    # 综合得分（调整权重分配）
    total_score = (
        keyword_score * 0.3 +
        recency_score * 0.5 +  # 时效性权重提高
        source_weight * 0.2
    ) * bonus
    
    return total_score


def rank_and_filter(
    items: Iterable[Item],
    max_items: int = 8,
    category_limits: Dict[str, int] = None,
    time_limit_days: Optional[int] = None
) -> List[Item]:
    """
    智能排序和过滤
    
    Args:
        items: 所有抓取的资讯
        max_items: 总数限制（默认 8）
        category_limits: 每个分类的限制（默认 AI:4, UX:4）
        time_limit_days: 时间限制（天数），None 表示不限制
    
    Returns:
        排序后的资讯列表
    """
    # 默认分类限制
    if category_limits is None:
        category_limits = {
            "ai": 4,
            "ux": 4,
        }
    
    # 时间过滤（如果指定）
    filtered_items = items
    if time_limit_days is not None:
        cutoff_time = datetime.now(tz=timezone.utc) - timedelta(days=time_limit_days)
        filtered_items = [it for it in items if it.published_at >= cutoff_time]
    
    # 按分类分组
    by_category: Dict[str, List[tuple[float, datetime, Item]]] = {
        "ai": [],
        "ux": [],
    }
    
    for item in filtered_items:
        if item.category in by_category:
            # 根据时间范围调整评分
            score = _calculate_score(item, max_age_days=time_limit_days or 90)
            by_category[item.category].append((score, item.published_at, item))
    
    # 每个分类内排序：先按分数，再按时间
    for category in by_category:
        by_category[category].sort(key=lambda x: (x[0], x[1]), reverse=True)
    
    # 从每个分类取 top N
    result: List[Item] = []
    seen_urls = set()
    
    # 严格按分类限制分配
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
    
    # 如果某个分类不足，从高分内容补充
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
