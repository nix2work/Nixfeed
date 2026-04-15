from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

from .dedupe import fingerprint, load_seen, save_seen
from .fetcher import fetch_items, rank_and_filter
from .feishu import build_post_payload, send_webhook
from .sources import get_sources
from .ai_helper import batch_generate_summaries


def ensure_balanced_items(all_items, seen_fingerprints, category_limits):
    """
    严格保证分类平衡的筛选逻辑

    策略：
    1. 每个分类独立筛选，确保达到目标数量
    2. 优先使用 7 天内内容
    3. 不够则扩展到 30 天、90 天
    4. 每个分类必须达到目标数量
    """
    from .fetcher import _calculate_score
    from .author_manager import load_curated, load_blocked

    # 加载优质/屏蔽作者集合（统一转小写方便比对）
    curated_data = load_curated()
    curated = {a["username"].lower().strip() for a in curated_data.get("authors", [])}
    blocked = {a.lower().strip() for a in load_blocked()}
    print(f"  📋 curated 作者: {len(curated)} 个，blocked 作者: {len(blocked)} 个")

    result = []
    seen_fps = set(seen_fingerprints)

    print(f"\n🔍 分类独立筛选（严格平衡）:")

    for category, target_count in category_limits.items():
        print(f"\n  [{category.upper()}] 目标: {target_count} 条")

        category_items = [it for it in all_items if it.category == category]
        print(f"    → 该分类共 {len(category_items)} 条")

        scored_items = []
        for item in category_items:
            score = _calculate_score(item, max_age_days=90, curated=curated, blocked=blocked)
            scored_items.append((score, item.published_at, item))

        scored_items.sort(key=lambda x: (x[0], x[1]), reverse=True)

        category_result = []

        # 阶段 1: 7 天内
        print(f"    阶段1: 从 7 天内筛选...")
        cutoff_7d = datetime.now(tz=timezone.utc) - timedelta(days=7)
        for score, pub_time, item in scored_items:
            if item.published_at < cutoff_7d:
                continue
            fp = fingerprint(item)
            if fp in seen_fps:
                continue
            category_result.append(item)
            seen_fps.add(fp)
            if len(category_result) >= target_count:
                break
        print(f"    → 找到 {len(category_result)} 条")

        # 阶段 2: 30 天内
        if len(category_result) < target_count:
            print(f"    阶段2: 扩展到 30 天内...")
            cutoff_30d = datetime.now(tz=timezone.utc) - timedelta(days=30)
            for score, pub_time, item in scored_items:
                if item.published_at < cutoff_30d:
                    continue
                fp = fingerprint(item)
                if fp in seen_fps:
                    continue
                category_result.append(item)
                seen_fps.add(fp)
                if len(category_result) >= target_count:
                    break
            print(f"    → 找到 {len(category_result)} 条")

        # 阶段 3: 90 天内
        if len(category_result) < target_count:
            print(f"    阶段3: 扩展到 90 天内...")
            cutoff_90d = datetime.now(tz=timezone.utc) - timedelta(days=90)
            for score, pub_time, item in scored_items:
                if item.published_at < cutoff_90d:
                    continue
                fp = fingerprint(item)
                if fp in seen_fps:
                    continue
                category_result.append(item)
                seen_fps.add(fp)
                if len(category_result) >= target_count:
                    break
            print(f"    → 找到 {len(category_result)} 条")

        # 阶段 4: 所有时间
        if len(category_result) < target_count:
            print(f"    阶段4: 扩展到所有时间...")
            for score, pub_time, item in scored_items:
                fp = fingerprint(item)
                if fp in seen_fps:
                    continue
                category_result.append(item)
                seen_fps.add(fp)
                if len(category_result) >= target_count:
                    break
            print(f"    → 找到 {len(category_result)} 条")

        if len(category_result) < target_count:
            print(f"    ⚠️ 警告: 只找到 {len(category_result)} 条（目标 {target_count} 条）")
        else:
            print(f"    ✅ 成功: 已找到 {target_count} 条")

        result.extend(category_result[:target_count])

    return result


def main() -> int:
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook:
        raise SystemExit("❌ Missing FEISHU_WEBHOOK_URL")

    category_limits = {"ai": 4, "ux": 4}
    target_count = sum(category_limits.values())

    sources = get_sources()
    print(f"📡 抓取资讯源（共 {len(sources)} 个）...")
    all_items = fetch_items(sources)
    print(f"✓ 抓取到 {len(all_items)} 条资讯")

    ai_total = len([i for i in all_items if i.category == "ai"])
    ux_total = len([i for i in all_items if i.category == "ux"])
    print(f"  - AI: {ai_total} 条")
    print(f"  - UX: {ux_total} 条")

    state_path = Path("state/seen.json")
    seen = load_seen(state_path)
    print(f"\n📚 历史记录: {len(seen)} 条已推送")

    selected_items = ensure_balanced_items(all_items, seen, category_limits)

    ai_count = len([i for i in selected_items if i.category == "ai"])
    ux_count = len([i for i in selected_items if i.category == "ux"])

    print(f"\n✓ 最终选择 {len(selected_items)} 条（已去重）")
    print(f"  - AI: {ai_count} 条（目标 {category_limits['ai']} 条）")
    print(f"  - UX: {ux_count} 条（目标 {category_limits['ux']} 条）")

    if len(selected_items) < target_count:
        print(f"\n⚠️ 警告: 只找到 {len(selected_items)} 条，未达到目标 {target_count} 条")
        if ai_count < category_limits['ai']:
            print(f"  - AI 不足 {category_limits['ai'] - ai_count} 条")
        if ux_count < category_limits['ux']:
            print(f"  - UX 不足 {category_limits['ux'] - ux_count} 条")
        if len(selected_items) == 0:
            print("⚠️ 没有新内容可推送")
            return 0

    items_dict = []
    for i, item in enumerate(selected_items, start=1):
        items_dict.append({
            "index": i,
            "title": item.title,
            "description": item.description,
            "url": item.url,
            "source_name": item.source_name,
            "category": item.category,
            "author": item.author,
        })

    from .author_manager import register_articles
    from datetime import date
    register_articles(items_dict, date_str=date.today().isoformat())

    enhanced_items = batch_generate_summaries(items_dict)

    print(f"\n📤 准备推送到飞书...")
    payload = build_post_payload(enhanced_items)
    result = send_webhook(payload, webhook)

    for item in selected_items:
        fp = fingerprint(item)
        seen.add(fp)

    save_seen(state_path, seen)
    print(f"✓ 状态已保存（新增 {len(selected_items)} 条记录）")

    if isinstance(result, dict) and str(result.get("code", "0")) not in ("0", 0):
        print("❌ 飞书 webhook 响应:", result)
        return 2

    print("✅ 推送成功!")

    print(f"\n📊 统计信息:")
    print(f"  - 本次推送: {len(selected_items)} 条（AI:{ai_count}, UX:{ux_count}）")
    print(f"  - 历史总计: {len(seen)} 条")
    if len(all_items) > 0:
        print(f"  - 去重率: {(1 - len(selected_items) / len(all_items)) * 100:.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
