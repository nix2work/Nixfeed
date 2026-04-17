"""
poll.py
轮询飞书消息，处理打分和订阅指令，更新 state/ 目录并 commit 回 repo。
由 GitHub Actions 每 6 小时触发一次。

迭代6新增：打分若带原因（格式：1:5 | 原因），自动提炼偏好信号写入
preference_profile.json，下次推送时影响文章排名。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot.feishu_reader import collect_commands
from bot.author_manager import process_commands


def git_commit_if_changed() -> None:
    """如果 state/ 有变更，自动 commit"""
    result = subprocess.run(
        ["git", "status", "--porcelain", "state/"],
        capture_output=True, text=True
    )
    if not result.stdout.strip():
        print("📭 state/ 无变更，跳过 commit")
        return

    subprocess.run(["git", "config", "user.name", "autobot"], check=True)
    subprocess.run(["git", "config", "user.email", "autobot@noreply"], check=True)
    subprocess.run(["git", "add", "state/"], check=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: update scores, curated list and preference profile [skip ci]"],
        check=True
    )
    subprocess.run(["git", "push"], check=True)
    print("✅ state/ 变更已 commit & push")


def main() -> int:
    print("🔄 开始轮询飞书消息...\n")

    # 1. 读取并解析最近 6 小时的飞书消息
    #    collect_commands 现在同时返回 score_reasons（带原因的打分列表）
    commands = collect_commands(since_hours=6)

    # 2. 处理所有指令
    #    process_commands 内部会自动调用 update_preference_from_scores
    if any([commands["scores"], commands["subscribe"], commands["unsubscribe"]]):
        process_commands(commands)
    else:
        print("📭 无新指令")
        return 0

    # 3. commit 变更（包括 preference_profile.json）
    git_commit_if_changed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
