"""Self-log domain tools used by the standalone app agent."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from solo.core.attachments import StoredAttachment
from openharness.services.app_reminders import (
    build_one_shot_reminder_schedule,
    format_local_reminder_time,
)
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.bash_tool import BashTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.image_to_text_tool import ImageToTextTool
from openharness.tools.skill_manager_tool import SkillManagerTool
from openharness.utils.log import get_logger

from solo.core.memory import add_memory_entry
from solo.core.models import ProfileUpdate, SoloEntry, SoloRecord
from solo.processor import SoloProcessor
from solo.core.store import SoloStore
from solo.core.utils import (
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
class SoloDomainTool:
    definition: ToolDefinition
    handler: ToolHandler


class SoloToolRegistry:
    """Tool registry for the solo domain."""

    def __init__(
        self,
        store: SoloStore,
        processor: SoloProcessor | None = None,
        agent_factory: Callable[[], Any] | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.processor = processor
        self._agent_factory = agent_factory
        self._source_context = dict(source_context or {})

    def _processor(self) -> SoloProcessor:
        if self.processor is None:
            self.processor = SoloProcessor(
                self.store,
                self._agent_factory() if self._agent_factory is not None else None,
            )
        return self.processor

    def tools(self) -> list[SoloDomainTool]:
        return [
            SoloDomainTool(_tool_record(), self._handle_record),
            SoloDomainTool(_tool_import_records(), self._handle_import_records),
            SoloDomainTool(_tool_clarify(), self._handle_clarify),
            SoloDomainTool(_tool_process(), self._handle_process),
            SoloDomainTool(_tool_backfill(), self._handle_backfill),
            SoloDomainTool(_tool_remind(), self._handle_remind),
            SoloDomainTool(_tool_schedule(), self._handle_schedule),
            SoloDomainTool(_tool_jobs(), self._handle_jobs),
            SoloDomainTool(_tool_cancel(), self._handle_cancel),
            SoloDomainTool(_tool_report(), self._handle_report),
            SoloDomainTool(_tool_view(), self._handle_view),
            SoloDomainTool(_tool_search(), self._handle_search),
            SoloDomainTool(_tool_show(), self._handle_show),
            SoloDomainTool(_tool_todos(), self._handle_todos),
            SoloDomainTool(_tool_done(), self._handle_done),
            SoloDomainTool(_tool_update_todo(), self._handle_update_todo),
            SoloDomainTool(_tool_update_record(), self._handle_update_record),
            SoloDomainTool(_tool_delete_record(), self._handle_delete_record),
            SoloDomainTool(_tool_status(), self._handle_status),
            SoloDomainTool(_tool_get_now(), self._handle_get_now),
            SoloDomainTool(_tool_profile_update(), self._handle_profile_update),
            SoloDomainTool(_tool_remember(), self._handle_remember),
            SoloDomainTool(_tool_suggest_reflection(), self._handle_suggest_reflection),
            SoloDomainTool(_tool_sync_context(), self._handle_sync_context),
            SoloDomainTool(_tool_visualize(), self._handle_visualize),
            SoloDomainTool(_tool_export(), self._handle_export),
            SoloDomainTool(_tool_heartbeat_task(), self._handle_heartbeat_task),
        ]

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [tool.definition.to_api_schema() for tool in self.tools()]

    def to_api_schema(self) -> list[dict[str, Any]]:
        return self.tool_schemas()

    def list_tools(self) -> list[SoloDomainTool]:
        return self.tools()

    def by_name(self) -> dict[str, SoloDomainTool]:
        return {tool.definition.name: tool for tool in self.tools()}

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self.by_name().get(name)
        if tool is None:
            logger.error("execute unknown tool name=%s", name)
            raise ValueError(f"Unknown solo tool: {name}")
        logger.debug("execute tool=%s arguments=%r", name, {k: v for k, v in arguments.items() if k != "content"})
        result = await tool.handler(arguments)
        return str(result.get("message") or result)

    async def _handle_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Record a personal entry, optionally structuring it into a queryable record.

        Two-phase design:
          Phase 1 — Always create a raw entry (guarantees no data loss).
          Phase 2 — If the model provided structured fields (summary, tags, etc.),
                    immediately create a structured record as well.
                    Otherwise, only the raw entry is persisted; it will be structured
                    later by ``process_pending()`` (triggered via solo_process or
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

            record = SoloRecord(
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
            return {
                "ok": True,
                "entry_id": entry.id,
                "record_id": record.id,
                "message": f"✅ 刚才的记录已经入库。record_id={record.id}",
            }

        # No structured fields provided — entry saved but not yet structured.
        # It will be processed by the next `solo_process` / `process_pending()` call.
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

            record = SoloRecord(
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
            created.append(record.id)
        logger.info("_handle_import_records imported=%d", len(created))
        ids_hint = "，可通过 solo_search/view 获取" if len(created) > 5 else "：" + ", ".join(created)
        return {
            "ok": True,
            "record_ids": created,
            "imported": len(created),
            "message": f"已批量入库 {len(created)} 条 solo 记录{ids_hint}。",
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

    async def _handle_remind(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Register a one-shot reminder: only sends a notification message at the scheduled time.

        Counterpart: _handle_schedule (which executes an agent task instead of just notifying).
        Both share _prepare_one_shot for time parsing and daemon management.
        """
        from solo.gateway.todo_cron import schedule_one_shot_reminder

        reminder_message = _required_text(arguments, "message")
        due_at_utc, notify, local_due, delay_text = self._prepare_one_shot(arguments, time_field="remind_at")
        job = schedule_one_shot_reminder(
            "solo",
            workspace=self.store.workspace,
            remind_at=due_at_utc,
            message=reminder_message,
            notify=notify,
        )
        return {
            "ok": True,
            "job_name": job["name"],
            "next_run": job["next_run"],
            "message": f"✅ 已设置提醒：将在 {local_due}（{delay_text}）提醒你：{reminder_message}",
        }

    async def _handle_schedule(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Register a one-shot agent task: runs the full agent loop at the scheduled time and DMs results.

        Counterpart: _handle_remind (which only sends a notification without executing anything).
        Both share _prepare_one_shot for time parsing and daemon management.
        """
        from solo.gateway.todo_cron import schedule_one_shot_agent_task

        task_prompt = _required_text(arguments, "prompt")
        due_at_utc, notify, local_due, delay_text = self._prepare_one_shot(arguments, time_field="run_at")
        job = schedule_one_shot_agent_task(
            "solo",
            workspace=self.store.workspace,
            run_at=due_at_utc,
            prompt=task_prompt,
            notify=notify,
        )
        return {
            "ok": True,
            "job_name": job["name"],
            "next_run": job["next_run"],
            "message": f"✅ 已安排定时任务：将在 {local_due}（{delay_text}）执行「{task_prompt}」并把结果发给你。",
        }

    def _prepare_one_shot(
        self,
        arguments: dict[str, Any],
        *,
        time_field: str,
    ) -> tuple[datetime, dict[str, str], str, str]:
        """Shared preparation for _handle_remind and _handle_schedule.

        Handles the three concerns common to both:
        1. Resolve the Feishu DM notify target from source_context
        2. Parse absolute/relative time into a UTC datetime
        3. Ensure the cron scheduler daemon is running

        Args:
            arguments: Tool call arguments containing delay_* or an absolute time field.
            time_field: Which argument key holds the absolute ISO-8601 time
                        ("remind_at" for remind, "run_at" for schedule).

        Returns:
            (due_at_utc, notify_dict, local_due_str, delay_text)
        """
        notify = _resolve_reminder_notify_target(self._source_context, self.store.workspace)
        if notify is None:
            raise ValueError("此功能目前只支持带 sender_id 的飞书会话。")

        schedule = build_one_shot_reminder_schedule(
            remind_at=_optional_text(arguments, time_field),
            delay_seconds=arguments.get("delay_seconds"),
            delay_minutes=arguments.get("delay_minutes"),
            delay_hours=arguments.get("delay_hours"),
            delay_days=arguments.get("delay_days"),
        )

        from solo.gateway.cron_scheduler import is_scheduler_running, start_daemon

        if not is_scheduler_running():
            start_daemon(self.store.workspace)

        local_due = format_local_reminder_time(schedule.due_at_local)
        return schedule.due_at_utc, notify, local_due, schedule.delay_text

    async def _handle_jobs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from solo.gateway.todo_cron import list_one_shot_jobs

        jobs = list_one_shot_jobs(self.store.workspace)
        if not jobs:
            return {"ok": True, "jobs": [], "message": "当前没有待执行的提醒或定时任务。"}
        lines = []
        for job in jobs:
            payload = job.get("payload") or {}
            kind = str(payload.get("kind") or "unknown")
            content = str(payload.get("message") or "")
            next_run_str = str(job.get("next_run") or "")
            try:
                local_due = format_local_reminder_time(
                    datetime.fromisoformat(next_run_str).astimezone()
                )
            except (ValueError, TypeError):
                local_due = next_run_str
            label = "提醒" if kind == "reminder" else "定时任务"
            lines.append(f"• [{label}] {content}  ⏰ {local_due}  (job_name: {job['name']})")
        return {
            "ok": True,
            "jobs": [{"name": j["name"], "kind": (j.get("payload") or {}).get("kind"), "next_run": j.get("next_run")} for j in jobs],
            "message": "待执行的任务：\n" + "\n".join(lines),
        }

    async def _handle_cancel(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from solo.gateway.todo_cron import delete_cron_job

        job_name = _required_text(arguments, "job_name")
        deleted = delete_cron_job(job_name, self.store.workspace)
        if deleted:
            return {"ok": True, "message": f"✅ 已取消任务：{job_name}"}
        return {"ok": False, "message": f"未找到任务 {job_name!r}，可能已执行或不存在。"}

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
        category = _optional_text(arguments, "category")
        limit = int(arguments.get("limit") or 20)
        todos = self.store.list_todos(status=status, category=category, limit=limit)
        return {
            "ok": True,
            "todos": [todo.to_dict() for todo in todos],
            "message": _format_todos(todos),
        }

    async def _handle_done(self, arguments: dict[str, Any]) -> dict[str, Any]:
        todo_id = _required_text(arguments, "todo_id")
        todo = self.store.get_todo(todo_id)
        if todo is None:
            return {"ok": False, "message": f"未找到待办：{todo_id}"}
        if not self.store.complete_todo(todo_id):
            return {"ok": False, "message": f"待办已完成或无法更新：{todo_id}"}
        parts = [f"「{todo.title}」"]
        if todo.category:
            parts.append(f"分类：{todo.category}")
        if todo.priority:
            parts.append(f"优先级：{todo.priority}")
        if todo.due_date:
            parts.append(f"截止：{todo.due_date}")
        detail = "，".join(parts)
        return {
            "ok": True,
            "todo": todo.to_dict(),
            "message": f"✅ 待办已完成：{detail}。请用自然语言向用户确认这条待办已完成，简要提及标题和分类。",
            "notify_user": True,
        }

    async def _handle_update_todo(self, arguments: dict[str, Any]) -> dict[str, Any]:
        todo_id = _required_text(arguments, "todo_id")
        updates: dict[str, Any] = {}
        for field in ["title", "category", "priority", "due_date", "status"]:
            if field in arguments and arguments[field] is not None:
                updates[field] = arguments[field]
        if not updates:
            return {"ok": False, "message": "未提供任何更新字段。"}
        if not self.store.update_todo(todo_id, **updates):
            return {"ok": False, "message": f"未找到待办：{todo_id}"}
        return {"ok": True, "message": f"✅ 已更新待办：{todo_id}"}

    async def _handle_update_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing solo record."""
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
        """Permanently delete an existing solo record."""
        record_id = _required_text(arguments, "record_id")
        success = self.store.delete_record(record_id)
        if success:
            return {"ok": True, "message": f"🗑️ 已永久删除记录 {record_id}。"}
        else:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}

    async def _handle_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = self.store.status()
        message = (
            f"solo 状态：entries={status['entries']}，records={status['records']}，"
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
            context_items.append("- [Git] 提交了 solo 架构优化代码")

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

    async def _handle_heartbeat_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Manage periodic tasks in HEARTBEAT.md."""
        action = _required_text(arguments, "action")
        hb_path = self.store.workspace / "HEARTBEAT.md"

        if action == "list":
            if not hb_path.exists():
                return {"ok": True, "tasks": [], "message": "HEARTBEAT.md 不存在，暂无周期性任务。"}
            lines = _read_heartbeat_tasks(hb_path)
            return {"ok": True, "tasks": lines, "message": "\n".join(lines) if lines else "暂无周期性任务。"}

        if action == "add":
            task = _required_text(arguments, "task")
            _ensure_heartbeat_file(hb_path)
            lines = _read_heartbeat_tasks(hb_path)
            if any(task.strip("- ") in ln for ln in lines):
                return {"ok": False, "message": f"任务已存在：{task}"}
            lines.append(f"- {task.strip('- ')}")
            _write_heartbeat_tasks(hb_path, lines)
            return {"ok": True, "message": f"✅ 已添加周期性任务：{task}"}

        if action == "remove":
            keyword = _required_text(arguments, "task")
            if not hb_path.exists():
                return {"ok": False, "message": "HEARTBEAT.md 不存在。"}
            lines = _read_heartbeat_tasks(hb_path)
            remaining = [ln for ln in lines if keyword not in ln]
            if len(remaining) == len(lines):
                return {"ok": False, "message": f"未找到匹配「{keyword}」的任务。"}
            removed_count = len(lines) - len(remaining)
            _write_heartbeat_tasks(hb_path, remaining)
            return {"ok": True, "message": f"✅ 已移除 {removed_count} 条匹配「{keyword}」的任务。"}

        if action == "update":
            old_keyword = _required_text(arguments, "task")
            new_task = str(arguments.get("new_task") or "").strip()
            if not new_task:
                raise ValueError("update 操作需要提供 new_task 参数。")
            if not hb_path.exists():
                return {"ok": False, "message": "HEARTBEAT.md 不存在。"}
            lines = _read_heartbeat_tasks(hb_path)
            updated = False
            for i, line in enumerate(lines):
                if old_keyword in line:
                    lines[i] = f"- {new_task.strip('- ')}"
                    updated = True
                    break
            if not updated:
                return {"ok": False, "message": f"未找到匹配「{old_keyword}」的任务。"}
            _write_heartbeat_tasks(hb_path, lines)
            return {"ok": True, "message": f"✅ 已更新任务：{new_task}"}

        return {"ok": False, "message": f"未知操作：{action}，支持 add/remove/update/list"}


class _AnyInput(BaseModel):
    """Permissive Pydantic model that accepts any tool arguments as extra fields."""

    model_config = ConfigDict(extra="allow")


class _SoloToolAdapter(BaseTool):
    """Thin BaseTool wrapper around a SoloDomainTool handler."""

    input_model = _AnyInput

    def __init__(self, domain_tool: SoloDomainTool) -> None:
        self.name = domain_tool.definition.name  # type: ignore[misc]
        self.description = domain_tool.definition.description  # type: ignore[misc]
        self._domain_tool = domain_tool

    def to_api_schema(self) -> dict[str, Any]:
        return self._domain_tool.definition.to_api_schema()

    def is_read_only(self, arguments: BaseModel) -> bool:
        return self.name in {"solo_view", "solo_search", "solo_show", "solo_status"}

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        raw = arguments.model_dump()
        try:
            result = await self._domain_tool.handler(raw)
            return ToolResult(output=str(result.get("message") or result))
        except Exception as exc:
            return ToolResult(output=str(exc), is_error=True)


def build_oh_registry(registry: SoloToolRegistry) -> ToolRegistry:
    """Build an OpenHarness ToolRegistry from a SoloToolRegistry."""
    oh_registry = ToolRegistry()
    for domain_tool in registry.tools():
        oh_registry.register(_SoloToolAdapter(domain_tool))
    oh_registry.register(BashTool())
    oh_registry.register(FileReadTool())
    oh_registry.register(ImageToTextTool())
    oh_registry.register(SkillManagerTool())
    return oh_registry


def _tool_record() -> ToolDefinition:
    return _definition(
        "solo_record",
        (
            "Record a solo entry when the intent and core content are clear enough to understand. "
            "Do NOT call this when the user's intent is ambiguous or the record is unintelligible — "
            "call solo_clarify instead. Fill in structured fields (summary, tags, emotion, etc.) "
            "based on your understanding of the content."
        ),
        [
            ("content", "string", "Original solo content as the user wrote it.", True),
            ("corrected_content", "string", "Lightly corrected / cleaned-up version of the content.", False),
            ("summary", "string", "One-sentence summary.", False),
            ("tags", "string", "Comma-separated tags.", False),
            ("emotion", "string", "Emotion label: 积极/消极/中性/复杂.", False),
            ("date", "string", "YYYY-MM-DD. Only provide this if the user explicitly mentions a specific date (e.g. '昨天', '5月18日', '上周三'). If no date is mentioned, leave this empty and the system will default to today's local date.", False),
            ("period", "string", "Semantic time period extracted from content (e.g. 凌晨, 上午).", False),
            ("events", "string", "Holidays, anniversaries, or birthdays.", False),
            ("emotion_reason", "string", "Brief reason for the emotion label.", False),
            ("related_people", "string", "Comma-separated people mentioned.", False),
            ("related_places", "string", "Comma-separated places mentioned.", False),
            ("source", "string", "Record source, e.g. 原始/补录.", False),
        ],
    )


def _tool_import_records() -> ToolDefinition:
    return ToolDefinition(
        name="solo_import_records",
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
        "solo_clarify",
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
        "solo_process",
        "Process pending solo entries and reminders.",
        [
            ("limit", "integer", "Maximum pending entries to process.", False),
            ("backfill_missing_yesterday", "boolean", "Whether to check yesterday.", False),
        ],
    )


def _tool_backfill() -> ToolDefinition:
    return _definition(
        "solo_backfill",
        (
            "Quick-path for backfilling a missing personal entry when the user provides raw content without structured fields. "
            "Saves the entry and auto-triggers process_pending to structure it via LLM. "
            "Use solo_record instead if you can already extract structured fields (summary, tags, emotion)."
        ),
        [("content", "string", "Backfill content.", True)],
    )


def _tool_report() -> ToolDefinition:
    return _definition(
        "solo_report",
        "Generate weekly, monthly, or yearly solo report.",
        [("type", "string", "weekly/monthly/yearly.", True)],
    )


def _tool_remind() -> ToolDefinition:
    return _definition(
        "solo_remind",
        (
            "Schedule a one-shot reminder that sends a notification to the user at a future time. "
            "The system only SENDS A MESSAGE — it does NOT execute any task. "
            "Use for requests like '2分钟后提醒我喝水' or '明天 09:30 提醒我去运动'. "
            "If the user wants the system to DO something and return results, use solo_schedule instead. "
            "For recurring/periodic reminders, use solo_heartbeat_task instead. "
            "Provide either remind_at (ISO-8601 datetime) or one/more delay_* fields."
        ),
        [
            ("message", "string", "What to remind the user about, e.g. 喝水 / 休息一下 / 出门前带钥匙.", True),
            ("remind_at", "string", "Absolute reminder time as ISO-8601 datetime. Use this for explicit future timestamps.", False),
            ("delay_seconds", "integer", "Relative delay in seconds for very short reminders.", False),
            ("delay_minutes", "integer", "Relative delay in minutes, e.g. 2 for '2分钟后'.", False),
            ("delay_hours", "integer", "Relative delay in hours.", False),
            ("delay_days", "integer", "Relative delay in days.", False),
        ],
    )


def _tool_schedule() -> ToolDefinition:
    return _definition(
        "solo_schedule",
        (
            "Schedule a one-shot agent task that EXECUTES at a future time and DMs the result to the user. "
            "The system will actually perform the work (e.g. generate a report, summarize logs) — not just remind. "
            "Use for requests like '明天12点生成一份周报' or '下午3点帮我整理这周的日志'. "
            "If the user only needs a notification without execution, use solo_remind instead. "
            "For recurring/periodic tasks, use solo_heartbeat_task instead. "
            "Provide either run_at (ISO-8601 datetime) or one/more delay_* fields."
        ),
        [
            ("prompt", "string", "The task prompt for the agent to execute at the scheduled time, e.g. 生成本周总结 / 整理今天的日志.", True),
            ("run_at", "string", "Absolute run time as ISO-8601 datetime.", False),
            ("delay_seconds", "integer", "Relative delay in seconds.", False),
            ("delay_minutes", "integer", "Relative delay in minutes.", False),
            ("delay_hours", "integer", "Relative delay in hours.", False),
            ("delay_days", "integer", "Relative delay in days.", False),
        ],
    )


def _tool_jobs() -> ToolDefinition:
    return _definition(
        "solo_jobs",
        "List all pending one-shot reminders and scheduled tasks (not yet executed). Use before cancelling to get job names.",
        [],
    )


def _tool_cancel() -> ToolDefinition:
    return _definition(
        "solo_cancel",
        (
            "Cancel a pending one-shot reminder or scheduled task by job name. "
            "Use solo_jobs first to get the job name. "
            "Use for requests like '取消刚才的提醒' or '我不想要那个定时任务了'."
        ),
        [
            ("job_name", "string", "Name of the job to cancel (from solo_jobs).", True),
        ],
    )


def _tool_view() -> ToolDefinition:
    return _definition(
        "solo_view",
        "Browse the most recent solo records in reverse-chronological order. Use for quick 'what did I log lately' checks without any filter criteria. For filtered queries, use solo_search instead.",
        [("limit", "integer", "Number of records (default 10).", False)],
    )


def _tool_search() -> ToolDefinition:
    return _definition(
        "solo_search",
        (
            "Search through solo records with precise filters (keywords, date range, tags, emotions). "
            "Use this for targeted lookups like 'find all records tagged 健康 in May' or 'records mentioning 小李'. "
            "Also use for open-ended retrospective questions like 'how have I been feeling lately' since solo has no separate aggregation tool."
        ),
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
        "solo_show",
        "Show one solo record with linked attachment paths and source-message trace data.",
        [("record_id", "string", "The ID of the record to inspect.", True)],
    )


def _tool_todos() -> ToolDefinition:
    return _definition(
        "solo_todos",
        "List personal todos derived from solo records, optionally filtered by status or category.",
        [
            ("status", "string", "Todo status: pending/done. Defaults to pending.", False),
            ("category", "string", "Category filter (健康/家庭/社交/购物/学习/其他).", False),
            ("limit", "integer", "Number of todos.", False),
        ],
    )


def _tool_done() -> ToolDefinition:
    return _definition(
        "solo_done",
        "Mark a personal todo as done by todo_id.",
        [("todo_id", "string", "The todo ID to complete.", True)],
    )


def _tool_update_todo() -> ToolDefinition:
    return _definition(
        "solo_update_todo",
        "Update a personal todo's fields (title, category, priority, due_date, or status).",
        [
            ("todo_id", "string", "The todo ID to update.", True),
            ("title", "string", "New title.", False),
            ("category", "string", "New category.", False),
            ("priority", "string", "New priority (high/medium/low).", False),
            ("due_date", "string", "New due date (YYYY-MM-DD or empty).", False),
            ("status", "string", "New status (pending/in_progress/done/cancelled).", False),
        ],
    )


def _tool_update_record() -> ToolDefinition:
    return _definition(
        "solo_update_record",
        "Modify an existing structured record. Use this to fix mistakes in summary, tags, emotions, or content.",
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
        "solo_delete_record",
        (
            "PERMANENTLY DELETE an existing record. Use this with EXTREME CAUTION. "
            "Only call this when the user explicitly asks to delete a specific record by ID or content. "
            "This action is IRREVERSIBLE."
        ),
        [("record_id", "string", "The ID of the record to delete.", True)],
    )


def _tool_status() -> ToolDefinition:
    return _definition("solo_status", "Show solo status.", [])


def _tool_get_now() -> ToolDefinition:
    return _definition(
        "solo_get_now",
        "Get the current local date, time, and timezone information.",
        []
    )


def _tool_profile_update() -> ToolDefinition:
    return _definition(
        "solo_profile_update",
        (
            "Store a suggested update for TRANSIENT or evolving user profile info that may change within weeks or months "
            "(e.g. current preferences, temporary habits, mood patterns, seasonal routines). "
            "These are reviewed and may expire. "
            "For STABLE life facts expected to last years (family, career milestones, medical history), use solo_remember instead."
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
        "solo_remember",
        (
            "Store STABLE, core life facts into the long-term memory directory — information expected to remain valid for years "
            "(e.g. family trees, medical history, career milestones, home location, chronic conditions). "
            "These facts serve as foundation context for all future sessions. "
            "For transient/evolving info that may change within months (current habits, temporary preferences), use solo_profile_update instead."
        ),
        [
            ("title", "string", "A short English title for this memory entry (used as filename, ASCII only, e.g. 'family_members', 'medical_history').", True),
            ("content", "string", "The markdown content to store. Be factual and concise.", True),
        ],
    )


def _tool_suggest_reflection() -> ToolDefinition:
    return _definition(
        "solo_suggest_reflection",
        "Suggest deep reflection questions based on recent solo history. The model can provide a focus area or a specific style.",
        [
            ("focus", "string", "Specific area to focus on (e.g. 'work stress', 'family relationships').", False),
            ("style", "string", "Style of the questions (e.g. 'challenging', 'supportive', 'philosophical').", False),
        ]
    )


def _tool_sync_context() -> ToolDefinition:
    return _definition(
        "solo_sync_context",
        "Synchronize external context like calendar events or git commits to enrich logs.",
        [("source", "string", "Source to sync: all, git, calendar.", False)]
    )


def _tool_visualize() -> ToolDefinition:
    return _definition(
        "solo_visualize",
        "Generate a visual report of recent activity. Model can choose the type and time range.",
        [
            ("type", "string", "Type of visualization: emotion_distribution, tag_cloud, activity_heatmap.", False),
            ("days", "integer", "Number of days to analyze (default 30).", False),
        ]
    )


def _tool_export() -> ToolDefinition:
    return _definition(
        "solo_export",
        "Export solo records with optional filtering and AI summary. Model can choose format, date range, and whether to include an AI-generated overview.",
        [
            ("format", "string", "Export format: markdown, json.", False),
            ("start_date", "string", "YYYY-MM-DD.", False),
            ("end_date", "string", "YYYY-MM-DD.", False),
            ("include_summary", "boolean", "Whether to include an AI-generated summary at the top of the export.", False),
        ]
    )


def _tool_heartbeat_task() -> ToolDefinition:
    return _definition(
        "solo_heartbeat_task",
        (
            "Manage periodic/recurring heartbeat tasks in HEARTBEAT.md. These tasks are automatically "
            "executed by the heartbeat watchdog every 30 minutes — use for RECURRING checks only. "
            "Examples: '每小时提醒我站起来活动', '检查有没有未读重要消息', '看看天气预报'. "
            "For ONE-TIME reminders, use solo_remind instead. "
            "For ONE-TIME scheduled tasks, use solo_schedule instead. "
            "Actions: add (add a new periodic task), remove (remove by keyword match), "
            "update (replace an existing task), list (show all current tasks)."
        ),
        [
            ("action", "string", "One of: add, remove, update, list.", True),
            ("task", "string", "The task content (for add: new task text; for remove/update: keyword to match existing task).", False),
            ("new_task", "string", "New task text when action=update (replaces the matched task).", False),
        ],
    )


def _read_heartbeat_tasks(path: Path) -> list[str]:
    """Read task lines from HEARTBEAT.md, ignoring comments and blank lines."""
    content = path.read_text(encoding="utf-8", errors="replace")
    return [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("<!--")
    ]


def _write_heartbeat_tasks(path: Path, lines: list[str]) -> None:
    """Write task lines back to HEARTBEAT.md."""
    content = "\n".join(lines) + "\n" if lines else ""
    path.write_text(content, encoding="utf-8")


def _ensure_heartbeat_file(path: Path) -> None:
    """Create HEARTBEAT.md if it doesn't exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


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


def _resolve_reminder_notify_target(
    source_context: dict[str, Any],
    workspace: str | Path | None,
) -> dict[str, str] | None:
    channel = str(source_context.get("channel") or "").strip().lower()
    sender_id = str(source_context.get("sender_id") or "").strip()
    if channel != "feishu" or not sender_id:
        return None
    return {
        "type": "feishu_dm",
        "user_open_id": sender_id,
        "workspace": str(workspace),
    }


def _format_records(store: SoloStore, records: list[SoloRecord]) -> str:
    if not records:
        return "暂无 solo 记录。"
    lines: list[str] = []
    for record in records:
        lines.append(f"- [{record.id}] {record.date} {record.summary or record.raw_content}")
        lines.extend(_format_attachment_refs(store, record))
    return "\n".join(lines)


def _format_todos(todos: list[Any]) -> str:
    if not todos:
        return "暂无匹配待办。"
    return "\n".join(
        f"- [{todo.id}] {todo.status} {todo.priority} {todo.category} {todo.title}".strip()
        for todo in todos
    )


def _format_record_trace(store: SoloStore, record: SoloRecord, entry: SoloEntry | None) -> str:
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


def _format_attachment_refs(store: SoloStore, record: SoloRecord) -> list[str]:
    if not record.attachments:
        return []
    lines = [f"  attachments={len(record.attachments)}"]
    for attachment in record.attachments:
        lines.append(f"  - {_format_attachment_line(store, attachment)}")
    return lines


def _format_attachment_line(
    store: SoloStore,
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


def _backfill_hint(store: SoloStore, record_date: object) -> str | None:
    if not record_date:
        return None
    try:
        day = datetime.strptime(str(record_date), "%Y-%m-%d").date()
    except ValueError:
        return None
    yesterday = (day - timedelta(days=1)).isoformat()
    if store.has_activity_on(yesterday):
        return None
    return f"发现昨天（{yesterday}）没有记录。可以回复 `/solo backfill {yesterday} 具体内容` 补录。"
