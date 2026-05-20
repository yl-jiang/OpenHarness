"""Work-log domain tools used by the standalone app agent."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from openharness.attachments import StoredAttachment
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.bash_tool import BashTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.image_to_text_tool import ImageToTextTool
from openharness.tools.skill_manager_tool import SkillManagerTool
from openharness.utils.log import get_logger

from wolo.artifacts import persist_work_artifacts
from wolo.memory import add_memory_entry
from wolo.models import ProfileUpdate, WoloEntry, WoloRecord
from wolo.processor import WoloProcessor
from wolo.store import WoloStore
from wolo.utils import (
    _get_holiday,
    _get_period,
    _get_personal_events,
    _get_season,
    _get_weekday,
    _is_weekend,
    _now,
)

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
class WoloDomainTool:
    definition: ToolDefinition
    handler: ToolHandler


class WoloToolRegistry:
    """Tool registry for the wolo domain."""

    def __init__(
        self,
        store: WoloStore,
        processor: WoloProcessor | None = None,
        agent_factory: Callable[[], Any] | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.processor = processor
        self._agent_factory = agent_factory
        self._source_context = dict(source_context or {})

    def _processor(self) -> WoloProcessor:
        if self.processor is None:
            self.processor = WoloProcessor(
                self.store,
                self._agent_factory() if self._agent_factory is not None else None,
            )
        return self.processor

    def tools(self) -> list[WoloDomainTool]:
        return [
            WoloDomainTool(_tool_record(), self._handle_record),
            WoloDomainTool(_tool_import_records(), self._handle_import_records),
            WoloDomainTool(_tool_clarify(), self._handle_clarify),
            WoloDomainTool(_tool_process(), self._handle_process),
            WoloDomainTool(_tool_backfill(), self._handle_backfill),
            WoloDomainTool(_tool_report(), self._handle_report),
            WoloDomainTool(_tool_view(), self._handle_view),
            WoloDomainTool(_tool_search(), self._handle_search),
            WoloDomainTool(_tool_show(), self._handle_show),
            WoloDomainTool(_tool_todos(), self._handle_todos),
            WoloDomainTool(_tool_done(), self._handle_done),
            WoloDomainTool(_tool_update_todo(), self._handle_update_todo),
            WoloDomainTool(_tool_blockers(), self._handle_blockers),
            WoloDomainTool(_tool_decisions(), self._handle_decisions),
            WoloDomainTool(_tool_highlights(), self._handle_highlights),
            WoloDomainTool(_tool_work_query(), self._handle_work_query),
            WoloDomainTool(_tool_update_record(), self._handle_update_record),
            WoloDomainTool(_tool_delete_record(), self._handle_delete_record),
            WoloDomainTool(_tool_status(), self._handle_status),
            WoloDomainTool(_tool_get_now(), self._handle_get_now),
            WoloDomainTool(_tool_profile_update(), self._handle_profile_update),
            WoloDomainTool(_tool_remember(), self._handle_remember),
            WoloDomainTool(_tool_suggest_reflection(), self._handle_suggest_reflection),
            WoloDomainTool(_tool_sync_context(), self._handle_sync_context),
            WoloDomainTool(_tool_visualize(), self._handle_visualize),
            WoloDomainTool(_tool_export(), self._handle_export),
        ]

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [tool.definition.to_api_schema() for tool in self.tools()]

    def to_api_schema(self) -> list[dict[str, Any]]:
        return self.tool_schemas()

    def list_tools(self) -> list[WoloDomainTool]:
        return self.tools()

    def by_name(self) -> dict[str, WoloDomainTool]:
        return {tool.definition.name: tool for tool in self.tools()}

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self.by_name().get(name)
        if tool is None:
            logger.error("execute unknown tool name=%s", name)
            raise ValueError(f"Unknown wolo tool: {name}")
        logger.debug("execute tool=%s arguments=%r", name, {k: v for k, v in arguments.items() if k != "content"})
        result = await tool.handler(arguments)
        return str(result.get("message") or result)

    async def _handle_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Record a work entry, optionally structuring it into a queryable record.

        Two-phase design:
          Phase 1 — Always create a raw entry (guarantees no data loss).
          Phase 2 — If the model provided structured fields (summary, tags, etc.),
                    immediately create a structured record as well.
                    Otherwise, only the raw entry is persisted; it will be structured
                    later by ``process_pending()`` (triggered via wolo_process or
                    automatically before report generation).
        """
        content = _required_text(arguments, "content")
        # Fall back to local date when the model does not provide an explicit date
        local_today = datetime.now().strftime("%Y-%m-%d")
        metadata = {
            key: value
            for key, value in {
                "record_date": arguments.get("record_date") or arguments.get("date") or local_today,
                "source": arguments.get("source") or "原始",
            }.items()
            if value
        }

        # Phase 1: Persist raw entry — this never fails and guarantees the user's
        # input is safely stored even if structuring fails or is deferred.
        entry = self.store.record(content, metadata=metadata, source_context=self._source_context)

        # Phase 2: If the model already extracted structured fields, create a
        # record immediately (fast path). Otherwise the entry remains "unprocessed"
        # and will be picked up by the next `process_pending()` call.
        if any(
            arguments.get(key)
            for key in (
                "corrected_content",
                "summary",
                "tags",
                "emotion",
                "events",
                "date",
                "period",
                "related_people",
                "related_places",
                "todos",
                "decisions",
                "highlights",
            )
        ):
            date = str(arguments.get("date") or metadata.get("record_date") or entry.created_at[:10])
            events = str(arguments.get("events") or "")
            holiday = _get_holiday(date)
            if holiday and holiday not in events:
                events = f"{holiday}, {events}" if events else holiday
            personal = _get_personal_events(self.store.workspace, date)
            if personal and personal not in events:
                events = f"{personal}, {events}" if events else personal

            record = WoloRecord(
                id=uuid4().hex[:12],
                entry_id=entry.id,
                date=date,
                raw_content=content,
                corrected_content=str(arguments.get("corrected_content") or content),
                summary=str(arguments.get("summary") or ""),
                tags=str(arguments.get("tags") or ""),
                emotion=str(arguments.get("emotion") or "中性"),
                weekday=_get_weekday(date),
                events=events,
                period=str(arguments.get("period") or _get_period(entry.created_at)),
                season=_get_season(date),
                is_weekend=_is_weekend(date),
                content_length=len(content),
                emotion_reason=str(arguments.get("emotion_reason") or ""),
                related_people=str(arguments.get("related_people") or ""),
                related_places=str(arguments.get("related_places") or ""),
                source=str(metadata.get("source") or "原始"),
                created_at=_now(),
                attachments=list(entry.attachments),
            )
            self.store.add_record(record)
            persist_work_artifacts(self.store, record, arguments)
            return {
                "ok": True,
                "entry_id": entry.id,
                "record_id": record.id,
                "message": f"✅ 刚才的记录已经入库。record_id={record.id}",
            }

        # No structured fields provided — entry saved but not yet structured.
        # It will be processed by the next `wolo_process` / `process_pending()` call.
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
                source_context=self._source_context,
            )
            date = str(item.get("date") or datetime.now(timezone.utc).date().isoformat())
            events = str(item.get("events") or "")
            holiday = _get_holiday(date)
            if holiday and holiday not in events:
                events = f"{holiday}, {events}" if events else holiday

            record = WoloRecord(
                id=uuid4().hex[:12],
                entry_id=entry.id,
                date=date,
                raw_content=raw,
                corrected_content=str(item.get("corrected_content") or raw),
                summary=str(item.get("summary") or ""),
                tags=str(item.get("tags") or ""),
                emotion=str(item.get("emotion") or "中性"),
                weekday=_get_weekday(date),
                events=events,
                period=str(item.get("period") or _get_period(entry.created_at)),
                season=_get_season(date),
                is_weekend=_is_weekend(date),
                content_length=len(raw),
                emotion_reason=str(item.get("emotion_reason") or ""),
                related_people=str(item.get("related_people") or ""),
                related_places=str(item.get("related_places") or ""),
                source=str(item.get("source") or arguments.get("source") or "补录"),
                created_at=_now(),
                attachments=list(entry.attachments),
            )
            self.store.add_record(record)
            persist_work_artifacts(self.store, record, item)
            created.append(record.id)
        logger.info("_handle_import_records imported=%d", len(created))
        ids_hint = "，可通过 wolo_search/view 获取" if len(created) > 5 else "：" + ", ".join(created)
        return {
            "ok": True,
            "record_ids": created,
            "imported": len(created),
            "message": f"已批量入库 {len(created)} 条 wolo 记录{ids_hint}。",
        }

    async def _handle_clarify(self, arguments: dict[str, Any]) -> dict[str, Any]:
        question = _required_text(arguments, "question")
        context = str(arguments.get("context") or "").strip()
        message = f"（关于：{context}）\n{question}" if context else question
        return {"ok": True, "needs_user_reply": True, "question": question, "message": message}

    async def _handle_process(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Process unstructured entries that were saved but not yet converted to records.

        This handles the "Phase 2" backlog: entries created by _handle_record (Phase 1)
        that didn't have structured fields at creation time. For each unprocessed entry,
        the processor calls the LLM to extract structured fields (date, summary, tags,
        emotion, etc.) and either:
          - Creates a structured record (auto_processed), or
          - Marks it as needing user clarification (pending_confirmations).

        Also checks for missing days and generates reminders.
        """
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
        entry = self.store.record(
            content,
            metadata={"record_date": target_date, "source": "补录"},
            source_context=self._source_context,
        )
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
        records = self.store.list_records(limit=limit)
        return {
            "ok": True,
            "records": [record.to_dict() for record in records],
            "message": _format_records(self.store, records),
        }

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
            "message": f"找到了 {len(records)} 条相关记录：\n" + _format_records(self.store, records),
        }

    async def _handle_show(self, arguments: dict[str, Any]) -> dict[str, Any]:
        record_id = _required_text(arguments, "record_id")
        record = self.store.get_record(record_id)
        if record is None:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}
        entry = self.store.get_entry(record.entry_id)
        return {
            "ok": True,
            "record": record.to_dict(),
            "entry": json.loads(entry.to_json()) if entry is not None else None,
            "message": _format_record_trace(self.store, record, entry),
        }

    async def _handle_todos(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = str(arguments.get("status") or "pending")
        project = _optional_text(arguments, "project")
        limit = int(arguments.get("limit") or 20)
        todos = self.store.list_todos(status=status, project=project, limit=limit)
        return {
            "ok": True,
            "todos": [todo.to_dict() for todo in todos],
            "message": _format_todos(todos),
        }

    async def _handle_done(self, arguments: dict[str, Any]) -> dict[str, Any]:
        todo_id = _required_text(arguments, "todo_id")
        if not self.store.complete_todo(todo_id):
            return {"ok": False, "message": f"未找到可完成的待办：{todo_id}"}
        return {"ok": True, "message": f"✅ 已完成待办：{todo_id}"}

    async def _handle_update_todo(self, arguments: dict[str, Any]) -> dict[str, Any]:
        todo_id = _required_text(arguments, "todo_id")
        updates: dict[str, Any] = {}
        for field in ["title", "project", "priority", "due_date", "status"]:
            if field in arguments and arguments[field] is not None:
                updates[field] = arguments[field]
        if not updates:
            return {"ok": False, "message": "未提供任何更新字段。"}
        if not self.store.update_todo(todo_id, **updates):
            return {"ok": False, "message": f"未找到待办：{todo_id}"}
        return {"ok": True, "message": f"✅ 已更新待办：{todo_id}"}

    async def _handle_blockers(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = _optional_text(arguments, "project")
        query = _optional_text(arguments, "query")
        limit = int(arguments.get("limit") or 20)
        blockers = self.store.list_highlights(
            kind="blocker",
            project=project,
            query=query,
            limit=limit,
        )
        return {
            "ok": True,
            "blockers": [item.to_dict() for item in blockers],
            "message": _format_highlights(blockers, empty="暂无 blocker。"),
        }

    async def _handle_decisions(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = _optional_text(arguments, "project")
        query = _optional_text(arguments, "query")
        limit = int(arguments.get("limit") or 20)
        decisions = self.store.list_decisions(project=project, query=query, limit=limit)
        return {
            "ok": True,
            "decisions": [decision.to_dict() for decision in decisions],
            "message": _format_decisions(decisions),
        }

    async def _handle_highlights(self, arguments: dict[str, Any]) -> dict[str, Any]:
        kind = _optional_text(arguments, "kind")
        project = _optional_text(arguments, "project")
        query = _optional_text(arguments, "query")
        limit = int(arguments.get("limit") or 20)
        highlights = self.store.list_highlights(
            kind=kind,
            project=project,
            query=query,
            limit=limit,
        )
        return {
            "ok": True,
            "highlights": [item.to_dict() for item in highlights],
            "message": _format_highlights(highlights, empty="暂无重要事项。"),
        }

    async def _handle_work_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = _optional_text(arguments, "query")
        project = _optional_text(arguments, "project")
        limit = int(arguments.get("limit") or 10)
        records = self.store.search_records(query=query, tags=[project] if project else None, limit=limit)
        decisions = self.store.list_decisions(project=project, query=query, limit=limit)
        highlights = self.store.list_highlights(project=project, query=query, limit=limit)
        sections = [
            "## Records\n" + _format_records(self.store, records),
            "## Decisions\n" + _format_decisions(decisions),
            "## Highlights\n" + _format_highlights(highlights, empty="暂无重要事项。"),
        ]
        return {
            "ok": True,
            "records": [record.to_dict() for record in records],
            "decisions": [decision.to_dict() for decision in decisions],
            "highlights": [item.to_dict() for item in highlights],
            "message": "\n\n".join(sections),
        }

    async def _handle_update_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing wolo record."""
        record_id = _required_text(arguments, "record_id")
        
        # Valid fields for update
        updates = {}
        for field in [
            "summary", "tags", "emotion", "emotion_reason", "events", "period",
            "corrected_content", "related_people", "related_places", "date"
        ]:
            if field in arguments:
                updates[field] = arguments[field]
        
        if "date" in updates:
            date_val = str(updates["date"])
            updates["weekday"] = _get_weekday(date_val)
            updates["season"] = _get_season(date_val)
            updates["is_weekend"] = _is_weekend(date_val)
        
        if "corrected_content" in updates:
            updates["content_length"] = len(str(updates["corrected_content"]))
        
        if not updates:
            return {"ok": False, "message": "未提供任何更新字段。"}
            
        success = self.store.update_record(record_id, **updates)
        if success:
            return {"ok": True, "message": f"✅ 已成功更新记录 {record_id}。"}
        else:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}

    async def _handle_delete_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Permanently delete an existing wolo record."""
        record_id = _required_text(arguments, "record_id")
        success = self.store.delete_record(record_id)
        if success:
            return {"ok": True, "message": f"🗑️ 已永久删除记录 {record_id}。"}
        else:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}

    async def _handle_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = self.store.status()
        message = (
            f"wolo 状态：entries={status['entries']}，records={status['records']}，"
            f"todos={status['todos']}，decisions={status['decisions']}，"
            f"highlights={status['highlights']}，pending={status['pending_confirmations']}，"
            f"path={status['path']}"
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
                    "2. 哪个 blocker 或风险重复出现，根因是什么？\n"
                    "3. 哪个 prompt/tool 经验值得沉淀到下次工作流？"
                )
            }

    async def _handle_sync_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fetch external context like calendar or git commits."""
        source = str(arguments.get("source") or "all").lower()
        context_items: list[str] = []

        if source in {"all", "git"}:
            # Mock git commit fetch for now
            context_items.append("- [Git] 提交了 wolo 工作日志架构优化代码")

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
            header = f"# Work-Log Export ({start_date or 'Beginning'} - {end_date or 'Now'})\n\n"
            
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
                weekday_info = f" ({r.weekday})" if r.weekday else ""
                event_info = f" 【{r.events}】" if r.events else ""
                period_info = f" [{r.period}]" if r.period else ""
                meta_info = f" ({r.season}, {'周末' if r.is_weekend else '工作日'}, {r.content_length}字)"
                content += f"### {r.date}{weekday_info}{period_info} {r.emotion}{event_info}\n"
                content += f"**摘要**：{r.summary} {meta_info}\n\n{r.corrected_content}\n\n---\n\n"
            path.write_text(content, encoding="utf-8")

        return {
            "ok": True,
            "path": str(path),
            "message": f"已成功按 {fmt} 格式导出 {len(records)} 条记录到：{path}"
        }


class _AnyInput(BaseModel):
    """Permissive Pydantic model that accepts any tool arguments as extra fields."""

    model_config = ConfigDict(extra="allow")


class _WoloToolAdapter(BaseTool):
    """Thin BaseTool wrapper around a WoloDomainTool handler."""

    input_model = _AnyInput

    def __init__(self, domain_tool: WoloDomainTool) -> None:
        self.name = domain_tool.definition.name  # type: ignore[misc]
        self.description = domain_tool.definition.description  # type: ignore[misc]
        self._domain_tool = domain_tool

    def to_api_schema(self) -> dict[str, Any]:
        return self._domain_tool.definition.to_api_schema()

    def is_read_only(self, arguments: BaseModel) -> bool:
        return self.name in {
            "wolo_view",
            "wolo_search",
            "wolo_show",
            "wolo_status",
            "wolo_todos",
            "wolo_blockers",
            "wolo_decisions",
            "wolo_highlights",
            "wolo_work_query",
        }

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        raw = arguments.model_dump()
        try:
            result = await self._domain_tool.handler(raw)
            return ToolResult(output=str(result.get("message") or result))
        except Exception as exc:
            return ToolResult(output=str(exc), is_error=True)


def build_oh_registry(registry: WoloToolRegistry) -> ToolRegistry:
    """Build an OpenHarness ToolRegistry from a WoloToolRegistry."""
    oh_registry = ToolRegistry()
    for domain_tool in registry.tools():
        oh_registry.register(_WoloToolAdapter(domain_tool))
    oh_registry.register(BashTool())
    oh_registry.register(FileReadTool())
    oh_registry.register(ImageToTextTool())
    oh_registry.register(SkillManagerTool())
    return oh_registry


def _tool_record() -> ToolDefinition:
    return _definition(
        "wolo_record",
        (
            "Record a work log entry when the intent and core content are clear enough to understand. "
            "Use for project progress, meetings, code changes, prompt/tool experiments, blockers, "
            "decisions, reviews, incidents, and next actions. Do NOT call this when the user's intent "
            "is ambiguous or the work record is unintelligible — call wolo_clarify instead. Fill in "
            "structured fields (summary, tags, status/emotion, etc.) based on the content."
        ),
        [
            ("content", "string", "Original work-log content as the user wrote it.", True),
            ("corrected_content", "string", "Lightly corrected / cleaned-up work note without inventing facts.", False),
            ("summary", "string", "One-sentence work summary with outcome, decision, blocker, or next action.", False),
            ("tags", "string", "Comma-separated project/work tags, e.g. project, meeting, code, prompt, tool, bug, review, blocker, decision.", False),
            ("emotion", "string", "Work status label: 顺利/受阻/中性/高压/完成/风险.", False),
            ("date", "string", "YYYY-MM-DD. Only provide this if the user explicitly mentions a specific date (e.g. '昨天', '5月18日', '上周三'). If no date is mentioned, leave this empty and the system will default to today's local date.", False),
            ("period", "string", "Semantic time period extracted from content (e.g. 凌晨, 上午).", False),
            ("events", "string", "Meetings, releases, reviews, incidents, milestones, or deadlines.", False),
            ("emotion_reason", "string", "Brief reason for the work status label.", False),
            ("related_people", "string", "Comma-separated coworkers, teams, owners, or stakeholders mentioned.", False),
            ("related_places", "string", "Comma-separated repos, services, tools, systems, platforms, or locations mentioned.", False),
            ("source", "string", "Record source, e.g. 原始/补录.", False),
            ("todos", "array", "Derived todos with title, project, priority, and due_date.", False),
            ("decisions", "array", "Derived decisions with title, rationale, impact, and project.", False),
            ("highlights", "array", "Derived highlights with kind, title, content, project, and tags.", False),
        ],
    )


def _tool_import_records() -> ToolDefinition:
    return ToolDefinition(
        name="wolo_import_records",
        description="Import multiple structured work records parsed by the model from messy notes, meeting logs, report drafts, or prompt/tool experiment notes.",
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
                            "period": {"type": "string"},
                            "events": {"type": "string"},
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
        "wolo_clarify",
        (
            "Ask the user ONE targeted clarification question instead of guessing or recording unclear work content. "
            "Use when: (1) intent is ambiguous (greeting/chitchat/test), "
            "(2) the work record's core project/task/decision is completely missing and matters, "
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
        "wolo_process",
        "Process pending wolo work entries and reminders.",
        [
            ("limit", "integer", "Maximum pending entries to process.", False),
            ("backfill_missing_yesterday", "boolean", "Whether to check yesterday.", False),
        ],
    )


def _tool_backfill() -> ToolDefinition:
    return _definition(
        "wolo_backfill",
        "Backfill a missing wolo work entry.",
        [("content", "string", "Backfill content.", True)],
    )


def _tool_report() -> ToolDefinition:
    return _definition(
        "wolo_report",
        "Generate weekly, monthly, or yearly wolo work report with progress, decisions, blockers, prompt/tool lessons, and next actions.",
        [("type", "string", "weekly/monthly/yearly.", True)],
    )


def _tool_view() -> ToolDefinition:
    return _definition(
        "wolo_view",
        "View recent wolo records.",
        [("limit", "integer", "Number of records.", False)],
    )


def _tool_search() -> ToolDefinition:
    return _definition(
        "wolo_search",
        "Search through wolo work records using keywords, dates, project tags, status labels, prompt/tool names, or blockers.",
        [
            ("query", "string", "Text search query.", False),
            ("tags", "string", "Comma-separated tags.", False),
            ("emotions", "string", "Comma-separated emotions.", False),
            ("start_date", "string", "YYYY-MM-DD.", False),
            ("end_date", "string", "YYYY-MM-DD.", False),
            ("limit", "integer", "Number of results.", False),
        ],
    )


def _tool_show() -> ToolDefinition:
    return _definition(
        "wolo_show",
        "Show one wolo record with linked attachment paths and source-message trace data.",
        [("record_id", "string", "The ID of the record to inspect.", True)],
    )


def _tool_todos() -> ToolDefinition:
    return _definition(
        "wolo_todos",
        "List work todos derived from wolo records, optionally filtered by status or project.",
        [
            ("status", "string", "Todo status: pending/done. Defaults to pending.", False),
            ("project", "string", "Project filter.", False),
            ("limit", "integer", "Number of todos.", False),
        ],
    )


def _tool_done() -> ToolDefinition:
    return _definition(
        "wolo_done",
        "Mark a derived work todo as done by todo_id.",
        [("todo_id", "string", "The todo ID to complete.", True)],
    )


def _tool_update_todo() -> ToolDefinition:
    return _definition(
        "wolo_update_todo",
        "Update a work todo's fields (title, project, priority, due_date, or status).",
        [
            ("todo_id", "string", "The todo ID to update.", True),
            ("title", "string", "New title.", False),
            ("project", "string", "New project.", False),
            ("priority", "string", "New priority (high/medium/low).", False),
            ("due_date", "string", "New due date (YYYY-MM-DD or empty).", False),
            ("status", "string", "New status (pending/in_progress/done/cancelled).", False),
        ],
    )


def _tool_blockers() -> ToolDefinition:
    return _definition(
        "wolo_blockers",
        "List blocker highlights derived from work records.",
        [
            ("project", "string", "Project filter.", False),
            ("query", "string", "Text query.", False),
            ("limit", "integer", "Number of blockers.", False),
        ],
    )


def _tool_decisions() -> ToolDefinition:
    return _definition(
        "wolo_decisions",
        "List important decisions, rationale, and impact derived from work records.",
        [
            ("project", "string", "Project filter.", False),
            ("query", "string", "Text query.", False),
            ("limit", "integer", "Number of decisions.", False),
        ],
    )


def _tool_highlights() -> ToolDefinition:
    return _definition(
        "wolo_highlights",
        "List important work highlights, including prompt lessons, tool lessons, blockers, and risks.",
        [
            ("kind", "string", "Highlight kind: important/prompt/tool/blocker/risk.", False),
            ("project", "string", "Project filter.", False),
            ("query", "string", "Text query.", False),
            ("limit", "integer", "Number of highlights.", False),
        ],
    )


def _tool_work_query() -> ToolDefinition:
    return _definition(
        "wolo_work_query",
        "Answer work-history queries by retrieving matching records, decisions, and highlights.",
        [
            ("query", "string", "Question or search query, e.g. 'what did I do last week'.", False),
            ("project", "string", "Project filter.", False),
            ("limit", "integer", "Number of items per section.", False),
        ],
    )


def _tool_update_record() -> ToolDefinition:
    return _definition(
        "wolo_update_record",
        "Modify an existing structured work record. Use this to fix mistakes in summary, tags, status labels, or content.",
        [
            ("record_id", "string", "The ID of the record to update.", True),
            ("summary", "string", "New summary.", False),
            ("tags", "string", "New comma-separated tags.", False),
            ("emotion", "string", "New emotion label.", False),
            ("emotion_reason", "string", "New emotion reason.", False),
            ("period", "string", "New time period.", False),
            ("events", "string", "New events.", False),
            ("corrected_content", "string", "New cleaned-up content.", False),
            ("related_people", "string", "New comma-separated people.", False),
            ("related_places", "string", "New comma-separated places.", False),
            ("date", "string", "New date (YYYY-MM-DD).", False),
        ],
    )


def _tool_delete_record() -> ToolDefinition:
    return _definition(
        "wolo_delete_record",
        (
            "PERMANENTLY DELETE an existing record. Use this with EXTREME CAUTION. "
            "Only call this when the user explicitly asks to delete a specific record by ID or content. "
            "This action is IRREVERSIBLE."
        ),
        [("record_id", "string", "The ID of the record to delete.", True)],
    )


def _tool_status() -> ToolDefinition:
    return _definition("wolo_status", "Show wolo status.", [])


def _tool_get_now() -> ToolDefinition:
    return _definition(
        "wolo_get_now",
        "Get the current local date, time, and timezone information.",
        []
    )


def _tool_profile_update() -> ToolDefinition:
    return _definition(
        "wolo_profile_update",
        (
            "Store a suggested update for transient or evolving work profile info "
            "(e.g. active projects, reporting preferences, temporary blockers, prompt/tool habits, or minor observations). "
            "Use this for things that might change over weeks or months and are not yet established as durable work facts."
        ),
        [
            ("record_id", "string", "Related record id.", False),
            ("category", "string", "Category (e.g. Project, Tooling, Prompting, Reporting, Collaboration).", True),
            ("entity_type", "string", "Entity type (e.g. Project, Tool, Prompt Pattern, Stakeholder, Workflow).", True),
            ("entity_name", "string", "Entity name.", True),
            ("suggested_value", "string", "Suggested value.", True),
            ("confidence", "string", "high/medium/low.", False),
        ],
    )


def _tool_remember() -> ToolDefinition:
    return _definition(
        "wolo_remember",
        (
            "Store highly stable, core work facts into the long-term memory directory "
            "(e.g. project goals, repository ownership, team conventions, toolchains, recurring prompt patterns, reporting cadence). "
            "These facts serve as the foundation for future work-log sessions. "
            "Use this ONLY for information expected to remain valid beyond the current task."
        ),
        [
            ("title", "string", "A short English title for this memory entry (used as filename, ASCII only, e.g. 'project_context', 'tooling_lessons').", True),
            ("content", "string", "The markdown content to store. Be factual and concise.", True),
        ],
    )


def _tool_suggest_reflection() -> ToolDefinition:
    return _definition(
        "wolo_suggest_reflection",
        "Suggest deep work reflection questions based on recent wolo history. The model can provide a focus area or a specific style.",
        [
            ("focus", "string", "Specific area to focus on (e.g. 'release blocker', 'prompt quality', 'tool reliability').", False),
            ("style", "string", "Style of the questions (e.g. 'executive', 'action-oriented', 'root-cause').", False),
        ]
    )


def _tool_sync_context() -> ToolDefinition:
    return _definition(
        "wolo_sync_context",
        "Synchronize external work context like calendar events, git commits, PRs, issues, or tool runs to enrich logs.",
        [("source", "string", "Source to sync: all, git, calendar.", False)]
    )


def _tool_visualize() -> ToolDefinition:
    return _definition(
        "wolo_visualize",
        "Generate a visual report of recent work activity. Model can choose the type and time range.",
        [
            ("type", "string", "Type of visualization: emotion_distribution, tag_cloud, activity_heatmap.", False),
            ("days", "integer", "Number of days to analyze (default 30).", False),
        ]
    )


def _tool_export() -> ToolDefinition:
    return _definition(
        "wolo_export",
        "Export wolo work records with optional filtering and AI summary. Model can choose format, date range, and whether to include an AI-generated overview.",
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


def _optional_text(arguments: dict[str, Any], key: str) -> str | None:
    value = str(arguments.get(key) or "").strip()
    return value or None


def _csv_list(value: Any) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _format_records(store: WoloStore, records: list[WoloRecord]) -> str:
    if not records:
        return "暂无 wolo 记录。"
    lines: list[str] = []
    for record in records:
        lines.append(f"- [{record.id}] {record.date} {record.summary or record.raw_content}")
        lines.extend(_format_attachment_refs(store, record))
    return "\n".join(lines)


def _format_record_trace(store: WoloStore, record: WoloRecord, entry: WoloEntry | None) -> str:
    lines = [
        f"record_id={record.id}",
        f"entry_id={record.entry_id}",
        f"date={record.date}",
        f"created_at={record.created_at}",
        f"source={record.source}",
        f"summary={record.summary or record.raw_content}",
    ]
    if entry is not None:
        if entry.channel:
            lines.append(f"channel={entry.channel}")
        if entry.sender_id:
            lines.append(f"sender_id={entry.sender_id}")
        if entry.chat_id:
            lines.append(f"chat_id={entry.chat_id}")
        if entry.message_id:
            lines.append(f"message_id={entry.message_id}")
        source_message = entry.metadata.get("source_message")
        if source_message is not None:
            lines.append(f"source_message={json.dumps(source_message, ensure_ascii=False, sort_keys=True)}")
    lines.append(f"attachments={len(record.attachments)}")
    for attachment in record.attachments:
        lines.append("- " + _format_attachment_line(store, attachment, include_source=True))
    return "\n".join(lines)


def _format_attachment_refs(store: WoloStore, record: WoloRecord) -> list[str]:
    if not record.attachments:
        return []
    lines = [f"  attachments={len(record.attachments)}"]
    for attachment in record.attachments:
        lines.append(f"  - {_format_attachment_line(store, attachment)}")
    return lines


def _format_attachment_line(
    store: WoloStore,
    attachment: StoredAttachment,
    *,
    include_source: bool = False,
) -> str:
    parts = [
        f"kind={attachment.kind}",
        f"name={attachment.original_name or '(unnamed)'}",
    ]
    if attachment.media_type:
        parts.append(f"mime={attachment.media_type}")
    if attachment.size_bytes is not None:
        parts.append(f"size={attachment.size_bytes}")
    parts.append(f"path={store.resolve_attachment_path(attachment)}")
    if include_source:
        parts.append(f"stored_path={attachment.stored_path}")
        parts.append(f"source_path={attachment.source_path}")
    if attachment.sha256:
        parts.append(f"sha256={attachment.sha256}")
    return " ".join(parts)


def _format_todos(todos: list[Any]) -> str:
    if not todos:
        return "暂无匹配待办。"
    return "\n".join(
        f"- [{todo.id}] {todo.status} {todo.priority} {todo.project} {todo.title}".strip()
        for todo in todos
    )


def _format_decisions(decisions: list[Any]) -> str:
    if not decisions:
        return "暂无决策记录。"
    return "\n".join(
        f"- [{decision.id}] {decision.project} {decision.title}"
        f"；原因：{decision.rationale}；影响：{decision.impact}"
        for decision in decisions
    )


def _format_highlights(highlights: list[Any], *, empty: str) -> str:
    if not highlights:
        return empty
    return "\n".join(
        f"- [{item.id}] [{item.kind}] {item.project} {item.title}：{item.content}".strip()
        for item in highlights
    )


def _backfill_hint(store: WoloStore, record_date: object) -> str | None:
    if not record_date:
        return None
    try:
        day = datetime.strptime(str(record_date), "%Y-%m-%d").date()
    except ValueError:
        return None
    yesterday = (day - timedelta(days=1)).isoformat()
    if store.has_activity_on(yesterday):
        return None
    return f"发现昨天（{yesterday}）没有记录。可以回复 `/wolo backfill {yesterday} 具体内容` 补录。"
