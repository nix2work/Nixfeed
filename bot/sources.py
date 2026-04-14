from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    category: str


def default_sources() -> Dict[str, List[Source]]:
    """
    优化的资讯源配置
    - AI 源增加到 8 个（覆盖 vibe coding、研究、工具）
    - UX 源增加到 6 个（确保充足）
    """
    return {
        "ai": [
            # 核心公司（必须）
            Source(name="OpenAI", url="https://openai.com/blog/rss/", category="ai"),
            Source(name="Anthropic", url="https://www.anthropic.com/news/rss.xml", category="ai"),
            Source(name="Google AI Blog", url="https://blog.google/technology/ai/rss/", category="ai"),
            
            # 开源和工具（vibe coding 重点）
            Source(name="Hugging Face", url="https://huggingface.co/blog/feed.xml", category="ai"),
            Source(name="LangChain", url="https://blog.langchain.dev/rss/", category="ai"),
            
            # AI 编程工具
            Source(name="GitHub AI Blog", url="https://github.blog/category/ai-and-ml/feed/", category="ai"),
            Source(name="Replit Blog", url="https://blog.replit.com/feed.xml", category="ai"),
            
            # 研究前沿
            Source(name="Meta AI", url="https://ai.meta.com/blog/rss/", category="ai"),
        ],
        "ux": [
            # 权威 UX 资源
            Source(name="NNg", url="https://www.nngroup.com/feed/rss/", category="ux"),
            Source(name="UX Collective", url="https://uxdesign.cc/feed", category="ux"),
            Source(name="Smashing (UX)", url="https://www.smashingmagazine.com/category/ux/feed/", category="ux"),
            
            # 补充 UX 资源
            Source(name="A List Apart", url="https://alistapart.com/main/feed/", category="ux"),
            Source(name="UX Booth", url="https://www.uxbooth.com/feed/", category="ux"),
            Source(name="UX Matters", url="https://www.uxmatters.com/index.xml", category="ux"),
        ],
    }


def load_sources_from_env() -> Optional[Dict[str, List[Source]]]:
    """从环境变量加载自定义源"""
    raw = os.getenv("SOURCES_JSON")
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        out: Dict[str, List[Source]] = {}
        for category, items in obj.items():
            out[str(category)] = [
                Source(
                    name=str(it.get("name", "Unnamed")),
                    url=str(it["url"]),
                    category=str(category),
                )
                for it in (items or [])
                if isinstance(it, dict) and it.get("url")
            ]
        return out
    except Exception as e:
        print(f"⚠️ 解析 SOURCES_JSON 失败: {e}")
        return None


def get_sources() -> List[Source]:
    """获取所有资讯源（环境变量优先，否则使用默认），并合并 curated 作者 RSS"""
    by_cat = load_sources_from_env() or default_sources()
    sources: List[Source] = []
    for cat, items in by_cat.items():
        for s in items:
            sources.append(Source(name=s.name, url=s.url, category=cat))

    # 合并 curated 作者的 Medium RSS
    try:
        from .author_manager import get_curated_sources
        for item in get_curated_sources():
            sources.append(Source(name=item["name"], url=item["url"], category=item["category"]))
        curated_count = len(get_curated_sources())
        if curated_count:
            print(f"  + 合并 {curated_count} 个精选作者 RSS 源")
    except Exception as e:
        print(f"  ⚠️ 加载 curated 作者源失败: {e}")

    return sources
