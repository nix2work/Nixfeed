from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from typing import Dict, List

import requests

from .dedupe import canonicalize_url


def _utc_date_str() -> str:
    """返回北京时间日期"""
    # GitHub Actions 运行在 UTC，需要加 8 小时得到北京时间
    from datetime import timedelta
    beijing_time = datetime.now() + timedelta(hours=8)
    return beijing_time.strftime("%Y-%m-%d")


def build_post_payload(items: List[Dict]) -> Dict:
    """
    构建飞书消息 payload（中文版本）
    
    Args:
        items: 文章列表，每个包含：
            - title_cn: 中文标题
            - summary_cn: 中文摘要
            - url: 文章链接
            - source_name: 来源名称
            - category: 分类
    
    Returns:
        飞书 post 格式的消息
    """
    keyword = os.getenv("FEISHU_KEYWORD", "").strip()
    title = f"AI×UX Daily Digest · {_utc_date_str()}"
    if keyword:
        title = f"{title} · {keyword}"

    content: List[List[Dict]] = []
    
    # 标题行
    content.append([{"tag": "text", "text": "📰 今日精选 (AI × UX)\n"}])
    content.append([{"tag": "text", "text": "\n"}])  # 空行

    for item in items:
        url = canonicalize_url(item.get("url", ""))
        title_cn = item.get("title_cn", item.get("title", ""))
        summary_cn = item.get("summary_cn", "")
        source_name = item.get("source_name", "")
        category = item.get("category", "").upper()
        idx = item.get("index", "")          # 打分编号

        # 标题行：编号 + 分类 + 标题链接 + 来源
        title_line = [
            {"tag": "text", "text": f"{idx}. [{category}] "},
            {"tag": "a", "text": title_cn, "href": url},
            {"tag": "text", "text": f" — {source_name}"},
        ]
        content.append(title_line)

        # 摘要行（如果有）
        if summary_cn:
            content.append([{"tag": "text", "text": f"   {summary_cn}"}])

        # 空行
        content.append([{"tag": "text", "text": "\n"}])

    # 打分提示
    content.append([{"tag": "text", "text": "─────────────────────"}])
    content.append([{"tag": "text", "text": "💬 回复打分：「1:5 2:3 3:4」（1-5分）"}])
    content.append([{"tag": "text", "text": "➕ 订阅作者：「订阅 @username」"}])
    content.append([{"tag": "text", "text": "➖ 取消订阅：「取消 @username」"}])

    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": content,
                }
            }
        },
    }


def sign_if_needed(headers: Dict[str, str], payload: Dict) -> Dict:
    """飞书签名校验"""
    secret = os.getenv("FEISHU_SECRET", "").strip()
    if not secret:
        return payload

    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    h = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(h).decode("utf-8")

    # Feishu v2 webhook supports sign + timestamp at top-level
    out = dict(payload)
    out["timestamp"] = timestamp
    out["sign"] = sign
    return out


def send_webhook(payload: Dict, webhook_url: str, timeout: int = 20) -> Dict:
    """发送飞书 webhook"""
    headers = {"Content-Type": "application/json; charset=utf-8"}
    final_payload = sign_if_needed(headers, payload)
    resp = requests.post(
        webhook_url,
        headers=headers,
        data=json.dumps(final_payload, ensure_ascii=False).encode("utf-8"),
        timeout=timeout
    )
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}
