from __future__ import annotations

"""
feishu_reader.py
读取飞书群消息，解析打分指令和订阅指令。

支持的指令格式：
  打分（旧格式）：「1:5 2:3 3:1」
  打分（新格式）：「1:5 | 喜欢AI Agent实战案例」（带原因，支持换行）
  订阅作者：「订阅 @username」或「subscribe @username」
  取消订阅：「取消 @username」或「unsubscribe @username」
"""

import os
import re
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional


# ── 飞书 API 工具 ─────────────────────────────────────────────────────────────

def _get_tenant_token() -> Optional[str]:
    """用 App ID + Secret 换取 tenant_access_token"""
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        print("⚠️ 缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET")
        return None

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        print(f"⚠️ 获取 tenant_access_token 失败: {data}")
        return None
    return data["tenant_access_token"]


def _get_bot_open_id(token: str) -> Optional[str]:
    """获取机器人自身的 open_id（用于排除机器人自己发的消息）"""
    resp = requests.get(
        "https://open.feishu.cn/open-apis/bot/v3/info",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        print(f"⚠️ 获取 bot info 失败: {data}")
        return None
    return data.get("bot", {}).get("open_id")


def fetch_recent_messages(token: str, chat_id: str, since_hours: int = 6) -> list[dict]:
    """拉取指定会话最近 since_hours 小时内的消息"""
    since_ts = int((datetime.now(tz=timezone.utc) - timedelta(hours=since_hours)).timestamp())

    resp = requests.get(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "container_id_type": "chat",
            "container_id": chat_id,
            "start_time": str(since_ts),
            "page_size": 50,
            "sort_type": "ByCreateTimeDesc",
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        print(f"⚠️ 拉取消息失败: {data}")
        return []

    return data.get("data", {}).get("items", [])


# ── 指令解析 ──────────────────────────────────────────────────────────────────

def parse_scores(text: str) -> dict[int, int]:
    """
    解析打分指令，返回 {文章编号: 分数}。（向后兼容，不含原因）

    支持格式：
      「1:5 2:3 3:1」「1:5, 2:3」「1:5 | 原因」（原因部分忽略）
    """
    pattern = r"\b(\d+)\s*[:：\-]\s*([1-5])\b"
    matches = re.findall(pattern, text)
    return {int(idx): int(score) for idx, score in matches}


def parse_scores_with_reasons(text: str) -> list[dict]:
    """
    解析带原因的打分指令，返回列表。

    每项结构：
      {
        "article_num": int,
        "score": int,
        "reason": str | None    # 无原因时为 None
      }

    支持格式（换行或分号分隔，可混用）：
      1:5 | 喜欢AI Agent实战案例
      2:1 | 太技术了，看不懂
      3:4
      4:3 | 有案例但略浅
    """
    results = []
    # 按换行或分号切分，每行独立解析
    lines = re.split(r"[\n;；]", text)

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 匹配：编号:分数 可选(| 原因)
        m = re.search(r"\b(\d+)\s*[:：]\s*([1-5])\b\s*(?:[|｜]\s*(.+))?", line)
        if m:
            reason_raw = m.group(3)
            reason = reason_raw.strip() if reason_raw else None
            results.append({
                "article_num": int(m.group(1)),
                "score": int(m.group(2)),
                "reason": reason,
            })

    return results


def parse_subscribe(text: str) -> Optional[str]:
    """解析订阅指令，返回 Medium username（不含 @）"""
    pattern = r"(?:订阅|subscribe|关注)\s+@?([\w\-\.]+)"
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


def parse_unsubscribe(text: str) -> Optional[str]:
    """解析取消订阅指令，返回 Medium username"""
    pattern = r"(?:取消|unsubscribe)\s+@?([\w\-\.]+)"
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


def extract_text_from_message(msg: dict) -> str:
    """从飞书消息结构中提取纯文本内容"""
    try:
        body = json.loads(msg.get("body", {}).get("content", "{}"))
        if msg.get("msg_type") == "text":
            return body.get("text", "").strip()
    except Exception:
        pass
    return ""


# ── 主入口：读取并分类所有新指令 ──────────────────────────────────────────────

def collect_commands(since_hours: int = 6) -> dict:
    """
    读取飞书消息，返回解析后的指令集合。

    返回结构：
    {
        "scores": {1: 5, 2: 3, ...},        # 文章编号 → 分数（向后兼容）
        "score_reasons": [                   # 带原因的完整打分列表（新增）
            {"article_num": 1, "score": 5, "reason": "喜欢实战案例"},
            {"article_num": 2, "score": 3, "reason": None},
        ],
        "subscribe": ["username1", ...],
        "unsubscribe": ["username2", ...],
    }
    """
    result = {
        "scores": {},
        "score_reasons": [],
        "subscribe": [],
        "unsubscribe": [],
    }

    token = _get_tenant_token()
    if not token:
        return result

    user_open_id = os.getenv("FEISHU_USER_OPEN_ID", "").strip()
    if not user_open_id:
        print("⚠️ 缺少 FEISHU_USER_OPEN_ID，跳过消息读取")
        return result

    bot_open_id = _get_bot_open_id(token)
    chat_id = os.getenv("FEISHU_CHAT_ID", "").strip() or user_open_id
    messages = fetch_recent_messages(token, chat_id, since_hours=since_hours)

    for msg in messages:
        sender_id = msg.get("sender", {}).get("id", "")
        if bot_open_id and sender_id == bot_open_id:
            continue

        text = extract_text_from_message(msg)
        if not text:
            continue

        # 用新函数解析（同时兼容旧格式）
        score_items = parse_scores_with_reasons(text)
        if score_items:
            for item in score_items:
                num = item["article_num"]
                result["scores"][num] = item["score"]
                # score_reasons 同编号去重，后来的覆盖前面的
                existing = next(
                    (x for x in result["score_reasons"] if x["article_num"] == num), None
                )
                if existing:
                    existing["score"] = item["score"]
                    existing["reason"] = item["reason"]
                else:
                    result["score_reasons"].append(item)

        sub = parse_subscribe(text)
        if sub and sub not in result["subscribe"]:
            result["subscribe"].append(sub)

        unsub = parse_unsubscribe(text)
        if unsub and unsub not in result["unsubscribe"]:
            result["unsubscribe"].append(unsub)

    reasons_count = sum(1 for x in result["score_reasons"] if x.get("reason"))
    total = len(result["scores"]) + len(result["subscribe"]) + len(result["unsubscribe"])
    print(
        f"📬 解析到 {total} 条指令：{len(result['scores'])} 个打分"
        f"（其中 {reasons_count} 条带原因），"
        f"{len(result['subscribe'])} 个订阅，{len(result['unsubscribe'])} 个取消订阅"
    )
    return result
