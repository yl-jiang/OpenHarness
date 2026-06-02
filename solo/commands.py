"""Command parsing and formatting for solo messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from solo.core.models import ProcessResult

SoloAction = Literal["record", "process", "status", "llm_usage", "view", "report", "backfill", "help"]


@dataclass(frozen=True)
class SoloCommand:
    action: SoloAction
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


def extract_solo_content(text: str) -> str | None:
    stripped = text.strip()
    if stripped == "/solo":
        return ""
    if stripped.startswith("/solo "):
        content = stripped.removeprefix("/solo").strip()
        if content.startswith("record "):
            return content.removeprefix("record ").strip()
        return content
    return None


def parse_solo_command(text: str, *, default_record: bool = False) -> SoloCommand | None:
    content = extract_solo_content(text)
    if content is None:
        return SoloCommand(action="record", content=text.strip()) if default_record else None
    if content == "":
        return SoloCommand(action="help")
    parts = content.split(maxsplit=1)
    first = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if first in {"help", "-h", "--help", "帮助"}:
        return SoloCommand(action="help")
    if first in {"process", "整理"}:
        return SoloCommand(action="process", backfill_missing_yesterday=True)
    if first in {"status", "状态"}:
        return SoloCommand(action="status")
    if first in {"llm-usage", "llm_usage", "llm", "models", "模型", "模型调用"}:
        return SoloCommand(action="llm_usage")
    if first in {"view", "list", "recent", "查看", "最近"}:
        return SoloCommand(action="view", limit=_parse_int(rest, default=10))
    if first in {"report", "周报", "月报", "年报"}:
        return SoloCommand(action="report", report_type=_parse_report_type(first, rest))
    if first in {"backfill", "补录"}:
        date, body = parse_backfill_argument(rest)
        return SoloCommand(action="backfill", content=body, backfill_date=date)
    return SoloCommand(action="record", content=content)


def parse_backfill_argument(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return (_yesterday(), "")
    parts = stripped.split(maxsplit=1)
    if _looks_like_date(parts[0]):
        return (parts[0], parts[1].strip() if len(parts) > 1 else "")
    return (_yesterday(), stripped)


def solo_help_text() -> str:
    return (
        "solo 用法：\n"
        "- 直接发送日常记录：自动入库并由模型整理\n"
        "- /solo process：整理待处理记录\n"
        "- /solo view [数量]：查看最近记录\n"
        "- /solo report weekly|monthly|yearly：生成报告\n"
        "- /solo status：查看状态\n"
        "- /solo llm-usage：查看模型调用统计\n"
        "- /solo backfill [YYYY-MM-DD] 内容：补录"
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


def format_solo_llm_usage(summary: dict[str, Any]) -> str:
    total = int(summary.get("total_calls") or 0)
    models = summary.get("models")
    if total <= 0 or not isinstance(models, list):
        return "solo 还没有 LLM 调用记录。"

    lines = [f"solo LLM 调用累计 {total} 次"]
    for item in models:
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or "").strip()
        count = int(item.get("count") or 0)
        if model and count > 0:
            lines.append(f"- {model}: {count} 次")
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
