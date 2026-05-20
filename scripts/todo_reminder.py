"""Todo reminder script for cron job integration.

Checks todos from solo/wolo stores and outputs reminders for overdue or
approaching-deadline items. Designed to be run as a cron job command.

Usage:
    python -m scripts.todo_reminder [--app solo|wolo|both] [--workspace PATH]

Exit code 0 = reminders generated or no todos.
Output goes to stdout for consumption by cron scheduler notifications.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _check_todos(app: str, workspace: str | None = None) -> str:
    """Check todos for a given app and return a formatted reminder string."""
    lines: list[str] = []
    today = datetime.now().strftime("%Y-%m-%d")
    in_3_days = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")

    if app in ("solo", "both"):
        try:
            from solo.store import SoloStore

            store = SoloStore(workspace)
            todos = store.list_todos(status="pending")
            overdue = [t for t in todos if t.due_date and t.due_date < today]
            due_today = [t for t in todos if t.due_date == today]
            due_soon = [t for t in todos if t.due_date and today < t.due_date <= in_3_days]

            if overdue or due_today or due_soon:
                lines.append("📋 **Solo 待办提醒**")
                if overdue:
                    lines.append(f"\n⚠️ 已逾期 ({len(overdue)} 项)：")
                    for t in overdue:
                        lines.append(f"  - [{t.priority}] {t.title} (截止: {t.due_date})")
                if due_today:
                    lines.append(f"\n🔔 今日到期 ({len(due_today)} 项)：")
                    for t in due_today:
                        lines.append(f"  - [{t.priority}] {t.title}")
                if due_soon:
                    lines.append(f"\n📅 即将到期 ({len(due_soon)} 项)：")
                    for t in due_soon:
                        lines.append(f"  - [{t.priority}] {t.title} (截止: {t.due_date})")

                # Also mention total pending without due date
                no_date = [t for t in todos if not t.due_date]
                if no_date:
                    lines.append(f"\n还有 {len(no_date)} 项待办未设截止日期。")
        except Exception as exc:
            lines.append(f"Solo 待办检查失败: {exc}")

    if app in ("wolo", "both"):
        try:
            from wolo.store import WoloStore

            store = WoloStore(workspace)
            todos = store.list_todos(status="pending")
            overdue = [t for t in todos if t.due_date and t.due_date < today]
            due_today = [t for t in todos if t.due_date == today]
            due_soon = [t for t in todos if t.due_date and today < t.due_date <= in_3_days]

            if overdue or due_today or due_soon:
                if lines:
                    lines.append("")
                lines.append("📋 **Wolo 工作待办提醒**")
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
        except Exception as exc:
            lines.append(f"Wolo 待办检查失败: {exc}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check todo reminders")
    parser.add_argument("--app", choices=["solo", "wolo", "both"], default="both")
    parser.add_argument("--workspace", type=str, default=None)
    args = parser.parse_args()

    output = _check_todos(args.app, args.workspace)
    if output:
        print(output)
    else:
        print("✅ 没有需要提醒的待办事项。")


if __name__ == "__main__":
    # Add project root to path for imports
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
