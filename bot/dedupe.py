from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, List, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .fetcher import Item


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "mkt_tok",
}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in TRACKING_PARAMS]
    query.sort()
    clean = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), urlencode(query), ""))
    return clean


def fingerprint(item: Item) -> str:
    base = f"{canonicalize_url(item.url)}\n{item.title.strip()}".encode("utf-8", errors="ignore")
    return hashlib.sha256(base).hexdigest()


def load_seen(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("seen"), list):
            return set(str(x) for x in obj["seen"])
        if isinstance(obj, list):
            return set(str(x) for x in obj)
    except Exception:
        return set()
    return set()


def save_seen(path: Path, seen: Set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {"seen": sorted(seen)}
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def filter_new(items: Iterable[Item], seen: Set[str]) -> Tuple[List[Item], Set[str]]:
    new_items: List[Item] = []
    updated = set(seen)
    for it in items:
        fp = fingerprint(it)
        if fp in updated:
            continue
        new_items.append(it)
        updated.add(fp)
    return new_items, updated

