"""Self-log domain tools used by the standalone app agent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from openharness.utils.log import get_logger

from self_log.agent import OpenHarnessSelfLogAgent
from self_log.models import ProfileUpdate, SelfLogRecord
from self_log.processor import SelfLogProcessor
from self_log.store import SelfLogStore

logger = get_logger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolParameterSchema:
    type: str
    properties: dict[str, Any]
    required: list[str]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: ToolParameterSchema

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": self.input_schema.type,
                "properties": self.input_schema.properties,
                "required": self.input_schema.required,
            },
        }


@dataclass(frozen=True)
class SelfLogDomainTool:
    definition: ToolDefinition
    handler: ToolHandler


class SelfLogToolRegistry:
    """Tool registry for the self-log domain."""

    def __init__(
        self,
        store: SelfLogStore,
        processor: SelfLogProcessor | None = None,
        agent_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self.processor = processor
        self._agent_factory = agent_factory

    def _processor(self) -> SelfLogProcessor:
        if self.processor is None:
            self.processor = SelfLogProcessor(
                self.store,
                self._agent_factory() if self._agent_factory is not None else None,
            )
        return self.processor

    def tools(self) -> list[SelfLogDomainTool]:
        return [
            SelfLogDomainTool(_tool_record(), self._handle_record),
            SelfLogDomainTool(_tool_import_records(), self._handle_import_records),
            SelfLogDomainTool(_tool_clarify(), self._handle_clarify),
            SelfLogDomainTool(_tool_process(), self._handle_process),
            SelfLogDomainTool(_tool_backfill(), self._handle_backfill),
            SelfLogDomainTool(_tool_report(), self._handle_report),
            SelfLogDomainTool(_tool_view(), self._handle_view),
            SelfLogDomainTool(_tool_status(), self._handle_status),
            SelfLogDomainTool(_tool_profile_update(), self._handle_profile_update),
        ]

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [tool.definition.to_api_schema() for tool in self.tools()]

    def to_api_schema(self) -> list[dict[str, Any]]:
        return self.tool_schemas()

    def list_tools(self) -> list[SelfLogDomainTool]:
        return self.tools()

    def by_name(self) -> dict[str, SelfLogDomainTool]:
        return {tool.definition.name: tool for tool in self.tools()}

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self.by_name().get(name)
        if tool is None:
            logger.error("execute unknown tool name=%s", name)
            raise ValueError(f"Unknown self-log tool: {name}")
        logger.debug("execute tool=%s arguments=%r", name, {k: v for k, v in arguments.items() if k != "content"})
        result = await tool.handler(arguments)
        return str(result.get("message") or result)

    async def _handle_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments.get("needs_clarification"):
            question = str(
                arguments.get("clarification_question")
                or arguments.get("question")
                or "这条记录有信息不清楚，能补充一下吗？"
            )
            logger.debug("_handle_record needs_clarification question=%r", question[:120])
            return {"ok": True, "needs_user_reply": True, "message": question}
        content = _required_text(arguments, "content")
        metadata = {
            key: value
            for key, value in {
                "record_date": arguments.get("record_date") or arguments.get("date"),
                "source": arguments.get("source") or "原始",
            }.items()
            if value
        }
        entry = self.store.record(content, metadata=metadata)
        if any(
            arguments.get(key)
            for key in (
                "corrected_content",
                "summary",
                "tags",
                "emotion",
                "related_people",
                "related_places",
            )
        ):
            record = SelfLogRecord(
                id=uuid4().hex[:12],
                entry_id=entry.id,
                date=str(metadata.get("record_date") or entry.created_at[:10]),
                raw_content=content,
                corrected_content=str(arguments.get("corrected_content") or content),
                summary=str(arguments.get("summary") or ""),
                tags=str(arguments.get("tags") or ""),
                emotion=str(arguments.get("emotion") or "中性"),
                emotion_reason=str(arguments.get("emotion_reason") or ""),
                related_people=str(arguments.get("related_people") or ""),
                related_places=str(arguments.get("related_places") or ""),
                source=str(metadata.get("source") or "原始"),
                created_at=_now(),
            )
            self.store.add_record(record)
            return {
                "ok": True,
                "entry_id": entry.id,
                "record_id": record.id,
                "message": "✅ 刚才的记录已经入库。",
            }
        backfill_hint = _backfill_hint(self.store, arguments.get("record_date") or arguments.get("date"))
        message = "✅ 刚才的记录已经入库。"
        if backfill_hint:
            message += "\n" + backfill_hint
        logger.info("_handle_record entry_id=%s", entry.id)
        return {"ok": True, "entry_id": entry.id, "message": message}

    async def _handle_import_records(self, arguments: dict[str, Any]) -> dict[str, Any]:
        records = arguments.get("records")
        if not isinstance(records, list) or not records:
            raise ValueError("records must be a non-empty list")
        created: list[str] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("content") or item.get("raw_content") or item.get("corrected_content") or "")
            if not raw.strip():
                continue
            entry = self.store.record(
                raw,
                metadata={
                    "record_date": item.get("date"),
                    "source": item.get("source") or arguments.get("source") or "补录",
                },
            )
            record = SelfLogRecord(
                id=uuid4().hex[:12],
                entry_id=entry.id,
                date=str(item.get("date") or datetime.now(timezone.utc).date().isoformat()),
                raw_content=raw,
                corrected_content=str(item.get("corrected_content") or raw),
                summary=str(item.get("summary") or ""),
                tags=str(item.get("tags") or ""),
                emotion=str(item.get("emotion") or "中性"),
                emotion_reason=str(item.get("emotion_reason") or ""),
                related_people=str(item.get("related_people") or ""),
                related_places=str(item.get("related_places") or ""),
                source=str(item.get("source") or arguments.get("source") or "补录"),
                created_at=_now(),
            )
            self.store.add_record(record)
            created.append(record.id)
        logger.info("_handle_import_records imported=%d", len(created))
        return {
            "ok": True,
            "record_ids": created,
            "imported": len(created),
            "message": f"已批量入库 {len(created)} 条 self-log 记录。",
        }

    async def _handle_clarify(self, arguments: dict[str, Any]) -> dict[str, Any]:
        question = _required_text(arguments, "question")
        return {"ok": True, "needs_user_reply": True, "question": question, "message": question}

    async def _handle_process(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._processor().process_pending(
            limit=int(arguments.get("limit") or 20),
            backfill_missing_yesterday=bool(arguments.get("backfill_missing_yesterday") or False),
        )
        return {
            "ok": True,
            "auto_processed": result.auto_processed,
            "pending_confirmations": result.pending_confirmations,
            "pending_reminder": result.pending_reminder,
            "missing_day_reminder": result.missing_day_reminder,
            "message": f"已整理 {result.auto_processed} 条，待确认 {result.pending_confirmations} 条。",
        }

    async def _handle_backfill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        content = _required_text(arguments, "content")
        target_date = arguments.get("date") or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        logger.info("_handle_backfill date=%s content=%r", target_date, content[:80])
        entry = self.store.record(content, metadata={"record_date": target_date, "source": "补录"})
        result = await self._processor().process_pending(limit=20)
        return {
            "ok": True,
            "entry_id": entry.id,
            "date": target_date,
            "auto_processed": result.auto_processed,
            "message": f"已补录 {target_date}",
        }

    async def _handle_report(self, arguments: dict[str, Any]) -> dict[str, Any]:
        report_type = str(arguments.get("report_type") or arguments.get("type") or "weekly")
        report = await self._processor().generate_report(report_type)
        return {"ok": True, "report_type": report_type, "content": report.content, "message": report.content}

    async def _handle_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = int(arguments.get("limit") or 10)
        records = [record.to_dict() for record in self.store.list_records(limit=limit)]
        return {"ok": True, "records": records, "message": _format_records(records)}

    async def _handle_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = self.store.status()
        message = (
            f"self-log 状态：entries={status['entries']}，records={status['records']}，"
            f"pending={status['pending_confirmations']}，path={status['path']}"
        )
        return {"ok": True, **status, "message": message}

    async def _handle_profile_update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        update = ProfileUpdate(
            id=uuid4().hex[:12],
            record_id=str(arguments.get("record_id") or ""),
            category=str(arguments.get("category") or ""),
            entity_type=str(arguments.get("entity_type") or ""),
            entity_name=str(arguments.get("entity_name") or ""),
            suggested_value=str(arguments.get("suggested_value") or ""),
            confidence=str(arguments.get("confidence") or "medium"),
        )
        self.store.add_profile_update(update)
        return {"ok": True, "profile_update_id": update.id, "message": "已记录资料更新建议。"}


class SelfLogToolAgent:
    """Execute model-selected self-log tools."""

    def __init__(
        self,
        registry: SelfLogToolRegistry | SelfLogStore,
        agent: OpenHarnessSelfLogAgent | None = None,
        *,
        router: Any | None = None,
        agent_factory: Callable[[], Any] | None = None,
    ) -> None:
        if isinstance(registry, SelfLogStore):
            self.registry = SelfLogToolRegistry(registry, agent_factory=agent_factory)
        else:
            self.registry = registry
        self.agent = router or agent or OpenHarnessSelfLogAgent()

    async def run(self, user_text: str) -> str:
        tool_uses = await self.agent.choose_self_log_tool(user_text, self.registry.tool_schemas())
        tools = self.registry.by_name()
        if not tool_uses:
            logger.info("SelfLogToolAgent.run no tools selected for text=%r", user_text[:120])
            return "这里是 self-log 记录专用 bot，请发送想要记录的内容。"
        results: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            tool = tools.get(tool_use.name)
            if tool is None:
                logger.warning("SelfLogToolAgent.run unknown tool=%s", tool_use.name)
                continue
            logger.debug("SelfLogToolAgent.run executing tool=%s", tool_use.name)
            results.append(await tool.handler(dict(tool_use.input or {})))
        return "\n".join(str(result.get("message") or result) for result in results)


def _tool_record() -> ToolDefinition:
    return _definition(
        "self_log_record",
        "Record a clear self-log entry, optionally with model-structured fields.",
        [
            ("content", "string", "Original self-log content.", True),
            ("corrected_content", "string", "Model-corrected content.", False),
            ("summary", "string", "One-sentence summary.", False),
            ("tags", "string", "Comma-separated tags.", False),
            ("emotion", "string", "Emotion label.", False),
            ("emotion_reason", "string", "Emotion reasoning.", False),
            ("related_people", "string", "Comma-separated people.", False),
            ("related_places", "string", "Comma-separated places.", False),
            ("source", "string", "Record source.", False),
        ],
    )


def _tool_import_records() -> ToolDefinition:
    return ToolDefinition(
        name="self_log_import_records",
        description="Import multiple structured records parsed by the model from messy human input.",
        input_schema=ToolParameterSchema(
            type="object",
            properties={
                "source_text": {"type": "string", "description": "Original batch text."},
                "source": {"type": "string", "description": "Import source label."},
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string"},
                            "content": {"type": "string"},
                            "corrected_content": {"type": "string"},
                            "summary": {"type": "string"},
                            "tags": {"type": "string"},
                            "emotion": {"type": "string"},
                            "emotion_reason": {"type": "string"},
                            "related_people": {"type": "string"},
                            "related_places": {"type": "string"},
                            "source": {"type": "string"},
                        },
                        "required": ["date", "content"],
                    },
                },
            },
            required=["records"],
        ),
    )


def _tool_clarify() -> ToolDefinition:
    return _definition(
        "self_log_clarify",
        "Ask the user a clarification question instead of guessing.",
        [("question", "string", "Clarification question.", True)],
    )


def _tool_process() -> ToolDefinition:
    return _definition(
        "self_log_process",
        "Process pending self-log entries and reminders.",
        [
            ("limit", "integer", "Maximum pending entries to process.", False),
            ("backfill_missing_yesterday", "boolean", "Whether to check yesterday.", False),
        ],
    )


def _tool_backfill() -> ToolDefinition:
    return _definition(
        "self_log_backfill",
        "Backfill a missing self-log entry.",
        [("content", "string", "Backfill content.", True)],
    )


def _tool_report() -> ToolDefinition:
    return _definition(
        "self_log_report",
        "Generate weekly, monthly, or yearly self-log report.",
        [("type", "string", "weekly/monthly/yearly.", True)],
    )


def _tool_view() -> ToolDefinition:
    return _definition(
        "self_log_view",
        "View recent self-log records.",
        [("limit", "integer", "Number of records.", False)],
    )


def _tool_status() -> ToolDefinition:
    return _definition("self_log_status", "Show self-log status.", [])


def _tool_profile_update() -> ToolDefinition:
    return _definition(
        "self_log_profile_update",
        "Store a suggested durable user profile update.",
        [
            ("record_id", "string", "Related record id.", False),
            ("category", "string", "Category.", True),
            ("entity_type", "string", "Entity type.", True),
            ("entity_name", "string", "Entity name.", True),
            ("suggested_value", "string", "Suggested value.", True),
            ("confidence", "string", "high/medium/low.", False),
        ],
    )


def _definition(
    name: str,
    description: str,
    params: list[tuple[str, str, str, bool]],
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=ToolParameterSchema(
            type="object",
            properties={key: {"type": type_, "description": desc} for key, type_, desc, _ in params},
            required=[key for key, _, _, required in params if required],
        ),
    )


def _required_text(arguments: dict[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _format_records(records: list[dict[str, Any]]) -> str:
    if not records:
        return "暂无 self-log 记录。"
    return "\n".join(
        f"- {record.get('date', '')} {record.get('summary') or record.get('raw_content', '')}"
        for record in records
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backfill_hint(store: SelfLogStore, record_date: object) -> str | None:
    if not record_date:
        return None
    try:
        day = datetime.strptime(str(record_date), "%Y-%m-%d").date()
    except ValueError:
        return None
    yesterday = (day - timedelta(days=1)).isoformat()
    if store.has_activity_on(yesterday):
        return None
    return f"发现昨天（{yesterday}）没有记录。可以回复 `/self-log backfill {yesterday} 具体内容` 补录。"
