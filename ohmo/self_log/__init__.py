"""ohmo-native self-log storage and routing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiTextDeltaEvent,
    SupportsStreamingMessages,
)
from openharness.config import load_settings
from openharness.engine.messages import ConversationMessage, ToolUseBlock
from openharness.ui.runtime import _resolve_api_client_from_settings

from ohmo.workspace import get_self_log_dir

CONFIG_FILENAME = "config.json"
ENTRIES_FILENAME = "entries.jsonl"
RECORDS_FILENAME = "records.jsonl"
PENDING_CONFIRMATIONS_FILENAME = "pending_confirmations.jsonl"
PROFILE_UPDATES_FILENAME = "profile_updates.jsonl"
REPORTS_FILENAME = "reports.jsonl"
MISSING_DAY_REMINDER_THRESHOLD = 3
PENDING_CONFIRMATION_REMINDER_STEP = 5


@dataclass(frozen=True)
class SelfLogEntry:
    """One raw self-log entry captured by ohmo before normal model routing."""

    id: str
    content: str
    created_at: str
    channel: str
    sender_id: str
    chat_id: str
    message_id: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, line: str) -> "SelfLogEntry":
        data = json.loads(line)
        return cls(
            id=str(data["id"]),
            content=str(data["content"]),
            created_at=str(data["created_at"]),
            channel=str(data.get("channel", "local")),
            sender_id=str(data.get("sender_id", "")),
            chat_id=str(data.get("chat_id", "")),
            message_id=data.get("message_id"),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "content": self.content,
                "created_at": self.created_at,
                "channel": self.channel,
                "sender_id": self.sender_id,
                "chat_id": self.chat_id,
                "message_id": self.message_id,
                "metadata": self.metadata or {},
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class SelfLogRecord:
    """One structured self-log record produced by the self-log agent."""

    id: str
    entry_id: str
    date: str
    raw_content: str
    corrected_content: str
    summary: str
    tags: str
    emotion: str
    emotion_reason: str = ""
    related_people: str = ""
    related_places: str = ""
    source: str = "原始"
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "SelfLogRecord":
        data = json.loads(line)
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entry_id": self.entry_id,
            "date": self.date,
            "raw_content": self.raw_content,
            "corrected_content": self.corrected_content,
            "summary": self.summary,
            "tags": self.tags,
            "emotion": self.emotion,
            "emotion_reason": self.emotion_reason,
            "related_people": self.related_people,
            "related_places": self.related_places,
            "source": self.source,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class PendingConfirmation:
    """A self-log entry the agent refused to guess."""

    id: str
    entry_id: str
    raw_content: str
    clarification_reason: str
    questions: list[str]
    created_at: str

    @classmethod
    def from_json(cls, line: str) -> "PendingConfirmation":
        data = json.loads(line)
        return cls(
            id=str(data["id"]),
            entry_id=str(data["entry_id"]),
            raw_content=str(data["raw_content"]),
            clarification_reason=str(data.get("clarification_reason", "")),
            questions=list(data.get("questions") or []),
            created_at=str(data["created_at"]),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "entry_id": self.entry_id,
                "raw_content": self.raw_content,
                "clarification_reason": self.clarification_reason,
                "questions": self.questions,
                "created_at": self.created_at,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class ProfileUpdate:
    """A profile update suggested by the self-log agent."""

    id: str
    record_id: str
    category: str
    entity_type: str
    entity_name: str
    suggested_value: str
    confidence: str
    status: str = "pending"

    @classmethod
    def from_json(cls, line: str) -> "ProfileUpdate":
        data = json.loads(line)
        return cls(**data)

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "record_id": self.record_id,
                "category": self.category,
                "entity_type": self.entity_type,
                "entity_name": self.entity_name,
                "suggested_value": self.suggested_value,
                "confidence": self.confidence,
                "status": self.status,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class SelfLogReport:
    """A generated self-log report."""

    id: str
    report_type: str
    content: str
    created_at: str

    @classmethod
    def from_json(cls, line: str) -> "SelfLogReport":
        data = json.loads(line)
        return cls(**data)

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "report_type": self.report_type,
                "content": self.content,
                "created_at": self.created_at,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class ProcessResult:
    """Summary of one self-log processing run."""

    auto_processed: int
    pending_confirmations: int
    backfilled: bool = False
    backfill_date: str | None = None
    backfill_prompt: str | None = None
    consecutive_missing_days: int = 0
    pending_reminder: str | None = None
    missing_day_reminder: str | None = None


@dataclass(frozen=True)
class SelfLogCommand:
    """Parsed `/self-log` remote command."""

    action: str
    argument: str = ""


@dataclass(frozen=True)
class SelfLogDomainTool:
    """Self-log local tool exposed only to the self-log semantic agent."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Any

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(self, arguments: dict[str, Any]) -> str:
        return await self.handler(arguments)


class SelfLogStore:
    """Append-only self-log store rooted in the ohmo workspace."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self.root = get_self_log_dir(workspace)
        self.config_path = self.root / CONFIG_FILENAME
        self.entries_path = self.root / ENTRIES_FILENAME
        self.records_path = self.root / RECORDS_FILENAME
        self.pending_confirmations_path = self.root / PENDING_CONFIRMATIONS_FILENAME
        self.profile_updates_path = self.root / PROFILE_UPDATES_FILENAME
        self.reports_path = self.root / REPORTS_FILENAME

    def initialize(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(
                json.dumps({"version": 1, "enabled": True}, indent=2) + "\n",
                encoding="utf-8",
            )
        for path in (
            self.entries_path,
            self.records_path,
            self.pending_confirmations_path,
            self.profile_updates_path,
            self.reports_path,
        ):
            if not path.exists():
                path.write_text("", encoding="utf-8")
        return self.root

    def record(
        self,
        content: str,
        *,
        channel: str = "local",
        sender_id: str = "",
        chat_id: str = "",
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> SelfLogEntry:
        text = content.strip()
        if not text:
            raise ValueError("self-log content cannot be empty")
        self.initialize()
        entry = SelfLogEntry(
            id=uuid4().hex[:12],
            content=text,
            created_at=created_at or _now(),
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            message_id=message_id,
            metadata=metadata or {},
        )
        with self.entries_path.open("a", encoding="utf-8") as file:
            file.write(entry.to_json() + "\n")
        self.entries_path.chmod(0o600)
        return entry

    def list_entries(self, *, limit: int | None = None) -> list[SelfLogEntry]:
        lines = self._read_jsonl(self.entries_path)
        entries = [SelfLogEntry.from_json(line) for line in lines]
        if limit is None:
            return entries
        return entries[-limit:]

    def add_record(self, record: SelfLogRecord) -> None:
        self._append_jsonl(self.records_path, record.to_json())

    def list_records(self, *, limit: int | None = None) -> list[SelfLogRecord]:
        records = [SelfLogRecord.from_json(line) for line in self._read_jsonl(self.records_path)]
        if limit is None:
            return records
        return records[-limit:]

    def add_pending_confirmation(self, pending: PendingConfirmation) -> None:
        self._append_jsonl(self.pending_confirmations_path, pending.to_json())

    def list_pending_confirmations(self) -> list[PendingConfirmation]:
        return [
            PendingConfirmation.from_json(line)
            for line in self._read_jsonl(self.pending_confirmations_path)
        ]

    def add_profile_update(self, update: ProfileUpdate) -> None:
        self._append_jsonl(self.profile_updates_path, update.to_json())

    def list_profile_updates(self) -> list[ProfileUpdate]:
        return [ProfileUpdate.from_json(line) for line in self._read_jsonl(self.profile_updates_path)]

    def add_report(self, report: SelfLogReport) -> None:
        self._append_jsonl(self.reports_path, report.to_json())

    def list_reports(self) -> list[SelfLogReport]:
        return [SelfLogReport.from_json(line) for line in self._read_jsonl(self.reports_path)]

    def status(self) -> dict[str, object]:
        entries = self.list_entries()
        records = self.list_records()
        pending = self.list_pending_confirmations()
        return {
            "entries": len(entries),
            "records": len(records),
            "pending_confirmations": len(pending),
            "last_entry_at": entries[-1].created_at if entries else None,
            "path": str(self.root),
        }

    def dates_with_activity(self) -> set[str]:
        dates = {self._entry_date(entry) for entry in self.list_entries()}
        dates.update(record.date for record in self.list_records())
        return {item for item in dates if item}

    def has_activity_on(self, target_date: str) -> bool:
        return target_date in self.dates_with_activity()

    def reminder_state(self) -> dict[str, int]:
        data = self._read_config()
        reminders = dict(data.get("reminders") or {})
        return {
            "last_pending_count": int(reminders.get("last_pending_count") or 0),
            "last_missing_streak": int(reminders.get("last_missing_streak") or 0),
        }

    def update_reminder_state(
        self,
        *,
        pending_count: int | None = None,
        missing_streak: int | None = None,
    ) -> None:
        data = self._read_config()
        reminders = dict(data.get("reminders") or {})
        if pending_count is not None:
            reminders["last_pending_count"] = pending_count
        if missing_streak is not None:
            reminders["last_missing_streak"] = missing_streak
        data["reminders"] = reminders
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _entry_date(self, entry: SelfLogEntry) -> str:
        metadata = entry.metadata or {}
        return str(metadata.get("record_date") or entry.created_at[:10])

    def _read_config(self) -> dict[str, Any]:
        self.initialize()
        return dict(json.loads(self.config_path.read_text(encoding="utf-8")))

    def _read_jsonl(self, path: Path) -> list[str]:
        self.initialize()
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _append_jsonl(self, path: Path, line: str) -> None:
        self.initialize()
        with path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        path.chmod(0o600)


class OpenHarnessSelfLogAgent:
    """Self-log domain agent backed by OpenHarness provider/auth/client plumbing."""

    def __init__(
        self,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        self._client = api_client or _resolve_api_client_from_settings(settings)

    async def process_record(self, raw_content: str, profile_context: str) -> dict[str, Any]:
        content = await self._complete(
            system_prompt=_PROCESS_RECORD_SYSTEM_PROMPT,
            user_prompt=f"{profile_context}\n\n## 用户原始记录\n{raw_content}\n\n请整理上述记录，输出 JSON。",
        )
        return _safe_parse_json(content)

    async def generate_report(
        self,
        report_type: str,
        records: list[dict[str, Any]],
        profile_context: str,
    ) -> str:
        if report_type not in {"weekly", "monthly", "yearly"}:
            raise ValueError(f"Unknown report type: {report_type}")
        records_text = "\n".join(
            f"### {record.get('date', '')}\n"
            f"- 摘要：{record.get('summary', '')}\n"
            f"- 标签：{record.get('tags', '')}\n"
            f"- 情绪：{record.get('emotion', '')}\n"
            f"- 原文：{str(record.get('raw_content', ''))[:400]}"
            for record in records
        )
        return await self._complete(
            system_prompt=_report_system_prompt(report_type),
            user_prompt=f"{profile_context}\n\n## 记录数据\n{records_text}",
        )

    async def choose_self_log_tool(self, user_text: str, tools: list[dict[str, Any]]) -> list[ToolUseBlock]:
        request = ApiMessageRequest(
            model=self._settings.model,
            messages=[ConversationMessage.from_user_text(user_text)],
            system_prompt=_SELF_LOG_TOOL_ROUTER_PROMPT,
            max_tokens=min(self._settings.max_tokens, 2048),
            tools=tools,
        )
        async for event in self._client.stream_message(request):
            if isinstance(event, ApiMessageCompleteEvent):
                return event.message.tool_uses
        return []

    async def _complete(self, *, system_prompt: str, user_prompt: str) -> str:
        request = ApiMessageRequest(
            model=self._settings.model,
            messages=[ConversationMessage.from_user_text(user_prompt)],
            system_prompt=system_prompt,
            max_tokens=min(self._settings.max_tokens, 4096),
            tools=[],
        )
        chunks: list[str] = []
        async for event in self._client.stream_message(request):
            if isinstance(event, ApiTextDeltaEvent):
                chunks.append(event.text)
            elif isinstance(event, ApiMessageCompleteEvent):
                final_text = event.message.text.strip()
                if final_text:
                    return final_text
        return "".join(chunks).strip()


class SelfLogProcessor:
    """Process self-log entries through the self-log domain agent."""

    def __init__(self, store: SelfLogStore, agent) -> None:
        self.store = store
        self.agent = agent

    async def process_pending(
        self,
        *,
        process_date: str | date | None = None,
        backfill_content: str | None = None,
    ) -> ProcessResult:
        process_day = _coerce_date(process_date)
        backfill_date = (process_day - timedelta(days=1)).isoformat()
        backfilled = False
        if backfill_content and backfill_content.strip():
            self.store.record(
                backfill_content,
                metadata={"source": "补录", "record_date": backfill_date},
            )
            backfilled = True
        backfill_prompt = None
        if not self.store.has_activity_on(backfill_date):
            backfill_prompt = _backfill_prompt(backfill_date)
        processed_ids = {
            *(record.entry_id for record in self.store.list_records()),
            *(pending.entry_id for pending in self.store.list_pending_confirmations()),
        }
        auto_processed = 0
        pending_count = 0
        for entry in self.store.list_entries():
            if entry.id in processed_ids:
                continue
            result = await self.agent.process_record(entry.content, self.profile_context())
            if result.get("needs_clarification"):
                self.store.add_pending_confirmation(
                    PendingConfirmation(
                        id=uuid4().hex[:12],
                        entry_id=entry.id,
                        raw_content=entry.content,
                        clarification_reason=str(result.get("clarification_reason", "")),
                        questions=list(result.get("clarification_questions") or []),
                        created_at=_now(),
                    )
                )
                pending_count += 1
                continue
            record = self._build_record(entry, result)
            self.store.add_record(record)
            self._store_profile_updates(record, result)
            auto_processed += 1
        pending_total = len(self.store.list_pending_confirmations())
        pending_reminder = self._pending_reminder(pending_total)
        missing_days = self._consecutive_missing_days(process_day)
        missing_day_reminder = self._missing_day_reminder(missing_days)
        return ProcessResult(
            auto_processed=auto_processed,
            pending_confirmations=pending_count,
            backfilled=backfilled,
            backfill_date=backfill_date if backfill_prompt or backfilled else None,
            backfill_prompt=backfill_prompt,
            consecutive_missing_days=missing_days,
            pending_reminder=pending_reminder,
            missing_day_reminder=missing_day_reminder,
        )

    def empty_result(
        self,
        *,
        backfill_date: str | None = None,
        backfill_prompt: str | None = None,
        pending_reminder: str | None = None,
    ) -> ProcessResult:
        return ProcessResult(
            auto_processed=0,
            pending_confirmations=0,
            backfill_date=backfill_date,
            backfill_prompt=backfill_prompt,
            pending_reminder=pending_reminder,
        )

    async def generate_report(self, report_type: str) -> SelfLogReport:
        records = [record.to_dict() for record in self.store.list_records()]
        content = await self.agent.generate_report(report_type, records, self.profile_context())
        report = SelfLogReport(
            id=uuid4().hex[:12],
            report_type=report_type,
            content=content,
            created_at=_now(),
        )
        self.store.add_report(report)
        return report

    def profile_context(self) -> str:
        return "## 用户背景知识\n（使用 ohmo user.md 和 memory 作为长期背景，待后续细化注入）"

    def _build_record(self, entry: SelfLogEntry, result: dict[str, Any]) -> SelfLogRecord:
        return SelfLogRecord(
            id=uuid4().hex[:12],
            entry_id=entry.id,
            date=str((entry.metadata or {}).get("record_date") or entry.created_at[:10]),
            raw_content=entry.content,
            corrected_content=str(result.get("corrected_content") or entry.content),
            summary=str(result.get("summary") or ""),
            tags=str(result.get("tags") or "其他"),
            emotion=str(result.get("emotion") or "中性"),
            emotion_reason=str(result.get("emotion_reason") or ""),
            related_people=str(result.get("related_people") or ""),
            related_places=str(result.get("related_places") or ""),
            source=str((entry.metadata or {}).get("source") or "原始"),
            created_at=_now(),
        )

    def _store_profile_updates(self, record: SelfLogRecord, result: dict[str, Any]) -> None:
        for item in result.get("suggested_profile_updates") or []:
            confidence = str(item.get("confidence", "")).strip().lower()
            if confidence not in {"high", "medium"}:
                continue
            self.store.add_profile_update(
                ProfileUpdate(
                    id=uuid4().hex[:12],
                    record_id=record.id,
                    category=str(item.get("category") or ""),
                    entity_type=str(item.get("entity_type") or ""),
                    entity_name=str(item.get("entity_name") or ""),
                    suggested_value=str(item.get("suggested_value") or ""),
                    confidence=confidence,
                )
            )

    def _pending_reminder(self, pending_total: int) -> str | None:
        state = self.store.reminder_state()
        if (
            pending_total >= PENDING_CONFIRMATION_REMINDER_STEP
            and pending_total % PENDING_CONFIRMATION_REMINDER_STEP == 0
            and pending_total > state["last_pending_count"]
        ):
            self.store.update_reminder_state(pending_count=pending_total)
            return f"你有 {pending_total} 条待确认，建议运行 `/self-log process` 批量处理。"
        return None

    def _consecutive_missing_days(self, process_day: date) -> int:
        dates = [date.fromisoformat(item) for item in self.store.dates_with_activity()]
        if not dates:
            return 0
        yesterday = process_day - timedelta(days=1)
        past_dates = [item for item in dates if item <= yesterday]
        if not past_dates:
            return 0
        latest = max(past_dates)
        if latest >= yesterday:
            return 0
        return (yesterday - latest).days

    def _missing_day_reminder(self, missing_days: int) -> str | None:
        state = self.store.reminder_state()
        if (
            missing_days >= MISSING_DAY_REMINDER_THRESHOLD
            and missing_days > state["last_missing_streak"]
        ):
            self.store.update_reminder_state(missing_streak=missing_days)
            return f"已经连续 {missing_days} 天没有 self-log 记录了，可以先补一条最简短的记录。"
        return None


class SelfLogToolRegistry:
    """Self-log-only tool registry; intentionally separate from OpenHarness backend tools."""

    def __init__(self, store: SelfLogStore, *, agent_factory=OpenHarnessSelfLogAgent) -> None:
        self.store = store
        self.agent_factory = agent_factory
        self._tools = {
            "self_log_record": SelfLogDomainTool(
                name="self_log_record",
                description=(
                    "Record a clear daily self-log entry. Use only after checking that people, "
                    "relationships, events, places, dates, and domain terms are sufficiently clear."
                ),
                parameters=_object_schema(
                    {
                        "content": {"type": "string", "description": "Daily record content"},
                        "corrected_content": {"type": "string", "description": "Edited original text"},
                        "summary": {"type": "string", "description": "High-level summary"},
                        "tags": {"type": "string", "description": "Comma-separated tags"},
                        "emotion": {"type": "string", "description": "Emotion label"},
                        "emotion_reason": {"type": "string", "description": "Reason for emotion label"},
                        "related_people": {"type": "string", "description": "Related people"},
                        "related_places": {"type": "string", "description": "Related places"},
                        "needs_clarification": {
                            "type": "boolean",
                            "description": "True if any key information is unclear",
                        },
                        "clarification_question": {
                            "type": "string",
                            "description": "Question to ask before recording when unclear",
                        },
                        "unclear_fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Unclear fields such as person, relationship, place, event, date",
                        },
                    },
                    required=["content"],
                ),
                handler=self._record,
            ),
            "self_log_process": SelfLogDomainTool(
                name="self_log_process",
                description="Process pending self-log entries, detect backfill gaps, and emit reminders.",
                parameters=_object_schema({}),
                handler=self._process,
            ),
            "self_log_backfill": SelfLogDomainTool(
                name="self_log_backfill",
                description="Backfill a missed self-log date and process it through the self-log agent.",
                parameters=_object_schema(
                    {
                        "content": {"type": "string", "description": "Backfill content"},
                    },
                    required=["content"],
                ),
                handler=self._backfill,
            ),
            "self_log_clarify": SelfLogDomainTool(
                name="self_log_clarify",
                description=(
                    "Ask a concise clarification question when the user expresses intent "
                    "but has not provided enough content to safely record or backfill."
                ),
                parameters=_object_schema(
                    {
                        "reason": {"type": "string", "description": "Why clarification is needed"},
                        "question": {"type": "string", "description": "Question to ask the user"},
                    },
                    required=["question"],
                ),
                handler=self._clarify,
            ),
            "self_log_report": SelfLogDomainTool(
                name="self_log_report",
                description="Generate a weekly, monthly, or yearly self-log report.",
                parameters=_object_schema(
                    {
                        "report_type": {
                            "type": "string",
                            "enum": ["weekly", "monthly", "yearly"],
                            "description": "Report period",
                        }
                    },
                    required=["report_type"],
                ),
                handler=self._report,
            ),
            "self_log_profile_update": SelfLogDomainTool(
                name="self_log_profile_update",
                description=(
                    "Persist high-value user-related information discovered while clarifying or processing a log."
                ),
                parameters=_object_schema(
                    {
                        "category": {"type": "string", "description": "Information category"},
                        "entity_type": {"type": "string", "description": "person/place/project/relation/etc."},
                        "entity_name": {"type": "string", "description": "Entity name"},
                        "suggested_value": {"type": "string", "description": "Durable fact to remember"},
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "Confidence level",
                        },
                    },
                    required=["category", "entity_type", "entity_name", "suggested_value"],
                ),
                handler=self._profile_update,
            ),
            "self_log_view": SelfLogDomainTool(
                name="self_log_view",
                description="View recent structured self-log records.",
                parameters=_object_schema({"limit": {"type": "integer", "description": "Maximum records"}}),
                handler=self._view,
            ),
            "self_log_status": SelfLogDomainTool(
                name="self_log_status",
                description="Show self-log storage and pending status.",
                parameters=_object_schema({}),
                handler=self._status,
            ),
        }

    def list_tools(self) -> list[SelfLogDomainTool]:
        return list(self._tools.values())

    def to_api_schema(self) -> list[dict[str, Any]]:
        return [tool.to_api_schema() for tool in self.list_tools()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown self-log tool: {name}")
        return await tool.execute(arguments)

    async def _record(self, arguments: dict[str, Any]) -> str:
        if _truthy(arguments.get("needs_clarification")):
            return self._clarification_reply(arguments)
        unclear_fields = [str(item) for item in arguments.get("unclear_fields") or [] if str(item).strip()]
        if unclear_fields:
            return self._clarification_reply(arguments)
        content = str(arguments.get("content") or "").strip()
        record_date = str(arguments.get("record_date") or "").strip()
        metadata = {"record_date": record_date} if record_date else None
        entry = self.store.record(content, metadata=metadata)
        high_level = self._record_high_level_fields(arguments)
        if high_level:
            self.store.add_record(
                SelfLogRecord(
                    id=uuid4().hex[:12],
                    entry_id=entry.id,
                    date=record_date or entry.created_at[:10],
                    raw_content=entry.content,
                    corrected_content=high_level.get("corrected_content") or entry.content,
                    summary=high_level.get("summary") or "",
                    tags=high_level.get("tags") or "其他",
                    emotion=high_level.get("emotion") or "中性",
                    emotion_reason=high_level.get("emotion_reason") or "",
                    related_people=high_level.get("related_people") or "",
                    related_places=high_level.get("related_places") or "",
                    source=str((entry.metadata or {}).get("source") or "原始"),
                    created_at=_now(),
                )
            )
        target_day = _coerce_date(record_date or None)
        previous_day = (target_day - timedelta(days=1)).isoformat()
        success = "✅ 刚才的记录已经入库。"
        if self.store.has_activity_on(previous_day):
            return success
        return f"{success}\n\n{_backfill_prompt(previous_day)}"

    async def _process(self, arguments: dict[str, Any]) -> str:
        processor = SelfLogProcessor(self.store, self.agent_factory())
        result = await processor.process_pending(
            process_date=str(arguments.get("process_date") or "").strip() or None,
            backfill_content=str(arguments.get("backfill_content") or "").strip() or None,
        )
        return format_process_result(result)

    async def _backfill(self, arguments: dict[str, Any]) -> str:
        backfill_date = str(arguments.get("date") or "").strip()
        content = str(arguments.get("content") or "").strip()
        parsed_date, parsed_content = parse_backfill_argument(f"{backfill_date} {content}".strip())
        process_date = (date.fromisoformat(parsed_date) + timedelta(days=1)).isoformat()
        processor = SelfLogProcessor(self.store, self.agent_factory())
        result = await processor.process_pending(process_date=process_date, backfill_content=parsed_content)
        return format_process_result(result)

    async def _report(self, arguments: dict[str, Any]) -> str:
        report_type = str(arguments.get("report_type") or "weekly").strip()
        processor = SelfLogProcessor(self.store, self.agent_factory())
        process_result = await processor.process_pending()
        report = await processor.generate_report(report_type)
        prefix = ""
        if process_result.pending_reminder:
            prefix = process_result.pending_reminder + "\n\n"
        elif process_result.missing_day_reminder:
            prefix = process_result.missing_day_reminder + "\n\n"
        return prefix + report.content

    async def _clarify(self, arguments: dict[str, Any]) -> str:
        question = str(arguments.get("question") or "").strip()
        if not question:
            question = "你想记录什么具体内容？"
        reason = str(arguments.get("reason") or "").strip()
        if reason:
            return f"{question}\n\n原因：{reason}"
        return question

    async def _profile_update(self, arguments: dict[str, Any]) -> str:
        confidence = str(arguments.get("confidence") or "medium").strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        self.store.add_profile_update(
            ProfileUpdate(
                id=uuid4().hex[:12],
                record_id="self-log-tool",
                category=str(arguments.get("category") or ""),
                entity_type=str(arguments.get("entity_type") or ""),
                entity_name=str(arguments.get("entity_name") or ""),
                suggested_value=str(arguments.get("suggested_value") or ""),
                confidence=confidence,
            )
        )
        return "✅ 资料更新建议已落盘。"

    def _clarification_reply(self, arguments: dict[str, Any]) -> str:
        question = str(arguments.get("clarification_question") or "").strip()
        if not question:
            fields = [str(item) for item in arguments.get("unclear_fields") or [] if str(item).strip()]
            detail = "、".join(fields) if fields else "关键信息"
            question = f"这条记录里的{detail}还不够清楚，可以补充说明一下吗？"
        return question

    def _record_high_level_fields(self, arguments: dict[str, Any]) -> dict[str, str]:
        keys = (
            "corrected_content",
            "summary",
            "tags",
            "emotion",
            "emotion_reason",
            "related_people",
            "related_places",
        )
        values = {key: str(arguments.get(key) or "").strip() for key in keys}
        return {key: value for key, value in values.items() if value}

    async def _view(self, arguments: dict[str, Any]) -> str:
        limit_raw = arguments.get("limit") or 10
        limit = int(limit_raw)
        records = self.store.list_records(limit=limit)
        if not records:
            return "暂无已整理 self-log 记录。"
        return "\n".join(
            f"{record.date} {record.emotion} [{record.source}] [{record.tags}] {record.summary}"
            for record in records
        )

    async def _status(self, arguments: dict[str, Any]) -> str:
        del arguments
        status = self.store.status()
        return (
            f"self-log 状态：entries={status['entries']} "
            f"records={status['records']} pending={status['pending_confirmations']}"
        )


class SelfLogToolAgent:
    """Route natural-language self-log requests to self-log domain tools."""

    def __init__(
        self,
        store: SelfLogStore,
        *,
        router: Any | None = None,
        agent_factory=OpenHarnessSelfLogAgent,
    ) -> None:
        self.store = store
        self.router = router or OpenHarnessSelfLogAgent()
        self.registry = SelfLogToolRegistry(store, agent_factory=agent_factory)

    async def run(self, text: str) -> str:
        tool_calls = await self.router.choose_self_log_tool(text, self.registry.to_api_schema())
        if not tool_calls:
            return await self.registry.execute("self_log_record", {"content": text})
        outputs = []
        for call in tool_calls:
            outputs.append(await self.registry.execute(call.name, call.input))
        return "\n\n".join(outputs)


def extract_self_log_content(raw: str) -> str | None:
    """Return content for `/self-log ...` messages, or None when not a self-log command."""

    parsed = parse_self_log_command(raw)
    if parsed is None:
        return None
    if parsed.action != "record":
        return None
    return parsed.argument or None


def parse_self_log_command(raw: str, *, default_record: bool = False) -> SelfLogCommand | None:
    """Parse a remote `/self-log` command."""

    stripped = raw.strip()
    if not stripped:
        return None
    command, _, rest = stripped.partition(" ")
    if command != "/self-log":
        if default_record and not stripped.startswith("/"):
            return SelfLogCommand("record", stripped)
        return None
    rest = rest.strip()
    if not rest:
        return SelfLogCommand("help")
    action, _, argument = rest.partition(" ")
    if action in {"help", "process", "view", "status", "backfill"}:
        return SelfLogCommand(action, argument.strip())
    if action == "report":
        return SelfLogCommand("report", (argument.strip() or "weekly"))
    if action == "record":
        return SelfLogCommand("record", argument.strip())
    return SelfLogCommand("record", rest)


def self_log_help_text() -> str:
    return "\n".join(
        [
            "self-log 用法：",
            "- 运行 `ohmo self-log listen` 后，直接发送文字会被当作日常记录",
            "- /self-log 今天完成了什么、感受如何",
            "- /self-log process",
            "- /self-log backfill 2026-05-15 昨天补充的记录",
            "- /self-log report weekly",
            "- /self-log report monthly",
            "- /self-log report yearly",
            "- /self-log view",
            "- /self-log status",
            "- /self-log help",
        ]
    )


def parse_backfill_argument(argument: str, *, process_date: str | date | None = None) -> tuple[str, str]:
    stripped = argument.strip()
    first, _, rest = stripped.partition(" ")
    try:
        backfill_date = date.fromisoformat(first).isoformat()
        content = rest.strip()
    except ValueError:
        backfill_date = (_coerce_date(process_date) - timedelta(days=1)).isoformat()
        content = stripped
    if not content:
        raise ValueError("self-log backfill content cannot be empty")
    return backfill_date, content


def format_process_result(result: ProcessResult) -> str:
    lines = [
        f"self-log processed: {result.auto_processed} | pending confirmations: {result.pending_confirmations}"
    ]
    if result.backfilled and result.backfill_date:
        lines.append(f"backfilled: {result.backfill_date}")
    if result.backfill_prompt:
        lines.append(result.backfill_prompt)
    if result.missing_day_reminder:
        lines.append(result.missing_day_reminder)
    if result.pending_reminder:
        lines.append(result.pending_reminder)
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_date(value: str | date | None) -> date:
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(value)
    return datetime.now().astimezone().date()


def _backfill_prompt(backfill_date: str) -> str:
    return (
        f"发现昨天（{backfill_date}）没有记录，是否需要简单补充？\n"
        f"可回复 `/self-log backfill {backfill_date} 你的补录内容`，或回复“跳过”。"
    )


def _object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "需要"}


def _safe_parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if "```json" in stripped:
        stripped = stripped.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in stripped:
        stripped = stripped.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {
            "corrected_content": text,
            "summary": "（JSON 解析失败，原样保留）",
            "tags": "其他",
            "emotion": "中性",
            "emotion_reason": "解析失败",
            "related_people": "",
            "related_places": "",
            "needs_clarification": False,
            "clarification_reason": "",
            "clarification_questions": [],
            "suggested_profile_updates": [],
            "note": "LLM 输出格式异常，已原样保留",
        }
    return parsed if isinstance(parsed, dict) else {}


_PROCESS_RECORD_SYSTEM_PROMPT = """你是一位资深心理咨询师兼文字编辑。你的任务是帮用户把日常口语化的记录整理成结构化的个人日志。

铁律：
1. 绝不猜测：遇到不确定的人名、关系、地点、事件含义时，明确标记 needs_clarification。
2. 信息密度优先：输出避免大段叙述，多用结构化格式。

输出严格 JSON：
{
  "corrected_content": "修正后的原文",
  "summary": "一句话摘要",
  "tags": "标签1,标签2",
  "emotion": "积极/消极/中性/复杂",
  "emotion_reason": "情绪判断理由",
  "related_people": "人物1,人物2",
  "related_places": "地点1,地点2",
  "needs_clarification": false,
  "clarification_reason": "",
  "clarification_questions": [],
  "suggested_profile_updates": [
    {"category": "家庭/工作/生活", "entity_type": "人物/地点/关系/项目", "entity_name": "名称", "suggested_value": "建议填入资料的内容", "confidence": "high/medium/low"}
  ],
  "note": "补充说明"
}
"""

_SELF_LOG_TOOL_ROUTER_PROMPT = """你是 self-log app 的语义路由 agent。用户会用自然语言表达记录、补录、整理、查看、状态、生成报告等需求。

必须优先调用 self-log 专用工具完成动作，不要只用文字回答。

路由规则：
- 普通日常记录、情绪、事件流水：调用 self_log_record。
- 调用 self_log_record 前，必须先判断人物、事件、人物关系、地点、时间、名词等关键信息是否清楚；不清楚时调用 self_log_clarify，绝不要入库。
- 调用 self_log_record 时，除了原始 content，也尽量提供 corrected_content、summary、tags、emotion 等高层结构化字段。
- 处理待整理记录、待确认、提醒、补录检测：调用 self_log_process；不要提供当前日期，工具会自行计算。
- 明确补昨天，且已经提供了实际记录内容：调用 self_log_backfill；不要提供昨天日期，工具会自行计算。
- 只有“我想补录/忘记记录/帮我记录一下”等意图，但没有实际记录内容：调用 self_log_clarify 追问具体内容，绝不要把这句话本身记录成日志。
- 周报/月报/年报/复盘报告：调用 self_log_report。
- 查看最近记录：调用 self_log_view。
- 查看数量、路径、状态、待确认数：调用 self_log_status。
- 沟通澄清或整理后发现值得长期保留的用户相关高价值信息：调用 self_log_profile_update。
"""


def _report_system_prompt(report_type: str) -> str:
    labels = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}
    return (
        f"你是一位个人成长教练。请基于用户记录生成一份高密度、结构化的{labels[report_type]}。"
        "使用 Markdown、表格和 bullet points；温暖、客观、有洞察力，拒绝空泛鼓励。"
    )
