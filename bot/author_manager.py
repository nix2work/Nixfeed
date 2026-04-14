from __future__ import annotations

"""
author_manager.py
管理 curated_authors.json 和 author_scores.json。

逻辑规则：
  - 文章打分 → 找到对应作者 → 更新累计分
  - 累计平均分 >= HIGH_SCORE_THRESHOLD → 自动加入 curated_authors
  - 累计平均分 <= LOW_SCORE_THRESHOLD  → 加入 blocked_authors（不再推送）
  - 手动「订阅 @username」→ 直接加入 curated_authors
  - 手动「取消 @username」→ 从两个列表移除
"""

import json
import re
from pathlib import Path
from typing import Optional

# 评分阈值
HIGH_SCORE_THRESHOLD = 4.0   # 平均分 >= 4，自动 curate
LOW_SCORE_THRESHOLD  = 2.0   # 平均分 <= 2，自动 block
MIN_VOTES_FOR_AUTO   = 2     # 至少打过 N 次分才触发自动规则

STATE_DIR = Path("state")
CURATED_PATH  = STATE_DIR / "curated_authors.json"
SCORES_PATH   = STATE_DIR / "author_scores.json"
BLOCKED_PATH  = STATE_DIR / "blocked_authors.json"
PENDING_PATH  = STATE_DIR / "pending_articles.json"


# ── 文件 I/O ──────────────────────────────────────────────────────────────────

def _load(path: Path, default: dict) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Pending 文章（编号 ↔ 作者/URL 映射） ────────────────────────────────────────

def load_pending() -> dict:
    return _load(PENDING_PATH, {"date": "", "articles": {}})


def save_pending(data: dict) -> None:
    _save(PENDING_PATH, data)


def register_articles(articles: list[dict], date_str: str) -> None:
    """
    把当天推送文章注册到 pending，分配编号 1, 2, 3...

    articles 每项需包含：
      url, title, source_name, author（可选，从 Medium RSS 解析）
    """
    pending = {"date": date_str, "articles": {}}
    for i, art in enumerate(articles, start=1):
        pending["articles"][str(i)] = {
            "url": art.get("url", ""),
            "title": art.get("title", ""),
            "source_name": art.get("source_name", ""),
            "author": art.get("author", ""),          # Medium username，可能为空
            "category": art.get("category", ""),
        }
    save_pending(pending)
    print(f"📝 注册 {len(articles)} 篇文章到 pending（{date_str}）")


# ── 评分处理 ──────────────────────────────────────────────────────────────────

def load_scores() -> dict:
    return _load(SCORES_PATH, {"scores": {}})


def save_scores(data: dict) -> None:
    _save(SCORES_PATH, data)


def apply_scores(score_map: dict[int, int]) -> list[str]:
    """
    把打分结果写入 author_scores.json，
    返回触发了状态变更的作者列表（用于日志）。
    """
    pending = load_pending()
    scores_data = load_scores()
    scores = scores_data.setdefault("scores", {})
    changed = []

    for idx, score in score_map.items():
        article = pending["articles"].get(str(idx))
        if not article:
            print(f"  ⚠️ 编号 {idx} 未找到对应文章，跳过")
            continue

        author = article.get("author", "").strip()
        source = article.get("source_name", "")
        # 用作者 username 或来源名作为 key
        key = author if author else source
        if not key:
            continue

        entry = scores.setdefault(key, {"total": 0, "count": 0, "author": author, "source": source})
        entry["total"] += score
        entry["count"] += 1
        avg = entry["total"] / entry["count"]
        print(f"  ✏️  [{idx}] {key}: 本次{score}分，累计均分{avg:.1f}（共{entry['count']}次）")
        changed.append(key)

    save_scores(scores_data)
    _auto_update_lists(scores)
    return changed


def _auto_update_lists(scores: dict) -> None:
    """根据累计均分自动更新 curated / blocked 列表"""
    for key, entry in scores.items():
        if entry["count"] < MIN_VOTES_FOR_AUTO:
            continue
        avg = entry["total"] / entry["count"]
        author = entry.get("author", "")
        if avg >= HIGH_SCORE_THRESHOLD and author:
            _add_curated(author, reason=f"自动：均分{avg:.1f}")
        elif avg <= LOW_SCORE_THRESHOLD and author:
            _add_blocked(author, reason=f"自动：均分{avg:.1f}")


# ── Curated 作者列表 ──────────────────────────────────────────────────────────

def load_curated() -> dict:
    return _load(CURATED_PATH, {"authors": []})


def _add_curated(username: str, reason: str = "手动订阅") -> bool:
    """添加作者到 curated 列表，已存在则跳过。返回是否新增。"""
    data = load_curated()
    existing = {a["username"] for a in data["authors"]}
    if username in existing:
        return False

    # 从 blocked 移除（如果存在）
    _remove_blocked(username)

    data["authors"].append({
        "username": username,
        "rss_url": f"https://medium.com/feed/@{username}",
        "reason": reason,
    })
    _save(CURATED_PATH, data)
    print(f"  ✅ 已订阅作者：@{username}（{reason}）")
    return True


def add_curated_manual(username: str) -> bool:
    """飞书「订阅 @username」指令的入口"""
    return _add_curated(username, reason="手动订阅")


def remove_curated(username: str) -> bool:
    """从 curated 列表移除作者"""
    data = load_curated()
    before = len(data["authors"])
    data["authors"] = [a for a in data["authors"] if a["username"] != username]
    if len(data["authors"]) < before:
        _save(CURATED_PATH, data)
        print(f"  🗑️  已从订阅列表移除：@{username}")
        return True
    return False


def get_curated_sources() -> list[dict]:
    """返回 curated 作者的 RSS 源列表，供 sources.py 合并"""
    data = load_curated()
    return [
        {"name": f"@{a['username']}", "url": a["rss_url"], "category": "ux"}
        for a in data["authors"]
    ]


# ── Blocked 作者列表 ──────────────────────────────────────────────────────────

def load_blocked() -> set[str]:
    data = _load(BLOCKED_PATH, {"authors": []})
    return {a["username"] for a in data.get("authors", [])}


def _add_blocked(username: str, reason: str = "自动屏蔽") -> None:
    data = _load(BLOCKED_PATH, {"authors": []})
    existing = {a["username"] for a in data["authors"]}
    if username not in existing:
        data["authors"].append({"username": username, "reason": reason})
        _save(BLOCKED_PATH, data)
        print(f"  🚫 已屏蔽作者：@{username}（{reason}）")
    # 同时从 curated 移除
    remove_curated(username)


def _remove_blocked(username: str) -> None:
    data = _load(BLOCKED_PATH, {"authors": []})
    data["authors"] = [a for a in data["authors"] if a["username"] != username]
    _save(BLOCKED_PATH, data)


def is_blocked(author: str) -> bool:
    return author in load_blocked()


# ── 处理来自飞书的所有指令 ───────────────────────────────────────────────────

def process_commands(commands: dict) -> None:
    """
    统一处理 feishu_reader.collect_commands() 返回的指令集。
    """
    # 1. 打分
    if commands["scores"]:
        print(f"\n📊 处理打分指令...")
        apply_scores(commands["scores"])

    # 2. 订阅
    for username in commands["subscribe"]:
        print(f"\n➕ 处理订阅指令：@{username}")
        add_curated_manual(username)

    # 3. 取消订阅
    for username in commands["unsubscribe"]:
        print(f"\n➖ 处理取消订阅：@{username}")
        remove_curated(username)
        _remove_blocked(username)
