from __future__ import annotations

import os
import json
import time
from typing import List, Optional, Dict
import requests


def call_gemini_api(prompt: str, max_retries: int = 2) -> Optional[str]:
    """
    调用 Gemini API
    
    Args:
        prompt: 提示词
        max_retries: 最大重试次数
    
    Returns:
        生成的文本，失败返回 None
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent"
    
    headers = {
        "Content-Type": "application/json",
    }
    
    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 200,
        }
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{url}?key={api_key}",
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "candidates" in data and len(data["candidates"]) > 0:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return text.strip()
            
            # 如果失败，等待后重试
            if attempt < max_retries - 1:
                time.sleep(1)
                
        except Exception as e:
            print(f"⚠️ Gemini API 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    
    return None


def call_bigmodel_api(prompt: str, max_retries: int = 2) -> Optional[str]:
    """
    调用智谱 BigModel API (GLM-4-Flash)
    
    Args:
        prompt: 提示词
        max_retries: 最大重试次数
    
    Returns:
        生成的文本，失败返回 None
    """
    api_key = os.getenv("BIGMODEL_API_KEY", "").strip()
    if not api_key:
        return None
    
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    payload = {
        "model": "glm-4-flash",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.7,
        "max_tokens": 200,
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "choices" in data and len(data["choices"]) > 0:
                    text = data["choices"][0]["message"]["content"]
                    return text.strip()
            
            if attempt < max_retries - 1:
                time.sleep(1)
                
        except Exception as e:
            print(f"⚠️ BigModel API 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    
    return None


def generate_summary_and_translate(
    title: str,
    description: str,
    category: str,
    source_name: str
) -> Dict[str, str]:
    """
    生成文章摘要并翻译成中文
    
    优先使用 Gemini API，失败则使用 BigModel API
    
    Args:
        title: 文章标题（英文）
        description: 文章描述（英文，可能为空）
        category: 分类 (ai/ux/product)
        source_name: 来源名称
    
    Returns:
        {
            "title_cn": "中文标题",
            "summary_cn": "一句话中文摘要"
        }
    """
    # 构建关键词提示
    keyword_hints = {
        "ai": "重点关注 'vibe coding'、'AI编程'、'代码生成' 等关键词",
        "ux": "重点关注 UX 专家名字（如 John Maeda, Don Norman, Jakob Nielsen）、'用户体验'、'设计系统' 等",
        "product": "重点关注 '产品管理'、'增长策略'、'用户留存' 等"
    }
    
    hint = keyword_hints.get(category, "")
    
    # 构建提示词
    prompt = f"""请完成以下任务：

1. 将标题翻译成中文
2. 用一句话（不超过30字）总结文章核心内容，{hint}

文章信息：
标题：{title}
来源：{source_name}
描述：{description if description else "无"}

请严格按照以下JSON格式输出，不要有任何额外内容：
{{"title_cn": "中文标题", "summary_cn": "一句话摘要"}}"""

    # 优先尝试 Gemini
    result = call_gemini_api(prompt)
    
    # 如果 Gemini 失败，尝试 BigModel
    if not result:
        print(f"  → Gemini 失败，切换到 BigModel API")
        result = call_bigmodel_api(prompt)
    
    # 解析结果
    if result:
        try:
            # 尝试提取 JSON
            # 有时 API 会返回额外的文字，需要提取 JSON 部分
            start = result.find("{")
            end = result.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = result[start:end]
                data = json.loads(json_str)
                return {
                    "title_cn": data.get("title_cn", title),
                    "summary_cn": data.get("summary_cn", "")
                }
        except Exception as e:
            print(f"  ⚠️ 解析 AI 响应失败: {e}")
            print(f"  原始响应: {result[:200]}")
    
    # 如果都失败，返回原标题和空摘要
    print(f"  ⚠️ AI 生成失败，使用原标题")
    return {
        "title_cn": title,
        "summary_cn": ""
    }


def batch_generate_summaries(items: List[Dict]) -> List[Dict]:
    """
    批量生成摘要和翻译
    
    Args:
        items: 文章列表，每个包含 title, description, category, source_name
    
    Returns:
        增强后的文章列表，添加了 title_cn 和 summary_cn
    """
    print(f"🤖 开始生成摘要和翻译（共 {len(items)} 条）...")
    
    enhanced_items = []
    for i, item in enumerate(items, 1):
        print(f"  [{i}/{len(items)}] 处理: {item.get('title', '')[:50]}...")
        
        result = generate_summary_and_translate(
            title=item.get("title", ""),
            description=item.get("description", ""),
            category=item.get("category", ""),
            source_name=item.get("source_name", "")
        )
        
        enhanced_item = dict(item)
        enhanced_item["title_cn"] = result["title_cn"]
        enhanced_item["summary_cn"] = result["summary_cn"]
        enhanced_items.append(enhanced_item)
        
        # 避免 API 限流，每次调用后稍作延迟
        if i < len(items):
            time.sleep(0.5)
    
    print(f"✓ 摘要生成完成")
    return enhanced_items
