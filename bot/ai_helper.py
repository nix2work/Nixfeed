from __future__ import annotations

import os
import json
import time
from typing import List, Optional, Dict
import requests


def call_claude_api(prompt: str, max_retries: int = 2) -> Optional[str]:
    """调用 Claude API（主力，第三方兼容接口）"""
    api_key = os.getenv("CLAUDE_API_KEY", "").strip()
    if not api_key:
        return None

    base_url = os.getenv("CLAUDE_BASE_URL", "https://api.anthropic.com").rstrip("/")
    url = f"{base_url}/v1/messages"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": "claude-3-haiku-20240307",
        "max_tokens": 200,
        "messages": [{"role": "user", "content": prompt}],
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if "content" in data and len(data["content"]) > 0:
                    return data["content"][0]["text"].strip()
            if attempt < max_retries - 1:
                time.sleep(1)
        except Exception as e:
            print(f"⚠️ Claude API 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    return None


def call_minimax_api(prompt: str, max_retries: int = 2) -> Optional[str]:
    """调用 MiniMax API（备用）"""
    api_key = os.getenv("MINIMAX_API_KEY", "").strip()
    group_id = os.getenv("MINIMAX_GROUP_ID", "").strip()
    if not api_key or not group_id:
        return None

    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": "abab6.5s-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.7,
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                data = response.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
            if attempt < max_retries - 1:
                time.sleep(1)
        except Exception as e:
            print(f"⚠️ MiniMax API 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    return None


def generate_summary_and_translate(
    title: str,
    description: str,
    category: str,
    source_name: str
) -> Dict[str, str]:
    """生成文章中文摘要，Claude 主力，MiniMax 备用"""
    keyword_hints = {
        "ai": "重点关注 'vibe coding'、'AI编程'、'代码生成' 等关键词",
        "ux": "重点关注 UX 专家名字（如 John Maeda, Don Norman, Jakob Nielsen）、'用户体验'、'设计系统' 等",
        "product": "重点关注 '产品管理'、'增长策略'、'用户留存' 等",
    }
    hint = keyword_hints.get(category, "")

    prompt = f"""请完成以下任务：

1. 将标题翻译成中文
2. 用一句话（不超过30字）总结文章核心内容，{hint}

文章信息：
标题：{title}
来源：{source_name}
描述：{description if description else "无"}

请严格按照以下JSON格式输出，不要有任何额外内容：
{{"title_cn": "中文标题", "summary_cn": "一句话摘要"}}"""

    # 主力：Claude
    result = call_claude_api(prompt)

    # 备用：MiniMax
    if not result:
        print(f"  → Claude 失败，切换到 MiniMax API")
        result = call_minimax_api(prompt)

    if result:
        try:
            start = result.find("{")
            end = result.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(result[start:end])
                return {
                    "title_cn": data.get("title_cn", title),
                    "summary_cn": data.get("summary_cn", ""),
                }
        except Exception as e:
            print(f"  ⚠️ 解析 AI 响应失败: {e}")
            print(f"  原始响应: {result[:200]}")

    print(f"  ⚠️ AI 生成失败，使用原标题")
    return {"title_cn": title, "summary_cn": ""}


def batch_generate_summaries(items: List[Dict]) -> List[Dict]:
    """批量生成摘要和翻译"""
    print(f"🤖 开始生成摘要和翻译（共 {len(items)} 条）...")
    enhanced_items = []
    for i, item in enumerate(items, 1):
        print(f"  [{i}/{len(items)}] 处理: {item.get('title', '')[:50]}...")
        result = generate_summary_and_translate(
            title=item.get("title", ""),
            description=item.get("description", ""),
            category=item.get("category", ""),
            source_name=item.get("source_name", ""),
        )
        enhanced_item = dict(item)
        enhanced_item["title_cn"] = result["title_cn"]
        enhanced_item["summary_cn"] = result["summary_cn"]
        enhanced_items.append(enhanced_item)
        if i < len(items):
            time.sleep(0.5)
    print(f"✓ 摘要生成完成")
    return enhanced_items
