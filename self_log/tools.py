"""Self-log domain tools used by the standalone app agent."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.utils.log import get_logger

from self_log.memory import add_memory_entry
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
            SelfLogDomainTool(_tool_search(), self._handle_search),
            SelfLogDomainTool(_tool_update_record(), self._handle_update_record),
            SelfLogDomainTool(_tool_delete_record(), self._handle_delete_record),
            SelfLogDomainTool(_tool_status(), self._handle_status),
            SelfLogDomainTool(_tool_get_now(), self._handle_get_now),
            SelfLogDomainTool(_tool_profile_update(), self._handle_profile_update),
            SelfLogDomainTool(_tool_remember(), self._handle_remember),
            SelfLogDomainTool(_tool_suggest_reflection(), self._handle_suggest_reflection),
            SelfLogDomainTool(_tool_sync_context(), self._handle_sync_context),
            SelfLogDomainTool(_tool_visualize(), self._handle_visualize),
            SelfLogDomainTool(_tool_export(), self._handle_export),
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
                "message": f"✅ 刚才的记录已经入库。record_id={record.id}",
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
        ids_hint = "，可通过 self_log_search/view 获取" if len(created) > 5 else "：" + ", ".join(created)
        return {
            "ok": True,
            "record_ids": created,
            "imported": len(created),
            "message": f"已批量入库 {len(created)} 条 self-log 记录{ids_hint}。",
        }

    async def _handle_clarify(self, arguments: dict[str, Any]) -> dict[str, Any]:
        question = _required_text(arguments, "question")
        context = str(arguments.get("context") or "").strip()
        message = f"（关于：{context}）\n{question}" if context else question
        return {"ok": True, "needs_user_reply": True, "question": question, "message": message}

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
            "message": f"已补录 {target_date}，entry_id={entry.id}",
        }

    async def _handle_report(self, arguments: dict[str, Any]) -> dict[str, Any]:
        report_type = str(arguments.get("report_type") or arguments.get("type") or "weekly")
        report = await self._processor().generate_report(report_type)
        return {"ok": True, "report_type": report_type, "content": report.content, "message": report.content}

    async def _handle_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = int(arguments.get("limit") or 10)
        records = [record.to_dict() for record in self.store.list_records(limit=limit)]
        return {"ok": True, "records": records, "message": _format_records(records)}

    async def _handle_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "")
        tags = _csv_list(arguments.get("tags"))
        emotions = _csv_list(arguments.get("emotions"))
        start_date = str(arguments.get("start_date") or "")
        end_date = str(arguments.get("end_date") or "")
        limit = int(arguments.get("limit") or 10)

        records = self.store.search_records(
            query=query,
            tags=tags,
            emotions=emotions,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return {
            "ok": True,
            "records": [r.to_dict() for r in records],
            "message": f"找到了 {len(records)} 条相关记录：\n" + _format_records([r.to_dict() for r in records]),
        }

    async def _handle_update_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing self-log record."""
        record_id = _required_text(arguments, "record_id")
        
        # Valid fields for update
        updates = {}
        for field in [
            "summary", "tags", "emotion", "emotion_reason", 
            "corrected_content", "related_people", "related_places", "date"
        ]:
            if field in arguments:
                updates[field] = arguments[field]
        
        if not updates:
            return {"ok": False, "message": "未提供任何更新字段。"}
            
        success = self.store.update_record(record_id, **updates)
        if success:
            return {"ok": True, "message": f"✅ 已成功更新记录 {record_id}。"}
        else:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}

    async def _handle_delete_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Permanently delete an existing self-log record."""
        record_id = _required_text(arguments, "record_id")
        success = self.store.delete_record(record_id)
        if success:
            return {"ok": True, "message": f"🗑️ 已永久删除记录 {record_id}。"}
        else:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}

    async def _handle_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = self.store.status()
        message = (
            f"self-log 状态：entries={status['entries']}，records={status['records']}，"
            f"pending={status['pending_confirmations']}，path={status['path']}"
        )
        return {"ok": True, **status, "message": message}

    async def _handle_get_now(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Get the current local date, time, and timezone information."""
        now = datetime.now()
        local_now = now.astimezone()
        tz_name = local_now.tzname()
        tz_offset = local_now.strftime("%z")
        
        info = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
            "timezone": tz_name,
            "tz_offset": tz_offset,
            "iso": now.isoformat(),
        }
        message = (
            f"当前时间：{info['date']} {info['time']} ({info['weekday']})\n"
            f"时区：{info['timezone']} (UTC{info['tz_offset']})"
        )
        return {"ok": True, **info, "message": message}

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

    async def _handle_remember(self, arguments: dict[str, Any]) -> dict[str, Any]:
        title = _required_text(arguments, "title")
        content = _required_text(arguments, "content")
        path = add_memory_entry(self.store.workspace, title, content)
        return {"ok": True, "message": f"已写入 memory：{path.name}"}

    async def _handle_suggest_reflection(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Generate reflection questions based on recent records."""
        records = self.store.list_records(limit=20)
        if not records:
            return {"ok": False, "message": "暂无记录，无法生成复盘建议。"}

        focus = str(arguments.get("focus") or "").strip()
        style = str(arguments.get("style") or "").strip()

        # Use the agent for dynamic question generation
        processor = self._processor()
        records_summary = "\n".join([f"- [{r.date}] {r.summary}" for r in records])

        try:
            questions = await processor.agent.generate_reflection_questions(
                profile_context=processor._profile_context(),
                records_summary=records_summary,
                focus=focus if focus else None,
                style=style if style else None,
            )
            return {"ok": True, "message": f"基于你最近的记录，建议复盘以下问题：\n{questions}"}
        except Exception as exc:
            logger.error("Failed to generate dynamic reflection questions: %s", exc)
            # Fallback to static questions
            return {
                "ok": True,
                "message": (
                    "基于你最近的记录，建议复盘以下问题：\n"
                    "1. 最近提到最多的标签是哪些，它们带给你什么感触？\n"
                    "2. 哪一天的情绪最波动，发生了什么？\n"
                    "3. 有哪些事情是你重复记录但尚未解决的？"
                )
            }

    async def _handle_sync_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fetch external context like calendar or git commits."""
        source = str(arguments.get("source") or "all").lower()
        context_items: list[str] = []

        if source in {"all", "git"}:
            # Mock git commit fetch for now
            context_items.append("- [Git] 提交了 self-log 架构优化代码")

        if source in {"all", "calendar"}:
            # Mock calendar fetch for now
            context_items.append("- [Calendar] 14:00 团队同步会")

        if not context_items:
            return {"ok": True, "message": "未发现相关的外部上下文信息。"}

        return {
            "ok": True,
            "items": context_items,
            "message": "已同步以下外部上下文：\n" + "\n".join(context_items)
        }

    async def _handle_visualize(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Generate a visualization of recent records based on the requested type."""
        viz_type = str(arguments.get("type") or "emotion_distribution").lower()
        days = int(arguments.get("days") or 30)

        # Filter records by date
        start_date = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        records = self.store.search_records(start_date=start_date, limit=100)

        if not records:
            return {"ok": False, "message": f"最近 {days} 天暂无记录，无法可视化。"}

        from collections import Counter
        if viz_type == "emotion_distribution":
            emotions = [r.emotion for r in records]
            counts = Counter(emotions)
            chart = "\n".join([f"{emo}: {'█' * count}" for emo, count in counts.items()])
            return {"ok": True, "message": f"最近 {days} 天的情绪分布：\n{chart}"}

        if viz_type == "tag_cloud":
            all_tags = []
            for r in records:
                all_tags.extend([t.strip() for t in r.tags.split(",") if t.strip()])
            counts = Counter(all_tags).most_common(15)
            cloud = "\n".join([f"{tag}: {count}" for tag, count in counts])
            return {"ok": True, "message": f"最近 {days} 天的高频标签 Top 15：\n{cloud}"}

        if viz_type == "activity_heatmap":
            dates = [r.date for r in records]
            counts = Counter(dates)
            # Simple list-based "heatmap"
            heatmap = []
            for i in range(days, -1, -1):
                d = (datetime.now(timezone.utc).date() - timedelta(days=i)).isoformat()
                count = counts.get(d, 0)
                heatmap.append("█" if count > 0 else "░")

            chunk_size = 7
            rows = [" ".join(heatmap[i:i + chunk_size]) for i in range(0, len(heatmap), chunk_size)]
            return {"ok": True, "message": f"最近 {days} 天的活动热力图 (每行 7 天)：\n" + "\n".join(rows)}

        return {"ok": False, "message": f"不支持的可视化类型：{viz_type}。目前支持：emotion_distribution, tag_cloud, activity_heatmap"}

    async def _handle_export(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Export records with dynamic filters and optional AI summary."""
        fmt = str(arguments.get("format") or "markdown").lower()
        start_date = str(arguments.get("start_date") or "")
        end_date = str(arguments.get("end_date") or "")
        include_summary = bool(arguments.get("include_summary") or False)
        
        export_dir = self.store.workspace / "exports"
        export_dir.mkdir(exist_ok=True)

        records = self.store.search_records(
            start_date=start_date if start_date else None,
            end_date=end_date if end_date else None,
            limit=1000,
        )
        
        if not records:
            return {"ok": False, "message": "范围内暂无记录可供导出。"}

        filename_suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if fmt == "json":
            path = export_dir / f"export_{filename_suffix}.json"
            data = [r.to_dict() for r in records]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            # Default to Markdown
            path = export_dir / f"export_{filename_suffix}.md"
            header = f"# Self-Log Export ({start_date or 'Beginning'} - {end_date or 'Now'})\n\n"
            
            ai_summary = ""
            if include_summary:
                processor = self._processor()
                try:
                    ai_summary = await processor.agent.generate_report(
                        report_type="monthly", # Reuse report logic for summary
                        records=[r.to_dict() for r in records[:50]],
                        profile_context=processor._profile_context()
                    )
                    ai_summary = f"## AI 导出摘要\n\n{ai_summary}\n\n---\n\n"
                except Exception as exc:
                    logger.error("Failed to include AI summary in export: %s", exc)

            content = header + ai_summary
            for r in records:
                content += f"### {r.date} {r.emotion}\n**摘要**：{r.summary}\n\n{r.corrected_content}\n\n---\n\n"
            path.write_text(content, encoding="utf-8")

        return {
            "ok": True,
            "path": str(path),
            "message": f"已成功按 {fmt} 格式导出 {len(records)} 条记录到：{path}"
        }


class _AnyInput(BaseModel):
    """Permissive Pydantic model that accepts any tool arguments as extra fields."""

    model_config = ConfigDict(extra="allow")


class _SelfLogToolAdapter(BaseTool):
    """Thin BaseTool wrapper around a SelfLogDomainTool handler."""

    input_model = _AnyInput

    def __init__(self, domain_tool: SelfLogDomainTool) -> None:
        self.name = domain_tool.definition.name  # type: ignore[misc]
        self.description = domain_tool.definition.description  # type: ignore[misc]
        self._domain_tool = domain_tool

    def to_api_schema(self) -> dict[str, Any]:
        return self._domain_tool.definition.to_api_schema()

    def is_read_only(self, arguments: BaseModel) -> bool:
        return self.name in {"self_log_view", "self_log_status"}

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        raw = arguments.model_dump()
        try:
            result = await self._domain_tool.handler(raw)
            return ToolResult(output=str(result.get("message") or result))
        except Exception as exc:
            return ToolResult(output=str(exc), is_error=True)


def build_oh_registry(registry: SelfLogToolRegistry) -> ToolRegistry:
    """Build an OpenHarness ToolRegistry from a SelfLogToolRegistry."""
    oh_registry = ToolRegistry()
    for domain_tool in registry.tools():
        oh_registry.register(_SelfLogToolAdapter(domain_tool))
    return oh_registry


def _tool_record() -> ToolDefinition:
    return _definition(
        "self_log_record",
        (
            "Record a self-log entry when the intent and core content are clear enough to understand. "
            "Do NOT call this when the user's intent is ambiguous or the record is unintelligible — "
            "call self_log_clarify instead. Fill in structured fields (summary, tags, emotion, etc.) "
            "based on your understanding of the content."
        ),
        [
            ("content", "string", "Original self-log content as the user wrote it.", True),
            ("corrected_content", "string", "Lightly corrected / cleaned-up version of the content.", False),
            ("summary", "string", "One-sentence summary.", False),
            ("tags", "string", "Comma-separated tags.", False),
            ("emotion", "string", "Emotion label: 积极/消极/中性/复杂.", False),
            ("emotion_reason", "string", "Brief reason for the emotion label.", False),
            ("related_people", "string", "Comma-separated people mentioned.", False),
            ("related_places", "string", "Comma-separated places mentioned.", False),
            ("source", "string", "Record source, e.g. 原始/补录.", False),
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
        (
            "Ask the user ONE targeted clarification question instead of guessing or recording unclear content. "
            "Use when: (1) intent is ambiguous (greeting/chitchat/test), "
            "(2) the record's core subject is completely missing and matters, "
            "(3) user wants to backfill but hasn't said what to backfill. "
            "Ask only the single most important question. "
            "Include 'context' to summarize what the user originally said so the question makes sense."
        ),
        [
            ("question", "string", "The single clarification question to ask the user.", True),
            ("context", "string", "Brief summary of what the user said, to contextualize the question.", False),
        ],
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


def _tool_search() -> ToolDefinition:
    return _definition(
        "self_log_search",
        "Search through self-log records using keywords, dates, tags, or emotions.",
        [
            ("query", "string", "Text search query.", False),
            ("tags", "string", "Comma-separated tags.", False),
            ("emotions", "string", "Comma-separated emotions.", False),
            ("start_date", "string", "YYYY-MM-DD.", False),
            ("end_date", "string", "YYYY-MM-DD.", False),
            ("limit", "integer", "Number of results.", False),
        ],
    )


def _tool_update_record() -> ToolDefinition:
    return _definition(
        "self_log_update_record",
        "Modify an existing structured record. Use this to fix mistakes in summary, tags, emotions, or content.",
        [
            ("record_id", "string", "The ID of the record to update.", True),
            ("summary", "string", "New summary.", False),
            ("tags", "string", "New comma-separated tags.", False),
            ("emotion", "string", "New emotion label.", False),
            ("emotion_reason", "string", "New emotion reason.", False),
            ("corrected_content", "string", "New cleaned-up content.", False),
            ("related_people", "string", "New comma-separated people.", False),
            ("related_places", "string", "New comma-separated places.", False),
            ("date", "string", "New date (YYYY-MM-DD).", False),
        ],
    )


def _tool_delete_record() -> ToolDefinition:
    return _definition(
        "self_log_delete_record",
        (
            "PERMANENTLY DELETE an existing record. Use this with EXTREME CAUTION. "
            "Only call this when the user explicitly asks to delete a specific record by ID or content. "
            "This action is IRREVERSIBLE."
        ),
        [("record_id", "string", "The ID of the record to delete.", True)],
    )


def _tool_status() -> ToolDefinition:
    return _definition("self_log_status", "Show self-log status.", [])


def _tool_get_now() -> ToolDefinition:
    return _definition(
        "self_log_get_now",
        "Get the current local date, time, and timezone information.",
        []
    )


def _tool_profile_update() -> ToolDefinition:
    return _definition(
        "self_log_profile_update",
        (
            "Store a suggested update for transient or evolving user profile info "
            "(e.g. current preferences, temporary habits, or minor observations). "
            "Use this for things that might change over months or are not yet established as core life facts."
        ),
        [
            ("record_id", "string", "Related record id.", False),
            ("category", "string", "Category (e.g. Habits, Work, Hobbies).", True),
            ("entity_type", "string", "Entity type (e.g. Preference, Routine, Mood Pattern).", True),
            ("entity_name", "string", "Entity name.", True),
            ("suggested_value", "string", "Suggested value.", True),
            ("confidence", "string", "high/medium/low.", False),
        ],
    )


def _tool_remember() -> ToolDefinition:
    return _definition(
        "self_log_remember",
        (
            "Store highly stable, core life facts into the long-term memory directory "
            "(e.g. family trees, medical history, career milestones, home location). "
            "These facts serve as the foundation for context in all future sessions. "
            "Use this ONLY for information expected to remain valid for years."
        ),
        [
            ("title", "string", "A short English title for this memory entry (used as filename, ASCII only, e.g. 'family_members', 'medical_history').", True),
            ("content", "string", "The markdown content to store. Be factual and concise.", True),
        ],
    )


def _tool_suggest_reflection() -> ToolDefinition:
    return _definition(
        "self_log_suggest_reflection",
        "Suggest deep reflection questions based on recent self-log history. The model can provide a focus area or a specific style.",
        [
            ("focus", "string", "Specific area to focus on (e.g. 'work stress', 'family relationships').", False),
            ("style", "string", "Style of the questions (e.g. 'challenging', 'supportive', 'philosophical').", False),
        ]
    )


def _tool_sync_context() -> ToolDefinition:
    return _definition(
        "self_log_sync_context",
        "Synchronize external context like calendar events or git commits to enrich logs.",
        [("source", "string", "Source to sync: all, git, calendar.", False)]
    )


def _tool_visualize() -> ToolDefinition:
    return _definition(
        "self_log_visualize",
        "Generate a visual report of recent activity. Model can choose the type and time range.",
        [
            ("type", "string", "Type of visualization: emotion_distribution, tag_cloud, activity_heatmap.", False),
            ("days", "integer", "Number of days to analyze (default 30).", False),
        ]
    )


def _tool_export() -> ToolDefinition:
    return _definition(
        "self_log_export",
        "Export self-log records with optional filtering and AI summary. Model can choose format, date range, and whether to include an AI-generated overview.",
        [
            ("format", "string", "Export format: markdown, json.", False),
            ("start_date", "string", "YYYY-MM-DD.", False),
            ("end_date", "string", "YYYY-MM-DD.", False),
            ("include_summary", "boolean", "Whether to include an AI-generated summary at the top of the export.", False),
        ]
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


def _csv_list(value: Any) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _format_records(records: list[dict[str, Any]]) -> str:
    if not records:
        return "暂无 self-log 记录。"
    return "\n".join(
        f"- [{record.get('id', '?')}] {record.get('date', '')} {record.get('summary') or record.get('raw_content', '')}"
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
