from __future__ import annotations

"""
feishu_reader.py
读取飞书私聊消息，解析打分指令和订阅指令。

支持的指令格式：
  打分：「1:5 2:3 3:1」或「1:5, 2:3, 3:1」
  订阅作者：「订阅 @username」或「subscribe @username」
  取消订阅：「取消 @username」或「unsubscribe @username」
"""

import os
import re
import time
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
    """获取机器人自身的 open_id（用于确认消息方向）"""
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


def fetch_recent_messages(token: str, chat_id: str, since_hours: int = 2) -> list[dict]:
    """
    拉取指定会话最近 since_hours 小时内的消息。
    chat_id 填用户的 open_id（私聊场景）。
    """
    since_ts = int((datetime.now(tz=timezone.utc) - timedelta(hours=since_hours)).timestamp())

    resp = requests.get(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "container_id_type": "chat",   # 群聊
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
    解析打分指令，返回 {文章编号: 分数}。

    支持格式：
      「1:5 2:3 3:1」
      「1:5, 2:3, 3:1」
      「1-5 2-3 3-1」
    分数范围 1–5，编号任意正整数。
    """
    # 兼容冒号和连字符分隔，逗号/空格分组
    pattern = r"\b(\d+)\s*[:：\-]\s*([1-5])\b"
    matches = re.findall(pattern, text)
    return {int(idx): int(score) for idx, score in matches}


def parse_subscribe(text: str) -> Optional[str]:
    """
    解析订阅指令，返回 Medium username（不含 @）。

    支持格式：
      「订阅 @username」
      「subscribe @username」
      「关注 @username」
    """
    pattern = r"(?:订阅|subscribe|关注)\s+@?([\w\-\.]+)"
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


def parse_unsubscribe(text: str) -> Optional[str]:
    """
    解析取消订阅指令，返回 Medium username。

    支持格式：
      「取消 @username」
      「unsubscribe @username」
    """
    pattern = r"(?:取消|unsubscribe)\s+@?([\w\-\.]+)"
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


def extract_text_from_message(msg: dict) -> str:
    """从飞书消息结构中提取纯文本内容"""
    try:
        body = json.loads(msg.get("body", {}).get("content", "{}"))
        # text 类型消息
        if msg.get("msg_type") == "text":
            return body.get("text", "").strip()
    except Exception:
        pass
    return ""


# ── 主入口：读取并分类所有新指令 ──────────────────────────────────────────────

def collect_commands(since_hours: int = 2) -> dict:
    """
    读取飞书消息，返回解析后的指令集合。

    返回结构：
    {
        "scores": {1: 5, 2: 3, ...},       # 文章编号 → 分数
        "subscribe": ["username1", ...],     # 新增订阅
        "unsubscribe": ["username2", ...],   # 取消订阅
    }
    """
    result = {"scores": {}, "subscribe": [], "unsubscribe": []}

    token = _get_tenant_token()
    if not token:
        return result

    # 从环境变量读取目标用户 open_id（即你自己）
    user_open_id = os.getenv("FEISHU_USER_OPEN_ID", "").strip()
    if not user_open_id:
        print("⚠️ 缺少 FEISHU_USER_OPEN_ID，跳过消息读取")
        return result

    bot_open_id = _get_bot_open_id(token)

    # 优先用群聊 chat_id，没有则回退到私聊 open_id
    chat_id = os.getenv("FEISHU_CHAT_ID", "").strip() or user_open_id
    messages = fetch_recent_messages(token, chat_id, since_hours=since_hours)

    for msg in messages:
        # 只处理用户发给机器人的消息（排除机器人自己发的）
        sender_id = msg.get("sender", {}).get("id", "")
        if bot_open_id and sender_id == bot_open_id:
            continue

        text = extract_text_from_message(msg)
        if not text:
            continue

        # 解析各类指令
        scores = parse_scores(text)
        if scores:
            result["scores"].update(scores)

        sub = parse_subscribe(text)
        if sub and sub not in result["subscribe"]:
            result["subscribe"].append(sub)

        unsub = parse_unsubscribe(text)
        if unsub and unsub not in result["unsubscribe"]:
            result["unsubscribe"].append(unsub)

    total = len(result["scores"]) + len(result["subscribe"]) + len(result["unsubscribe"])
    print(f"📬 解析到 {total} 条指令：{len(result['scores'])} 个打分，"
          f"{len(result['subscribe'])} 个订阅，{len(result['unsubscribe'])} 个取消订阅")
    return result
