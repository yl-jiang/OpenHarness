"""Todo reminder entry point for the wolo cron job.

Checks wolo todos and prints reminders for overdue or approaching-deadline items.
Designed to be run as a cron job command by the wolo scheduler.

Usage:
    python -m wolo.todo_reminder [--workspace PATH]

Exit code 0 always; output goes to stdout for cron scheduler notification.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


def check_todos(workspace: str | None = None) -> str:
    from wolo.core.store import WoloStore

    today = datetime.now().strftime("%Y-%m-%d")
    in_3_days = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")

    store = WoloStore(workspace)
    todos = store.list_todos(status="pending")
    overdue = [t for t in todos if t.due_date and t.due_date < today]
    due_today = [t for t in todos if t.due_date == today]
    due_soon = [t for t in todos if t.due_date and today < t.due_date <= in_3_days]

    if not (overdue or due_today or due_soon):
        return "✅ 没有需要提醒的工作待办。"

    lines = ["📋 **Wolo 工作待办提醒**"]
    if overdue:
        lines.append(f"\n⚠️ 已逾期 ({len(overdue)} 项)：")
        for t in overdue:
            lines.append(f"  - [{t.priority}] {t.project} {t.title} (截止: {t.due_date})")
    if due_today:
        lines.append(f"\n🔔 今日到期 ({len(due_today)} 项)：")
        for t in due_today:
            lines.append(f"  - [{t.priority}] {t.project} {t.title}")
    if due_soon:
        lines.append(f"\n📅 即将到期 ({len(due_soon)} 项)：")
        for t in due_soon:
            lines.append(f"  - [{t.priority}] {t.project} {t.title} (截止: {t.due_date})")
    no_date = [t for t in todos if not t.due_date]
    if no_date:
        lines.append(f"\n还有 {len(no_date)} 项工作待办未设截止日期。")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wolo todo reminder")
    parser.add_argument("--workspace", type=str, default=None)
    args = parser.parse_args()
    print(check_todos(args.workspace))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
