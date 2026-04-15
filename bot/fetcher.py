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
                r'<meta[^>]+name=["\'']author["\''][^>]+content=["\'']([^"\'\']+)["\'']',
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
