from __future__ import annotations

"""
author_manager.py
管理 curated_authors.json、author_scores.json 和 preference_profile.json。

逻辑规则：
  - 文章打分 → 找到对应作者 → 更新累计分
  - 累计平均分 >= HIGH_SCORE_THRESHOLD → 自动加入 curated_authors
  - 累计平均分 <= LOW_SCORE_THRESHOLD  → 加入 blocked_authors（不再推送）
  - 手动「订阅 @username」→ 直接加入 curated_authors
  - 手动「取消 @username」→ 从两个列表移除
  - 打分附带原因 → AI 提炼信号关键词 → 累积到 preference_profile
"""

import json
from datetime import date
from pathlib import Path
from typing import Optional

# 评分阈值
HIGH_SCORE_THRESHOLD = 4.0
LOW_SCORE_THRESHOLD  = 2.0
MIN_VOTES_FOR_AUTO   = 2

STATE_DIR       = Path("state")
CURATED_PATH    = STATE_DIR / "curated_authors.json"
SCORES_PATH     = STATE_DIR / "author_scores.json"
BLOCKED_PATH    = STATE_DIR / "blocked_authors.json"
PENDING_PATH    = STATE_DIR / "pending_articles.json"
PREFERENCE_PATH = STATE_DIR / "preference_profile.json"   # 新增


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


# ── Pending 文章 ─────────────────────────────────────────────────────────────

def load_pending() -> dict:
    return _load(PENDING_PATH, {"date": "", "articles": {}})


def save_pending(data: dict) -> None:
    _save(PENDING_PATH, data)


def register_articles(articles: list[dict], date_str: str) -> None:
    """把当天推送文章注册到 pending，分配编号 1, 2, 3..."""
    pending = {"date": date_str, "articles": {}}
    for i, art in enumerate(articles, start=1):
        pending["articles"][str(i)] = {
            "url":         art.get("url", ""),
            "title":       art.get("title", ""),
            "source_name": art.get("source_name", ""),
            "author":      art.get("author", ""),
            "category":    art.get("category", ""),
            "description": art.get("description", ""),   # 新增，供偏好匹配用
        }
    save_pending(pending)
    print(f"📝 注册 {len(articles)} 篇文章到 pending（{date_str}）")


# ── 评分处理 ──────────────────────────────────────────────────────────────────

def load_scores() -> dict:
    return _load(SCORES_PATH, {"scores": {}})


def save_scores(data: dict) -> None:
    _save(SCORES_PATH, data)


def apply_scores(score_map: dict[int, int]) -> list[str]:
    """把打分结果写入 author_scores.json，返回触发状态变更的作者列表"""
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


# ── 偏好画像（新增） ──────────────────────────────────────────────────────────

def load_preference_profile() -> dict:
    """加载偏好画像，不存在时返回空结构"""
    return _load(PREFERENCE_PATH, {
        "high_quality_signals": [],
        "low_quality_signals": [],
    })


def save_preference_profile(profile: dict) -> None:
    _save(PREFERENCE_PATH, profile)


def _extract_signal_keyword(reason: str, score: int) -> Optional[str]:
    """
    用 MiniMax 把用户打分原因提炼成 1 个核心信号关键词（2-8字）。
    例："喜欢AI Agent实战案例，很有启发" → "AI Agent实战"
    失败时返回 None，不影响主流程。
    """
    from bot.ai_helper import call_minimax_api

    quality = "高质量" if score >= 4 else "低质量"
    prompt = f"""用户给一篇文章打了 {score} 分（{quality}），原因是："{reason}"

请提炼出 1 个核心信号关键词（2-8个汉字或英文词组），代表这篇文章让用户觉得{quality}的本质原因。
只返回关键词本身，不要任何解释、标点或额外文字。

示例：
原因"喜欢AI Agent实战案例" → AI Agent实战
原因"太技术了，看不懂" → 技术细节
原因"有具体数据支撑" → 数据驱动
原因"实际工作可以直接用" → 可落地方法"""

    try:
        result = call_minimax_api(prompt)
        if result:
            keyword = result.strip().strip("\"'""''").strip()
            return keyword[:20] if keyword else None
    except Exception as e:
        print(f"  ⚠️ 信号关键词提炼失败: {e}")
    return None


def update_preference_from_scores(score_reasons: list[dict]) -> None:
    """
    处理带原因的打分列表，提炼信号关键词，更新偏好画像。

    score_reasons 格式（来自 feishu_reader.collect_commands）：
      [{"article_num": 1, "score": 5, "reason": "喜欢实战案例"}, ...]

    只处理有 reason 的条目，无 reason 的直接跳过。
    """
    items_with_reason = [x for x in score_reasons if x.get("reason")]
    if not items_with_reason:
        print("  ℹ️ 本次无带原因的打分，跳过偏好更新")
        return

    print(f"\n🧠 处理偏好批注（共 {len(items_with_reason)} 条带原因）...")
    pending = load_pending()
    profile = load_preference_profile()
    today = date.today().isoformat()

    for item in items_with_reason:
        article_num = item["article_num"]
        score = item["score"]
        reason = item["reason"]

        # 读取文章标题，丰富上下文
        article = pending["articles"].get(str(article_num), {})
        title = article.get("title", "")
        context = f"文章标题：{title}\n用户原因：{reason}" if title else reason

        print(f"  [{article_num}] 分数:{score} | 原因:{reason}")

        keyword = _extract_signal_keyword(context, score)
        if not keyword:
            print(f"    ⚠️ 关键词提炼失败，跳过")
            continue

        print(f"    → 提炼关键词：{keyword}")

        # 高分（4-5）→ 高质量信号；低分（1-2）→ 低质量信号；3分中性不处理
        if score >= 4:
            target = "high_quality_signals"
        elif score <= 2:
            target = "low_quality_signals"
        else:
            print(f"    ℹ️ 3分中性，不更新偏好画像")
            continue

        signals = profile[target]
        existing = next((s for s in signals if s["keyword"] == keyword), None)

        if existing:
            existing["count"] += 1
            # 权重随出现次数增长，上限 1.0
            existing["weight"] = min(1.0, round(existing["weight"] + 0.1, 2))
            existing["last_seen"] = today
            print(f"    ✅ 已有信号强化：{keyword}（count={existing['count']}, weight={existing['weight']}）")
        else:
            signals.append({
                "keyword": keyword,
                "count": 1,
                "weight": 0.5,   # 新信号初始权重保守
                "last_seen": today,
            })
            print(f"    ✅ 新增信号：{keyword} → [{target}]")

    save_preference_profile(profile)
    print("💾 偏好画像已更新")


# ── Curated 作者列表 ──────────────────────────────────────────────────────────

def load_curated() -> dict:
    return _load(CURATED_PATH, {"authors": []})


def _add_curated(username: str, reason: str = "手动订阅") -> bool:
    data = load_curated()
    existing = {a["username"] for a in data["authors"]}
    if username in existing:
        return False
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
    return _add_curated(username, reason="手动订阅")


def remove_curated(username: str) -> bool:
    data = load_curated()
    before = len(data["authors"])
    data["authors"] = [a for a in data["authors"] if a["username"] != username]
    if len(data["authors"]) < before:
        _save(CURATED_PATH, data)
        print(f"  🗑️  已从订阅列表移除：@{username}")
        return True
    return False


def get_curated_sources() -> list[dict]:
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
    commands 新增 score_reasons 字段，用于偏好学习。
    """
    # 1. 打分（更新作者分数）
    if commands["scores"]:
        print(f"\n📊 处理打分指令...")
        apply_scores(commands["scores"])

    # 2. 偏好批注（从带原因的打分中学习，新增）
    if commands.get("score_reasons"):
        update_preference_from_scores(commands["score_reasons"])

    # 3. 订阅
    for username in commands["subscribe"]:
        print(f"\n➕ 处理订阅指令：@{username}")
        add_curated_manual(username)

    # 4. 取消订阅
    for username in commands["unsubscribe"]:
        print(f"\n➖ 处理取消订阅：@{username}")
        remove_curated(username)
        _remove_blocked(username)
