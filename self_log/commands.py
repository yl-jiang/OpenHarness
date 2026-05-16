"""Command parsing and formatting for self-log messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from self_log.models import ProcessResult

SelfLogAction = Literal["record", "process", "status", "view", "report", "backfill", "help"]


@dataclass(frozen=True)
class SelfLogCommand:
    action: SelfLogAction
    content: str = ""
    report_type: str = "weekly"
    limit: int = 10
    backfill_missing_yesterday: bool = False
    backfill_date: str | None = None

    @property
    def argument(self) -> str:
        if self.action == "report":
            return self.report_type
        if self.action == "view":
            return str(self.limit)
        if self.action == "backfill" and self.backfill_date:
            return f"{self.backfill_date} {self.content}".strip()
        return self.content


def extract_self_log_content(text: str) -> str | None:
    stripped = text.strip()
    if stripped == "/self-log":
        return ""
    if stripped.startswith("/self-log "):
        content = stripped.removeprefix("/self-log").strip()
        if content.startswith("record "):
            return content.removeprefix("record ").strip()
        return content
    return None


def parse_self_log_command(text: str, *, default_record: bool = False) -> SelfLogCommand | None:
    content = extract_self_log_content(text)
    if content is None:
        return SelfLogCommand(action="record", content=text.strip()) if default_record else None
    if content == "":
        return SelfLogCommand(action="help")
    parts = content.split(maxsplit=1)
    first = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if first in {"help", "-h", "--help", "帮助"}:
        return SelfLogCommand(action="help")
    if first in {"process", "整理"}:
        return SelfLogCommand(action="process", backfill_missing_yesterday=True)
    if first in {"status", "状态"}:
        return SelfLogCommand(action="status")
    if first in {"view", "list", "recent", "查看", "最近"}:
        return SelfLogCommand(action="view", limit=_parse_int(rest, default=10))
    if first in {"report", "周报", "月报", "年报"}:
        return SelfLogCommand(action="report", report_type=_parse_report_type(first, rest))
    if first in {"backfill", "补录"}:
        date, body = parse_backfill_argument(rest)
        return SelfLogCommand(action="backfill", content=body, backfill_date=date)
    return SelfLogCommand(action="record", content=content)


def parse_backfill_argument(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return (_yesterday(), "")
    parts = stripped.split(maxsplit=1)
    if _looks_like_date(parts[0]):
        return (parts[0], parts[1].strip() if len(parts) > 1 else "")
    return (_yesterday(), stripped)


def self_log_help_text() -> str:
    return (
        "self-log 用法：\n"
        "- 直接发送日常记录：自动入库并由模型整理\n"
        "- /self-log process：整理待处理记录\n"
        "- /self-log view [数量]：查看最近记录\n"
        "- /self-log report weekly|monthly|yearly：生成报告\n"
        "- /self-log status：查看状态\n"
        "- /self-log backfill [YYYY-MM-DD] 内容：补录"
    )


def format_process_result(result: ProcessResult) -> str:
    lines = [f"已整理 {result.auto_processed} 条，待确认 {result.pending_confirmations} 条。"]
    for item in (
        result.pending_reminder,
        result.missing_day_reminder,
        result.backfill_prompt,
        result.daily_question,
    ):
        if item:
            lines.append(item)
    return "\n".join(lines)


def _parse_report_type(first: str, rest: str) -> str:
    if first == "月报":
        return "monthly"
    if first == "年报":
        return "yearly"
    lowered = rest.lower()
    if "month" in lowered or "monthly" in lowered:
        return "monthly"
    if "year" in lowered or "yearly" in lowered:
        return "yearly"
    return "weekly"


def _parse_int(text: str, *, default: int) -> int:
    try:
        return max(1, min(100, int(text.strip()))) if text.strip() else default
    except ValueError:
        return default


def _looks_like_date(text: str) -> bool:
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _yesterday() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
