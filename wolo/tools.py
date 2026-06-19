"""Work-log domain tools used by the standalone app agent."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from wolo.core.attachments import StoredAttachment
from openharness.services.app_reminders import (
    build_one_shot_reminder_schedule,
    format_local_reminder_time,
)
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.bash_tool import BashTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.image_to_text_tool import ImageToTextTool
from openharness.tools.skill_delete_tool import SkillDeleteTool
from openharness.tools.skill_list_tool import SkillListTool
from openharness.tools.skill_load_tool import SkillLoadTool
from openharness.tools.skill_patch_tool import SkillPatchTool
from openharness.tools.skill_search_tool import SkillSearchTool
from openharness.tools.skill_write_tool import SkillWriteTool
from openharness.utils.log import get_logger

from common.constants import EMOTION_MAX_LENGTH, SUMMARY_MAX_LENGTH
from wolo.core.artifacts import persist_work_artifacts
from wolo.core.memory import add_memory_entry
from wolo.core.models import (
    ProfileUpdate,
    Project,
    ProjectAlias,
    ProjectLink,
    ProjectSnapshot,
    ProjectSuggestion,
    WoloDecision,
    WoloEntry,
    WoloExperiment,
    WoloHighlight,
    WoloRecord,
    WoloTodo,
)
from wolo.commands import format_wolo_llm_usage
from wolo.processor import WoloProcessor
from wolo.core.store import WoloStore
from wolo.core.utils import (
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


# Feature-flag names read via os.environ at handler time.
_WOLO_DISABLE_SAME_TURN_UPDATE_GUARD = "WOLO_DISABLE_SAME_TURN_UPDATE_GUARD"
_WOLO_DISABLE_HALLUCINATION_GUARD = "WOLO_DISABLE_HALLUCINATION_GUARD"

# Recency window (seconds) used by Layer-4 hallucination guard when Layer-2
# is disabled via flag — records newer than this are considered "fresh".
SAME_TURN_RECENT_WINDOW_SECONDS = 180

# Subjective / inferential fields the model must NOT patch in via update on a
# freshly-created record. These must come from the user's own words in the
# original *_record call, or remain empty / neutral.
SUBJECTIVE_UPDATE_FIELDS: frozenset[str] = frozenset(
    {
        "emotion",
        "emotion_reason",
        "sample_type",
        "problem_essence",
        "available_cards",
        "strategy",
        "next_move",
        "deadline",
        "validation_signal",
    }
)


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
        progress_callback: Callable[[str], Any] | None = None,
    ) -> None:
        self.store = store
        self.processor = processor
        self._agent_factory = agent_factory
        self._source_context = dict(source_context or {})
        self._progress_callback = progress_callback
        self._background_tasks: set[Any] = set()
        self._created_record_ids: set[str] = set()

    def _processor(self) -> WoloProcessor:
        if self.processor is None:
            self.processor = WoloProcessor(
                self.store,
                self._agent_factory() if self._agent_factory is not None else None,
            )
        return self.processor

    async def _push_progress(self, text: str) -> None:
        if self._progress_callback is None:
            return
        try:
            import asyncio

            coro = self._progress_callback(text)
            if asyncio.iscoroutine(coro):
                await coro
        except Exception:
            pass

    def tools(self) -> list[WoloDomainTool]:
        return [
            WoloDomainTool(_tool_record(), self._handle_record),
            WoloDomainTool(_tool_import_records(), self._handle_import_records),
            WoloDomainTool(_tool_clarify(), self._handle_clarify),
            WoloDomainTool(_tool_process(), self._handle_process),
            WoloDomainTool(_tool_backfill(), self._handle_backfill),
            WoloDomainTool(_tool_remind(), self._handle_remind),
            WoloDomainTool(_tool_schedule(), self._handle_schedule),
            WoloDomainTool(_tool_jobs(), self._handle_jobs),
            WoloDomainTool(_tool_cancel(), self._handle_cancel),
            WoloDomainTool(_tool_report(), self._handle_report),
            WoloDomainTool(_tool_report_list(), self._handle_report_list),
            WoloDomainTool(_tool_report_show(), self._handle_report_show),
            WoloDomainTool(_tool_report_delete(), self._handle_report_delete),
            WoloDomainTool(_tool_report_update(), self._handle_report_update),
            WoloDomainTool(_tool_report_search(), self._handle_report_search),
            WoloDomainTool(_tool_view(), self._handle_view),
            WoloDomainTool(_tool_search(), self._handle_search),
            WoloDomainTool(_tool_show(), self._handle_show),
            WoloDomainTool(_tool_todos(), self._handle_todos),
            WoloDomainTool(_tool_experiments(), self._handle_experiments),
            WoloDomainTool(_tool_done(), self._handle_done),
            WoloDomainTool(_tool_update_todo(), self._handle_update_todo),
            WoloDomainTool(_tool_blockers(), self._handle_blockers),
            WoloDomainTool(_tool_decisions(), self._handle_decisions),
            WoloDomainTool(_tool_highlights(), self._handle_highlights),
            WoloDomainTool(_tool_add_todo(), self._handle_add_todo),
            WoloDomainTool(_tool_add_decision(), self._handle_add_decision),
            WoloDomainTool(_tool_add_highlight(), self._handle_add_highlight),
            WoloDomainTool(_tool_add_experiment(), self._handle_add_experiment),
            WoloDomainTool(_tool_work_query(), self._handle_work_query),
            WoloDomainTool(_tool_patterns(), self._handle_patterns),
            WoloDomainTool(_tool_playbook(), self._handle_playbook),
            WoloDomainTool(_tool_update_record(), self._handle_update_record),
            WoloDomainTool(_tool_delete_record(), self._handle_delete_record),
            WoloDomainTool(_tool_status(), self._handle_status),
            WoloDomainTool(_tool_llm_usage(), self._handle_llm_usage),
            WoloDomainTool(_tool_get_now(), self._handle_get_now),
            WoloDomainTool(_tool_profile_update(), self._handle_profile_update),
            WoloDomainTool(_tool_remember(), self._handle_remember),
            WoloDomainTool(_tool_suggest_reflection(), self._handle_suggest_reflection),
            WoloDomainTool(_tool_sync_context(), self._handle_sync_context),
            WoloDomainTool(_tool_visualize(), self._handle_visualize),
            WoloDomainTool(_tool_export(), self._handle_export),
            WoloDomainTool(_tool_heartbeat_task(), self._handle_heartbeat_task),
            WoloDomainTool(_tool_fetch_digest(), self._handle_fetch_digest),
            WoloDomainTool(_tool_projects(), self._handle_projects),
            WoloDomainTool(_tool_project_scan(), self._handle_project_scan),
            WoloDomainTool(_tool_project_create(), self._handle_project_create),
            WoloDomainTool(_tool_project_suggestions(), self._handle_project_suggestions),
            WoloDomainTool(_tool_project_review(), self._handle_project_review),
            WoloDomainTool(_tool_project_detail(), self._handle_project_detail),
            WoloDomainTool(_tool_project_update(), self._handle_project_update),
            WoloDomainTool(_tool_project_delete(), self._handle_project_delete),
            WoloDomainTool(_tool_project_complete(), self._handle_project_complete),
            WoloDomainTool(_tool_project_archive(), self._handle_project_archive),
            WoloDomainTool(_tool_project_reactivate(), self._handle_project_reactivate),
            WoloDomainTool(_tool_milestone_create(), self._handle_milestone_create),
            WoloDomainTool(_tool_milestone_update(), self._handle_milestone_update),
            WoloDomainTool(_tool_milestone_complete(), self._handle_milestone_complete),
            WoloDomainTool(_tool_milestone_delete(), self._handle_milestone_delete),
            WoloDomainTool(_tool_project_link_create(), self._handle_project_link_create),
            WoloDomainTool(_tool_project_link_delete(), self._handle_project_link_delete),
            WoloDomainTool(_tool_project_alias_create(), self._handle_project_alias_create),
            WoloDomainTool(_tool_project_link_backfill(), self._handle_project_link_backfill),
            WoloDomainTool(_tool_project_snapshot_create(), self._handle_project_snapshot_create),
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
            self._created_record_ids.add(record.id)

            # Auto-link to project if LLM specified one
            linked_project_name = _optional_text(arguments, "linked_project")
            link_info = None
            if linked_project_name:
                matched_project = _resolve_project_by_name(self.store, linked_project_name)
                if matched_project:
                    link = ProjectLink(
                        id=uuid4().hex[:12],
                        project_id=matched_project.id,
                        entity_type="record",
                        entity_id=record.id,
                        source="ai_high_confidence",
                        confidence="high",
                        status="active",
                        created_at=_now(),
                        updated_at=_now(),
                    )
                    try:
                        self.store.create_project_link(link)
                        link_info = {"project_id": matched_project.id, "project_title": matched_project.title}
                    except Exception:
                        pass

            message = "收到～已记下这条。"
            if link_info:
                message += f" 已关联到项目「{link_info['project_title']}」。"

            result: dict[str, Any] = {
                "ok": True,
                "entry_id": entry.id,
                "record_id": record.id,
                "message": message,
                "_metadata": {
                    "app": "wolo",
                    "domain_event": "record_created",
                    "record_ids": [record.id],
                },
            }
            if link_info:
                result["linked_project"] = link_info
            return result

        # No structured fields provided — entry saved but not yet structured.
        # It will be processed by the next `wolo_process` / `process_pending()` call.
        backfill_hint = _backfill_hint(self.store, arguments.get("record_date") or arguments.get("date"))
        message = "收到～已记下这条。"
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
        self._created_record_ids.update(created)
        logger.info("_handle_import_records imported=%d", len(created))
        ids_hint = "，可通过 wolo_search/view 获取" if len(created) > 5 else "：" + ", ".join(created)
        return {
            "ok": True,
            "record_ids": created,
            "imported": len(created),
            "message": f"收到～已记下 {len(created)} 条{ids_hint}。",
            "_metadata": {
                "app": "wolo",
                "domain_event": "records_imported",
                "record_ids": created,
            },
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
        from wolo.gateway.todo_cron import schedule_one_shot_reminder

        reminder_message = _required_text(arguments, "message")
        due_at_utc, notify, local_due, delay_text = self._prepare_one_shot(arguments, time_field="remind_at")
        session_key = str(self._source_context.get("session_key") or "").strip()
        job = schedule_one_shot_reminder(
            "wolo",
            workspace=self.store.workspace,
            remind_at=due_at_utc,
            message=reminder_message,
            notify=notify,
            session_key=session_key,
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
        from wolo.gateway.todo_cron import schedule_one_shot_agent_task

        task_prompt = _required_text(arguments, "prompt")
        due_at_utc, notify, local_due, delay_text = self._prepare_one_shot(arguments, time_field="run_at")
        job = schedule_one_shot_agent_task(
            "wolo",
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

        from wolo.gateway.cron_scheduler import is_scheduler_running, start_daemon

        if not is_scheduler_running():
            start_daemon(self.store.workspace)

        local_due = format_local_reminder_time(schedule.due_at_local)
        return schedule.due_at_utc, notify, local_due, schedule.delay_text

    async def _handle_jobs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from wolo.gateway.todo_cron import list_one_shot_jobs

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
        from wolo.gateway.todo_cron import delete_cron_job

        job_name = _required_text(arguments, "job_name")
        deleted = delete_cron_job(job_name, self.store.workspace)
        if deleted:
            return {"ok": True, "message": f"✅ 已取消任务：{job_name}"}
        return {"ok": False, "message": f"未找到任务 {job_name!r}，可能已执行或不存在。"}

    async def _handle_report(self, arguments: dict[str, Any]) -> dict[str, Any]:
        report_type = str(arguments.get("report_type") or arguments.get("type") or "weekly")
        start_date = _optional_text(arguments, "start_date")
        end_date = _optional_text(arguments, "end_date")
        report = await self._processor().generate_report(
            report_type, start_date=start_date, end_date=end_date,
        )
        return {"ok": True, "report_type": report_type, "content": report.content, "message": report.content}

    async def _handle_report_list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        report_type = _optional_text(arguments, "type")
        reports = self.store.list_reports()
        if report_type:
            reports = [r for r in reports if r.report_type == report_type]
        reports.sort(key=lambda r: r.created_at, reverse=True)
        if not reports:
            return {"ok": True, "reports": [], "message": "No reports found."}
        lines = [f"[{r.id}] {r.report_type} — {r.created_at}" for r in reports]
        return {"ok": True, "reports": [{"id": r.id, "type": r.report_type, "created_at": r.created_at} for r in reports], "message": "\n".join(lines)}

    async def _handle_report_show(self, arguments: dict[str, Any]) -> dict[str, Any]:
        report_id = _required_text(arguments, "report_id")
        report = self.store.get_report(report_id)
        if not report:
            return {"ok": False, "message": f"Report {report_id} not found."}
        return {"ok": True, "report_type": report.report_type, "created_at": report.created_at, "content": report.content, "message": f"# {report.report_type} report ({report.created_at})\n\n{report.content or '(empty)'}"}

    async def _handle_report_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        report_id = _required_text(arguments, "report_id")
        deleted = self.store.delete_report(report_id)
        if not deleted:
            return {"ok": False, "message": f"Report {report_id} not found."}
        return {"ok": True, "message": f"Deleted report {report_id}."}

    async def _handle_report_update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        report_id = _required_text(arguments, "report_id")
        content = _required_text(arguments, "content")
        report = self.store.get_report(report_id)
        if not report:
            return {"ok": False, "message": f"Report {report_id} not found."}
        self.store.update_report(report_id, content)
        return {"ok": True, "message": f"Updated report {report_id}."}

    async def _handle_report_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        keyword = _required_text(arguments, "keyword")
        reports = self.store.list_reports()
        matches = [r for r in reports if keyword.lower() in (r.content or "").lower()]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        if not matches:
            return {"ok": True, "reports": [], "message": f"No reports matching '{keyword}'."}
        lines = [f"[{r.id}] {r.report_type} — {r.created_at}" for r in matches]
        return {"ok": True, "reports": [{"id": r.id, "type": r.report_type, "created_at": r.created_at} for r in matches], "message": "\n".join(lines)}

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

    async def _handle_experiments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = str(arguments.get("status") or "active").strip().lower()
        project = _optional_text(arguments, "project")
        query = _optional_text(arguments, "query")
        limit = int(arguments.get("limit") or 20)
        experiments = self.store.list_experiments(
            status=None if status in {"", "all"} else status,
            project=project,
            query=query,
            limit=limit,
        )
        return {
            "ok": True,
            "experiments": [item.to_dict() for item in experiments],
            "message": _format_experiments(experiments),
        }

    async def _handle_done(self, arguments: dict[str, Any]) -> dict[str, Any]:
        todo_id = _required_text(arguments, "todo_id")
        todo = self.store.get_todo(todo_id)
        if todo is None:
            return {"ok": False, "message": f"未找到待办：{todo_id}"}
        if not self.store.complete_todo(todo_id):
            return {"ok": False, "message": f"待办已完成或无法更新：{todo_id}"}
        parts = [f"「{todo.title}」"]
        if todo.project:
            parts.append(f"项目：{todo.project}")
        if todo.priority:
            parts.append(f"优先级：{todo.priority}")
        if todo.due_date:
            parts.append(f"截止：{todo.due_date}")
        detail = "，".join(parts)
        return {
            "ok": True,
            "todo": todo.to_dict(),
            "message": f"✅ 已完成待办：{detail}。请用自然语言向用户确认这条待办已完成，简要提及标题和所属项目。",
            "notify_user": True,
        }

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

    async def _handle_add_todo(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Create a single work todo item.

        Use for user-stated action items (e.g. "这周要完成 X", "明天约 Y 对齐").
        Call once per distinct todo; link to the originating record via record_id.
        """
        title = _required_text(arguments, "title")
        todo = WoloTodo(
            id=uuid4().hex[:12],
            record_id=str(arguments.get("record_id") or ""),
            title=title,
            project=str(arguments.get("project") or ""),
            priority=str(arguments.get("priority") or "medium"),
            due_date=str(arguments.get("due_date") or ""),
            status="pending",
            source="explicit",
            created_at=_now(),
        )
        self.store.add_todo(todo)
        return {
            "ok": True,
            "todo_id": todo.id,
            "message": f"✅ 已添加待办：{title}",
            "_metadata": {
                "app": "wolo",
                "domain_event": "todo_created",
                "todo_ids": [todo.id],
            },
        }

    async def _handle_add_decision(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Record a conclusion-level decision the user just made.

        Use for explicit statements like "决定用 X 方案" / "我们定了 Y 不做 Z".
        Not for tentative thoughts or open questions.
        """
        title = _required_text(arguments, "title")
        decision = WoloDecision(
            id=uuid4().hex[:12],
            record_id=str(arguments.get("record_id") or ""),
            title=title,
            rationale=str(arguments.get("rationale") or ""),
            impact=str(arguments.get("impact") or ""),
            project=str(arguments.get("project") or ""),
            source="explicit",
            created_at=_now(),
        )
        self.store.add_decision(decision)
        return {
            "ok": True,
            "decision_id": decision.id,
            "message": f"✅ 已记录决策：{title}",
            "_metadata": {
                "app": "wolo",
                "domain_event": "decision_created",
                "decision_ids": [decision.id],
            },
        }

    async def _handle_add_highlight(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Record a highlight entry of the given kind.

        kind must be one of: important, prompt, tool, blocker, risk.
        Use for user-stated lessons, blockers, risks, or important sync points.
        """
        title = _required_text(arguments, "title")
        kind = _required_text(arguments, "kind")
        valid_kinds = ("important", "prompt", "tool", "blocker", "risk")
        if kind not in valid_kinds:
            raise ValueError(f"kind must be one of {valid_kinds}, got {kind!r}")
        highlight = WoloHighlight(
            id=uuid4().hex[:12],
            record_id=str(arguments.get("record_id") or ""),
            kind=kind,
            title=title,
            content=str(arguments.get("detail") or arguments.get("content") or ""),
            project=str(arguments.get("project") or ""),
            tags=str(arguments.get("tags") or ""),
            source="explicit",
            created_at=_now(),
        )
        self.store.add_highlight(highlight)
        return {
            "ok": True,
            "highlight_id": highlight.id,
            "message": f"✅ 已记录 highlight({kind})：{title}",
            "_metadata": {
                "app": "wolo",
                "domain_event": "highlight_created",
                "highlight_ids": [highlight.id],
            },
        }

    async def _handle_add_experiment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Record a verifiable engineering / process experiment the user proposed.

        Use for explicit "I'll try X to see if Y" style statements, not vague wishes.
        """
        title = _required_text(arguments, "title")
        experiment = WoloExperiment(
            id=uuid4().hex[:12],
            record_id=str(arguments.get("record_id") or ""),
            title=title,
            hypothesis=str(arguments.get("hypothesis") or ""),
            problem=str(arguments.get("problem") or ""),
            strategy=str(arguments.get("strategy") or ""),
            next_move=str(arguments.get("next_move") or ""),
            success_signal=str(arguments.get("success_signal") or ""),
            deadline=str(arguments.get("deadline") or ""),
            project=str(arguments.get("project") or ""),
            status="active",
            source="explicit",
            created_at=_now(),
        )
        self.store.add_experiment(experiment)
        return {
            "ok": True,
            "experiment_id": experiment.id,
            "message": f"✅ 已记录实验：{title}",
            "_metadata": {
                "app": "wolo",
                "domain_event": "experiment_created",
                "experiment_ids": [experiment.id],
            },
        }

    async def _handle_work_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = _optional_text(arguments, "query")
        project = _optional_text(arguments, "project")
        limit = int(arguments.get("limit") or 10)
        records = self.store.search_records(query=query, tags=[project] if project else None, limit=limit)
        decisions = self.store.list_decisions(project=project, query=query, limit=limit)
        highlights = self.store.list_highlights(project=project, query=query, limit=limit)
        experiments = self.store.list_experiments(project=project, query=query, limit=limit)
        sections = [
            "## Records\n" + _format_records(self.store, records),
            "## Decisions\n" + _format_decisions(decisions),
            "## Highlights\n" + _format_highlights(highlights, empty="暂无重要事项。"),
            "## Experiments\n" + _format_experiments(experiments),
        ]
        return {
            "ok": True,
            "records": [record.to_dict() for record in records],
            "decisions": [decision.to_dict() for decision in decisions],
            "highlights": [item.to_dict() for item in highlights],
            "experiments": [item.to_dict() for item in experiments],
            "message": "\n\n".join(sections),
        }

    async def _handle_patterns(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from collections import Counter

        days = int(arguments.get("days") or 30)
        limit = int(arguments.get("limit") or 5)
        start_date = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        query = _optional_text(arguments, "query")
        project = _optional_text(arguments, "project")
        search_query = " ".join(part for part in (project, query) if part)
        records = self.store.search_records(
            query=search_query or None,
            start_date=start_date,
            limit=300,
        )
        if not records:
            return {"ok": False, "message": f"最近 {days} 天暂无工作记录，无法总结模式。"}

        sample_counts = Counter(record.sample_type or "neutral" for record in records)
        problem_counts = Counter(record.problem_essence for record in records if record.problem_essence)
        strategy_counts = Counter(record.strategy for record in records if record.strategy)
        validation_counts = Counter(record.validation_signal for record in records if record.validation_signal)

        sections = [
            "## Sample Types\n" + "\n".join(f"- {name}: {count}" for name, count in sample_counts.most_common(limit)),
            "## Problem Essence\n" + ("\n".join(f"- {name}: {count}" for name, count in problem_counts.most_common(limit)) or "- 暂无"),
            "## Strategies\n" + ("\n".join(f"- {name}: {count}" for name, count in strategy_counts.most_common(limit)) or "- 暂无"),
            "## Validation Signals\n" + ("\n".join(f"- {name}: {count}" for name, count in validation_counts.most_common(limit)) or "- 暂无"),
        ]
        return {
            "ok": True,
            "records": [record.to_dict() for record in records],
            "message": "\n\n".join(sections),
        }

    async def _handle_playbook(self, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = int(arguments.get("limit") or 5)
        project = _optional_text(arguments, "project")
        query = _optional_text(arguments, "query")
        search_query = " ".join(part for part in (project, query) if part)
        records = self.store.search_records(query=search_query or None, limit=100)
        decisions = self.store.list_decisions(project=project, query=query, limit=limit)
        experiments = self.store.list_experiments(status="active", project=project, query=query, limit=limit)

        problem_rules = [
            f"- 问题：{record.problem_essence}\n  策略：{record.strategy}\n  下一步：{record.next_move}\n  验证：{record.validation_signal}"
            for record in records
            if record.problem_essence and record.strategy
        ][:limit]
        decision_lines = [
            f"- {item.title}（项目：{item.project}；原因：{item.rationale}）"
            for item in decisions
        ]
        experiment_lines = [
            f"- {item.title}: {item.hypothesis} -> {item.next_move}（成功信号：{item.success_signal}）"
            for item in experiments
        ]

        sections = [
            "## Problem → Strategy\n" + ("\n".join(problem_rules) or "- 暂无"),
            "## Decision Anchors\n" + ("\n".join(decision_lines) or "- 暂无"),
            "## Active Experiments\n" + ("\n".join(experiment_lines) or "- 暂无"),
        ]
        return {
            "ok": True,
            "decisions": [item.to_dict() for item in decisions],
            "experiments": [item.to_dict() for item in experiments],
            "message": "\n\n".join(sections),
        }

    async def _handle_update_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing wolo record."""
        record_id = _required_text(arguments, "record_id")

        existing = self.store.get_record(record_id)
        if existing is None:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}

        # Valid fields for update
        VALID_FIELDS = (
            "summary", "tags", "emotion", "emotion_reason", "events", "period",
            "corrected_content", "related_people", "related_places", "date",
            "sample_type", "problem_essence", "available_cards", "strategy",
            "next_move", "deadline", "validation_signal",
        )
        updates: dict[str, Any] = {}
        for field in VALID_FIELDS:
            if field in arguments:
                updates[field] = arguments[field]

        if not updates:
            return {"ok": False, "message": "未提供任何更新字段。"}

        # Layer 2 — Hard same-turn update rejection. This is the primary fix
        # for the incident where the model would create a record and then
        # issue a chain of supplement / polish updates in the same user turn.
        same_turn_guard_enabled = os.environ.get(_WOLO_DISABLE_SAME_TURN_UPDATE_GUARD) != "1"
        if same_turn_guard_enabled and record_id in self._created_record_ids:
            logger.info(
                "_handle_update_record same-turn guard rejected record_id=%s", record_id
            )
            return {
                "ok": False,
                "message": (
                    f"❌ 同轮创建保护：record_id={record_id} 是本轮刚创建的记录，"
                    "不允许用 wolo_update_record 补充或润色。\n"
                    "请将所有已知字段在 wolo_record 调用时一次性填入；"
                    "缺失或模糊的事实请用 wolo_clarify 向用户确认，不要推断。\n"
                    "不要重试本次 update —— 直接用一句自然的话回复用户即可。"
                ),
            }

        # Layer 4 — Hallucinated-field rejection on fresh records. Independent
        # of Layer-2: Layer-2 only catches updates to records created in the
        # *current* turn, but the model can also pollute an older record by
        # pulling a subjective field out of conversation-history context.
        # Layer-4 is the belt-and-suspenders guard for that case — it rejects
        # inferred subjective fields on any record younger than
        # SAME_TURN_RECENT_WINDOW_SECONDS.
        hallucination_guard_enabled = os.environ.get(_WOLO_DISABLE_HALLUCINATION_GUARD) != "1"
        if (
            hallucination_guard_enabled
            and _is_recently_created_record(existing, window_seconds=SAME_TURN_RECENT_WINDOW_SECONDS)
        ):
            subjective_hit = SUBJECTIVE_UPDATE_FIELDS & set(updates.keys())
            if subjective_hit:
                logger.info(
                    "_handle_update_record hallucination guard rejected record_id=%s fields=%s",
                    record_id, sorted(subjective_hit),
                )
                return {
                    "ok": False,
                    "message": (
                        "❌ 推断字段保护：刚创建的记录不允许通过 update 补入主观推断字段"
                        f"（{', '.join(sorted(subjective_hit))}）。\n"
                        "请在 wolo_record 调用时依据用户原文一次性填写；"
                        "如果用户没有明确说明，请保持为空或中性。\n"
                        "不要从对话历史中把之前用户纠正过的字段搬到这条新记录上。"
                    ),
                }

        # No-op shortcut: if every proposed field already matches the existing
        # value, short-circuit and surface the result as is_error=True with
        # metadata.noop=True so the adapter + engine loop guard can block any
        # second identical no-op.
        def _normalize(value: Any) -> str:
            if value is None:
                return ""
            return " ".join(str(value).split())

        no_op = all(_normalize(updates.get(f)) == _normalize(getattr(existing, f, None)) for f in updates)
        if no_op:
            logger.info("_handle_update_record no-op record_id=%s", record_id)
            return {
                "ok": False,
                "_is_error": True,
                "message": (
                    f"ℹ️ 记录 {record_id} 当前内容已与传入值一致，无需重复更新。"
                    "不要再对同一条记录发起相同内容的 update 调用，"
                    "直接用一句自然的话回复用户即可。"
                ),
                "_metadata": {
                    "domain_event": "record_update_noop",
                    "noop": True,
                    "record_id": record_id,
                },
            }

        if "date" in updates:
            date_val = str(updates["date"])
            updates["weekday"] = _get_weekday(date_val)
            updates["season"] = _get_season(date_val)
            updates["is_weekend"] = _is_weekend(date_val)

        if "corrected_content" in updates:
            updates["content_length"] = len(str(updates["corrected_content"]))

        success = self.store.update_record(record_id, **updates)
        if success:
            return {"ok": True, "message": f"✅ 已成功更新记录 {record_id}。"}
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
            f"highlights={status['highlights']}，experiments={status['experiments']}，"
            f"pending={status['pending_confirmations']}，"
            f"path={status['path']}"
        )
        return {"ok": True, **status, "message": message}

    async def _handle_llm_usage(self, arguments: dict[str, Any]) -> dict[str, Any]:
        summary = self.store.llm_usage_summary()
        return {"ok": True, **summary, "message": format_wolo_llm_usage(summary)}

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
        records_summary = "\n".join(
            f"- [{r.date}] {r.summary} sample={r.sample_type} problem={r.problem_essence} "
            f"strategy={r.strategy} next={r.next_move} validation={r.validation_signal}"
            for r in records
        )

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
        end_date = datetime.now(timezone.utc).date().isoformat()
        records = self.store.search_records(start_date=start_date, limit=200)

        if not records:
            return {"ok": False, "message": f"最近 {days} 天暂无记录，无法可视化。"}

        from collections import Counter

        if viz_type == "emotion_distribution":
            emotions = [r.emotion for r in records if r.emotion]
            counts = Counter(emotions)
            total = sum(counts.values())
            # Table format for better card rendering
            header = f"## 📊 情绪分布 ({start_date} ~ {end_date})\n\n"
            table = "| 情绪 | 次数 | 占比 | 分布 |\n|------|------|------|------|\n"
            for emo, count in counts.most_common():
                pct = count * 100 // total if total else 0
                bar = "▓" * min(count, 20) + "░" * max(0, 20 - count)
                table += f"| {emo} | {count} | {pct}% | `{bar}` |\n"
            return {"ok": True, "message": header + table}

        if viz_type == "tag_cloud":
            all_tags: list[str] = []
            for r in records:
                all_tags.extend([t.strip() for t in r.tags.split(",") if t.strip()])
            counts = Counter(all_tags).most_common(15)
            total = sum(c for _, c in counts)
            header = f"## 🏷️ 高频标签 Top 15 ({start_date} ~ {end_date})\n\n"
            table = "| # | 标签 | 次数 | 占比 | 热度 |\n|---|------|------|------|------|\n"
            for i, (tag, count) in enumerate(counts, 1):
                pct = count * 100 // total if total else 0
                heat = "🔥" * min(count, 5)
                table += f"| {i} | `{tag}` | {count} | {pct}% | {heat} |\n"
            return {"ok": True, "message": header + table}

        if viz_type == "activity_heatmap":
            dates = [r.date for r in records]
            counts = Counter(dates)
            header = f"## 📅 活动热力图 ({start_date} ~ {end_date})\n\n"
            # Week-based grid with day labels
            lines = ["| 周 | 一 | 二 | 三 | 四 | 五 | 六 | 日 |", "|---|---|---|---|---|---|---|---|"]
            # Build week rows
            current = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
            # Align to Monday
            while current.weekday() != 0:
                current = current - timedelta(days=1)
            week_num = 1
            while current <= end_dt:
                row = [f"W{week_num}"]
                for _ in range(7):
                    d = current.isoformat()
                    count = counts.get(d, 0)
                    if current > end_dt or current < datetime.strptime(start_date, "%Y-%m-%d").date():
                        row.append("·")
                    elif count == 0:
                        row.append("░")
                    elif count == 1:
                        row.append("▒")
                    elif count == 2:
                        row.append("▓")
                    else:
                        row.append("█")
                    current = current + timedelta(days=1)
                lines.append("| " + " | ".join(row) + " |")
                week_num += 1
            legend = "\n\n> 图例: · 非周期 | ░ 无记录 | ▒ 1条 | ▓ 2条 | █ 3+条"
            active_days = len(set(dates))
            summary = f"\n\n**活跃天数**: {active_days}/{days} | **总记录**: {len(records)} 条 | **日均**: {len(records)/max(1,active_days):.1f} 条"
            return {"ok": True, "message": header + "\n".join(lines) + legend + summary}

        if viz_type == "sample_type_distribution":
            sample_types = [r.sample_type for r in records if r.sample_type]
            counts = Counter(sample_types)
            total = sum(counts.values())
            header = f"## 🧬 样本类型分布 ({start_date} ~ {end_date})\n\n"
            table = "| 类型 | 次数 | 占比 | 分布 |\n|------|------|------|------|\n"
            for kind, count in counts.most_common():
                pct = count * 100 // total if total else 0
                bar = "▓" * min(count, 20) + "░" * max(0, 20 - count)
                table += f"| {kind} | {count} | {pct}% | `{bar}` |\n"
            return {"ok": True, "message": header + table}

        return {"ok": False, "message": f"不支持的可视化类型：{viz_type}。目前支持：emotion_distribution, tag_cloud, activity_heatmap, sample_type_distribution"}

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
                if r.sample_type != "neutral":
                    content += (
                        f"- 样本类型：{r.sample_type}\n"
                        f"- 问题本质：{r.problem_essence}\n"
                        f"- 手上牌：{r.available_cards}\n"
                        f"- 策略：{r.strategy}\n"
                        f"- 下一步：{r.next_move}\n"
                        f"- 截止：{r.deadline}\n"
                        f"- 验证信号：{r.validation_signal}\n\n---\n\n"
                    )
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
            # Avoid duplicates
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

    async def _handle_fetch_digest(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """On-demand feed digest: start a background collection task and return immediately."""
        import asyncio

        # Accept both "domain" (new) and "preset" (deprecated alias)
        domain = str(arguments.get("domain") or arguments.get("preset") or "").strip() or None
        date = str(arguments.get("date") or "").strip() or None

        logger.info("fetch_digest requested domain=%s date=%s", domain, date)
        task = asyncio.create_task(
            self._run_fetch_digest_background(domain=domain, date=date),
            name="wolo-fetch-digest",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(self._log_fetch_digest_background_result)
        label = domain or "enabled domains"
        return {
            "ok": True,
            "message": f"🕒 新闻简报已在后台开始生成（domain={label}），完成后会自动推送报告。",
        }

    def _log_fetch_digest_background_result(self, task: Any) -> None:
        import asyncio

        try:
            task.result()
        except asyncio.CancelledError:
            logger.info("fetch_digest background task cancelled")
        except Exception as exc:
            logger.error("fetch_digest background task crashed: %s", exc, exc_info=True)

    async def _run_fetch_digest_background(self, *, domain: str | None, date: str | None) -> None:
        """Collect, score, synthesize, archive and push Markdown via progress callback."""
        from wolo.feed_digest import run_feed_digest

        try:
            report = await run_feed_digest(
                workspace=self.store.workspace,
                domain_name=domain,
                date=date,
                progress_callback=self._push_progress,
            )
        except Exception as exc:
            logger.error("fetch_digest failed: %s", exc, exc_info=True)
            await self._push_progress(f"❌ 新闻简报生成失败：{exc}")
            return

        meta = report.metadata or {}
        is_empty = meta.get("is_empty", False)
        logger.info(
            "fetch_digest succeeded domain=%s date=%s is_empty=%s selected=%d",
            meta.get("domain"),
            meta.get("date"),
            is_empty,
            meta.get("selected_count", 0),
        )
        if is_empty:
            label = meta.get("domain") or domain or "default"
            await self._push_progress(f"📭 今日无高信号新闻（domain={label}）。\n\n{report.content}")
            return

        await self._push_progress(report.content)

    # ── Project management handlers ──────────────────────────────

    async def _handle_projects(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = _optional_text(arguments, "status") or "active"
        projects = self.store.list_projects(status=status)
        items = []
        for p in projects:
            milestones = self.store.list_milestones(p.id)
            done = sum(1 for m in milestones if m.status == "completed")
            items.append({
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "status": p.status,
                "priority": p.priority,
                "start_date": p.start_date,
                "target_date": p.target_date,
                "milestones": f"{done}/{len(milestones)}",
            })
        return {"ok": True, "projects": items, "count": len(items)}

    async def _handle_project_scan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from common.project_ai.discovery import scan_for_projects

        if not self._agent_factory:
            return {
                "ok": False,
                "error": "AI agent is not available. Please check your model configuration before scanning for projects.",
            }
        agent = self._agent_factory()
        try:
            candidates = await scan_for_projects(store=self.store, agent=agent)
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        now = _now()
        created = 0
        for c in candidates:
            suggestion = ProjectSuggestion(
                id=str(uuid4()),
                suggestion_type=c.get("suggestion_type", "create_project"),
                title=c.get("title", ""),
                rationale=c.get("rationale", ""),
                proposed_payload_json=json.dumps({
                    "title": c.get("title", ""),
                    "summary": c.get("summary", ""),
                    "keywords": c.get("keywords", []),
                    "suggested_milestones": c.get("suggested_milestones", []),
                }, ensure_ascii=False),
                evidence_json=json.dumps(c.get("evidence", []), ensure_ascii=False),
                confidence=float(c.get("confidence", 0)),
                status="pending",
                source="ai_scan",
                created_at=now,
                updated_at=now,
            )
            self.store.create_project_suggestion(suggestion)
            created += 1
        return {"ok": True, "candidates_found": created}

    async def _handle_project_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        title = _required_text(arguments, "title")
        description = _optional_text(arguments, "description") or ""
        now = _now()
        project = Project(
            id=str(uuid4()),
            title=title,
            description=description,
            status="active",
            priority=_optional_text(arguments, "priority") or "medium",
            start_date=now[:10],
            created_at=now,
            updated_at=now,
        )
        self.store.create_project(project)
        milestones = arguments.get("milestones") or []
        if isinstance(milestones, list):
            for ms_title in milestones:
                if isinstance(ms_title, str) and ms_title.strip():
                    from wolo.core.models import Milestone
                    ms = Milestone(
                        id=str(uuid4()),
                        project_id=project.id,
                        title=ms_title.strip(),
                        created_at=now,
                        updated_at=now,
                    )
                    self.store.create_milestone(ms)
        return {"ok": True, "project_id": project.id, "title": title}

    async def _handle_project_suggestions(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = _optional_text(arguments, "status") or "pending"
        suggestions = self.store.list_project_suggestions(status=status)
        items = [
            {
                "id": s.id,
                "type": s.suggestion_type,
                "title": s.title,
                "rationale": s.rationale,
                "confidence": s.confidence,
                "status": s.status,
            }
            for s in suggestions
        ]
        return {"ok": True, "suggestions": items, "count": len(items)}

    async def _handle_project_review(self, arguments: dict[str, Any]) -> dict[str, Any]:
        suggestion_id = _required_text(arguments, "suggestion_id")
        action = _required_text(arguments, "action")
        if action == "accept":
            ok = self.store.accept_project_suggestion(suggestion_id)
        elif action == "reject":
            ok = self.store.reject_project_suggestion(suggestion_id)
        else:
            return {"ok": False, "error": f"Unknown action: {action}"}
        return {"ok": ok}

    async def _handle_project_detail(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        detail = self.store.get_project_detail(project_id)
        if not detail:
            return {"ok": False, "error": "Project not found"}
        return {"ok": True, "project": detail}

    async def _handle_project_update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        updates = {}
        for key in ("title", "description", "priority", "target_date", "tags", "stakeholders", "success_criteria"):
            val = _optional_text(arguments, key)
            if val is not None:
                updates[key] = val
        if not updates:
            return {"ok": False, "error": "No fields to update"}
        ok = self.store.update_project(project_id, **updates)
        return {"ok": ok, "project_id": project_id, "updated_fields": list(updates.keys())}

    async def _handle_project_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        ok = self.store.delete_project(project_id)
        return {"ok": ok, "project_id": project_id}

    async def _handle_project_complete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        ok = self.store.complete_project(project_id)
        return {"ok": ok, "project_id": project_id}

    async def _handle_project_archive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        reason = _optional_text(arguments, "reason") or ""
        ok = self.store.archive_project(project_id, reason=reason)
        return {"ok": ok, "project_id": project_id}

    async def _handle_project_reactivate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        ok = self.store.reactivate_project(project_id)
        return {"ok": ok, "project_id": project_id}

    async def _handle_milestone_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from wolo.core.models import Milestone
        project_id = _required_text(arguments, "project_id")
        title = _required_text(arguments, "title")
        description = _optional_text(arguments, "description") or ""
        target_date = _optional_text(arguments, "target_date") or ""
        completed_at = _optional_text(arguments, "completed_at") or ""
        ms = Milestone(
            id=str(uuid4()),
            project_id=project_id,
            title=title,
            description=description,
            target_date=target_date,
            completed_at=completed_at,
            status="completed" if completed_at else "pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.create_milestone(ms)
        return {"ok": True, "milestone": ms.to_dict()}

    async def _handle_milestone_update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        milestone_id = _required_text(arguments, "milestone_id")
        updates = {}
        for key in ("title", "description", "target_date", "completed_at"):
            val = _optional_text(arguments, key)
            if val is not None:
                updates[key] = val
        if not updates:
            return {"ok": False, "error": "No fields to update"}
        ok = self.store.update_milestone(milestone_id, **updates)
        return {"ok": ok, "milestone_id": milestone_id, "updated_fields": list(updates.keys())}

    async def _handle_milestone_complete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        milestone_id = _required_text(arguments, "milestone_id")
        completed_at = _optional_text(arguments, "completed_at")
        if completed_at:
            ok = self.store.update_milestone(milestone_id, status="completed", completed_at=completed_at)
        else:
            ok = self.store.complete_milestone(milestone_id)
        return {"ok": ok, "milestone_id": milestone_id}

    async def _handle_milestone_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        milestone_id = _required_text(arguments, "milestone_id")
        ok = self.store.delete_milestone(milestone_id)
        return {"ok": ok, "milestone_id": milestone_id}

    async def _handle_project_link_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        entity_type = _required_text(arguments, "entity_type")
        entity_id = _required_text(arguments, "entity_id")
        source = _optional_text(arguments, "source") or "user"
        link = ProjectLink(
            id=str(uuid4()),
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            source=source,
            status="active",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.create_project_link(link)
        return {"ok": True, "link": link.to_dict()}

    async def _handle_project_link_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        link_id = _required_text(arguments, "link_id")
        ok = self.store.delete_project_link(link_id)
        return {"ok": ok, "link_id": link_id}

    async def _handle_project_alias_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        alias = _required_text(arguments, "alias")
        source = _optional_text(arguments, "source") or "user"
        pa = ProjectAlias(
            id=str(uuid4()),
            project_id=project_id,
            alias=alias,
            source=source,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.create_project_alias(pa)
        return {"ok": True, "alias": pa.to_dict()}

    async def _handle_project_link_backfill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        project = self.store.get_project(project_id)
        if project is None:
            return {"ok": False, "error": "Project not found"}

        search_keywords = _optional_text(arguments, "search_keywords") or ""
        if search_keywords:
            query = search_keywords
        else:
            aliases = self.store.list_project_aliases(project_id)
            terms = [project.title] + [a.alias for a in aliases]
            query = " ".join(terms)

        existing_links = self.store.list_project_links(project_id=project_id, entity_type="record")
        linked_ids = {lnk.entity_id for lnk in existing_links}

        records = self.store.search_records(query=query, limit=200)

        now = _now()
        new_links: list[dict] = []
        for r in records:
            if r.id in linked_ids:
                continue
            link_id = str(uuid4())
            link = ProjectLink(
                id=link_id,
                project_id=project_id,
                entity_type="record",
                entity_id=r.id,
                source="ai_high_confidence",
                confidence="",
                status="active",
                sort_order=len(linked_ids) + len(new_links),
                created_at=now,
                updated_at=now,
            )
            self.store.create_project_link(link)
            new_links.append({"id": r.id, "date": r.date, "summary": r.summary})
            linked_ids.add(r.id)

        return {
            "ok": True,
            "project_id": project_id,
            "newly_linked_count": len(new_links),
            "newly_linked": new_links,
            "total_linked": len(linked_ids),
        }

    async def _handle_project_snapshot_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_text(arguments, "project_id")
        summary = _optional_text(arguments, "summary") or ""
        next_action = _optional_text(arguments, "next_action") or ""
        health = _optional_text(arguments, "health") or ""

        project = self.store.get_project(project_id)
        if project is None:
            return {"ok": False, "error": "Project not found"}

        milestones = self.store.list_milestones(project_id)
        total = len(milestones)
        done = sum(1 for m in milestones if m.status == "completed")
        completion_pct = int(done / total * 100) if total > 0 else None

        links = self.store.list_project_links(project_id=project_id, status="active")
        now = datetime.now(timezone.utc)
        cutoff_7d = (now - timedelta(days=7)).isoformat()
        activity_7d = sum(1 for lnk in links if lnk.created_at and lnk.created_at >= cutoff_7d)

        open_blocker_count = 0
        for lnk in links:
            if lnk.entity_type == "highlight":
                cur = self.store._db.execute("SELECT kind FROM highlights WHERE id = ?", (lnk.entity_id,))
                hrow = cur.fetchone()
                if hrow and hrow[0] == "blocker":
                    open_blocker_count += 1

        if not health:
            health = "normal"
            if open_blocker_count > 0:
                health = "at_risk"
            elif project.target_date:
                try:
                    target = datetime.fromisoformat(project.target_date).date()
                    if target < now.date():
                        health = "at_risk"
                    elif target <= now.date() + timedelta(days=7) and (completion_pct is None or completion_pct < 80):
                        health = "attention"
                except ValueError:
                    pass
            if health == "normal" and activity_7d == 0 and links:
                health = "attention"

        if not next_action:
            pending = [m for m in milestones if m.status == "pending"]
            next_action = f"完成里程碑: {pending[0].title}" if pending else "项目已完成或无待办里程碑"

        if not summary:
            linked_record_count = sum(1 for lnk in links if lnk.entity_type == "record")
            summary = (
                f"{linked_record_count}条记录已关联，{done}/{total}里程碑已完成。"
                f"近7天活跃度{activity_7d}，{open_blocker_count}个未解决blocker。"
            )

        snapshot = ProjectSnapshot(
            id=str(uuid4()),
            project_id=project_id,
            snapshot_date=now.strftime("%Y-%m-%d"),
            summary=summary,
            health=health,
            completion_pct=completion_pct,
            activity_7d=activity_7d,
            open_blocker_count=open_blocker_count,
            next_action=next_action,
            created_at=now.isoformat(),
        )
        self.store.create_project_snapshot(snapshot)
        return {"ok": True, "snapshot": snapshot.to_dict()}

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
            "wolo_experiments",
            "wolo_blockers",
            "wolo_decisions",
            "wolo_highlights",
            "wolo_work_query",
            "wolo_patterns",
            "wolo_playbook",
        }

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        raw = arguments.model_dump()
        try:
            result = await self._domain_tool.handler(raw)
            output = str(result.get("message") or result)
            metadata: dict[str, Any] = {}
            if result.get("path"):
                metadata["paths"] = [str(result["path"])]
            handler_metadata = result.get("_metadata")
            if isinstance(handler_metadata, dict):
                metadata.update(handler_metadata)
            is_error = bool(result.get("_is_error")) or result.get("ok") is False
            return ToolResult(output=output, metadata=metadata, is_error=is_error)
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
    oh_registry.register(SkillListTool())
    oh_registry.register(SkillLoadTool())
    oh_registry.register(SkillSearchTool())
    oh_registry.register(SkillWriteTool())
    oh_registry.register(SkillPatchTool())
    oh_registry.register(SkillDeleteTool())
    return oh_registry


def _tool_record() -> ToolDefinition:
    return _definition(
        "wolo_record",
        (
            "Record a SINGLE-DATE work log entry when the intent and core content are clear enough to understand. "
            "Use for project progress, meetings, code changes, prompt/tool experiments, blockers, "
            "decisions, reviews, incidents, and next actions. "
            "IMPORTANT: This tool only accepts ONE date. If the user's message spans multiple dates "
            "(e.g. '昨天做了X，今天做了Y'), use wolo_import_records to split into separate records per date. "
            "Do NOT call this when the user's intent is ambiguous — call wolo_clarify instead. "
            "Fill in ALL known structured fields (summary, tags, emotion/emotion_reason, related_people, "
            "related_places, date, period, events, todos, decisions, highlights) in this single call — "
            "missing or uncertain facts must be clarified via wolo_clarify, NEVER patched in later via "
            "wolo_update_record. Subjective fields (emotion, emotion_reason, sample_type, problem_essence, "
            "available_cards, strategy, next_move, deadline, validation_signal) must come from the user's "
            "own words; when the user did not state them, leave them empty / neutral. Do not add unstated "
            "causes, diagnoses, motives, timelines, or outcomes. Do NOT rewrite a future plan as a "
            "completed event; preserve the original tense. SIDE-EFFECT CHECK: If this message reveals persistent personal facts (chronic health conditions, new relationships, work structure changes, long-term preferences), also call wolo_remember in the SAME turn to store them in long-term memory."
        ),
        [
            ("content", "string", "Faithful paraphrase of the user's current message. Must preserve all facts, opinions, and claims the user actually expressed. Do NOT add facts, opinions, or reflections the user did not state in this turn — even if a recent conversation topic suggests them.", True),
            ("corrected_content", "string", "Cleanup of speech-to-text artifacts in `content` ONLY: removing redundancy/filler words, fixing typos, adding punctuation, smoothing broken grammar. Think copy-editor pass, not rewrite. MUST NOT introduce any fact, opinion, or claim that is not already present in `content` — every fact in `corrected_content` must also appear in `content`. NEVER change the event tense (planned→done / future→past). Example: 'too late today, will push to production tomorrow' → keep the future tense, do NOT rewrite as 'pushed to production today'.", False),
            ("summary", "string", f"One-sentence work summary (≤{SUMMARY_MAX_LENGTH} chars) with outcome, decision, blocker, or next action. Keep semantics complete — do not over-compress.", False),
            ("tags", "string", "Comma-separated project/work tags, e.g. project, meeting, code, prompt, tool, bug, review, blocker, decision.", False),
            ("emotion", "string", f"Short work status keyword (≤{EMOTION_MAX_LENGTH} chars), e.g. 顺利/受阻/中性/高压/完成/风险. Must NOT be a full sentence.", False),
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
            ("linked_project", "string", "Project title or alias this record is related to. Fill this when the record content clearly relates to an ongoing project (e.g. progress update, milestone, activity log). The system will auto-link the record to the matched project.", False),
        ],
    )


def _tool_import_records() -> ToolDefinition:
    return ToolDefinition(
        name="wolo_import_records",
        description=(
            "Import multiple structured work records, each with its own date. "
            "Use when: (1) a single message contains events spanning MULTIPLE dates "
            "(e.g. '昨天加班到12点，今天上午开了站会') — split by date, assign correct date to each; "
            "(2) batch import from messy notes, meeting logs, or report drafts. "
            "Each record's content should be written from that date's perspective (avoid '昨天' inside the content)."
        ),
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
        (
            "Quick-path for backfilling a missing work entry when the user provides raw content without structured fields. "
            "Saves the entry and auto-triggers process_pending to structure it via LLM. "
            "Use wolo_record instead if you can already extract structured fields (summary, tags, emotion)."
        ),
        [("content", "string", "Backfill content.", True)],
    )


def _tool_report() -> ToolDefinition:
    return _definition(
        "wolo_report",
        "Generate weekly, monthly, or yearly wolo work report with progress, decisions, blockers, prompt/tool lessons, and next actions. "
        "Supports custom date ranges to generate reports for any past period (e.g. '上周的周报', '3月份的月报').",
        [
            ("type", "string", "weekly/monthly/yearly.", True),
            ("start_date", "string", "Optional start date (YYYY-MM-DD). If omitted, defaults to 7/30/365 days ago based on type.", False),
            ("end_date", "string", "Optional end date (YYYY-MM-DD). If omitted, defaults to today.", False),
        ],
    )


def _tool_report_list() -> ToolDefinition:
    return _definition(
        "wolo_report_list",
        "List all existing reports, optionally filtered by type. Returns id, type, and created_at for each.",
        [("type", "string", "Optional filter: weekly/monthly/yearly.", False)],
    )


def _tool_report_show() -> ToolDefinition:
    return _definition(
        "wolo_report_show",
        "Show the full content of a specific report by its ID.",
        [("report_id", "string", "The report ID to show.", True)],
    )


def _tool_report_delete() -> ToolDefinition:
    return _definition(
        "wolo_report_delete",
        "Permanently delete a report by its ID.",
        [("report_id", "string", "The report ID to delete.", True)],
    )


def _tool_report_update() -> ToolDefinition:
    return _definition(
        "wolo_report_update",
        "Update/replace the content of an existing report.",
        [
            ("report_id", "string", "The report ID to update.", True),
            ("content", "string", "New markdown content for the report.", True),
        ],
    )


def _tool_report_search() -> ToolDefinition:
    return _definition(
        "wolo_report_search",
        "Search reports by keyword in their content. Returns matching report IDs and metadata.",
        [("keyword", "string", "Search keyword.", True)],
    )


def _tool_remind() -> ToolDefinition:
    return _definition(
        "wolo_remind",
        (
            "Schedule a one-shot reminder that sends a notification to the user at a future time. "
            "The system only SENDS A MESSAGE — it does NOT execute any task. "
            "Use for requests like '2分钟后提醒我喝水' or '明天 09:30 提醒我发周报'. "
            "If the user wants the system to DO something and return results, use wolo_schedule instead. "
            "For recurring/periodic reminders, use wolo_heartbeat_task instead. "
            "Provide either remind_at (ISO-8601 datetime) or one/more delay_* fields."
        ),
        [
            ("message", "string", "What to remind the user about, e.g. 喝水 / 发周报 / 跟进 blocker.", True),
            ("remind_at", "string", "Absolute reminder time as ISO-8601 datetime. Use this for explicit future timestamps.", False),
            ("delay_seconds", "integer", "Relative delay in seconds for very short reminders.", False),
            ("delay_minutes", "integer", "Relative delay in minutes, e.g. 2 for '2分钟后'.", False),
            ("delay_hours", "integer", "Relative delay in hours.", False),
            ("delay_days", "integer", "Relative delay in days.", False),
        ],
    )


def _tool_schedule() -> ToolDefinition:
    return _definition(
        "wolo_schedule",
        (
            "Schedule a one-shot agent task that EXECUTES at a future time and DMs the result to the user. "
            "The system will actually perform the work (e.g. generate a report, summarize records) — not just remind. "
            "Use for requests like '明天12点生成一份周报' or '下午3点帮我整理今天的工作记录'. "
            "If the user only needs a notification without execution, use wolo_remind instead. "
            "For recurring/periodic tasks, use wolo_heartbeat_task instead. "
            "Provide either run_at (ISO-8601 datetime) or one/more delay_* fields."
        ),
        [
            ("prompt", "string", "The task prompt for the agent to execute at the scheduled time, e.g. 生成本周周报 / 整理今天工作记录.", True),
            ("run_at", "string", "Absolute run time as ISO-8601 datetime.", False),
            ("delay_seconds", "integer", "Relative delay in seconds.", False),
            ("delay_minutes", "integer", "Relative delay in minutes.", False),
            ("delay_hours", "integer", "Relative delay in hours.", False),
            ("delay_days", "integer", "Relative delay in days.", False),
        ],
    )


def _tool_jobs() -> ToolDefinition:
    return _definition(
        "wolo_jobs",
        "List all pending one-shot reminders and scheduled tasks (not yet executed). Use before cancelling to get job names.",
        [],
    )


def _tool_cancel() -> ToolDefinition:
    return _definition(
        "wolo_cancel",
        (
            "Cancel a pending one-shot reminder or scheduled task by job name. "
            "Use wolo_jobs first to get the job name. "
            "Use for requests like '取消刚才的提醒' or '我不想要那个周报任务了'."
        ),
        [
            ("job_name", "string", "Name of the job to cancel (from wolo_jobs).", True),
        ],
    )


def _tool_view() -> ToolDefinition:
    return _definition(
        "wolo_view",
        "Browse the most recent wolo records in reverse-chronological order. Use for quick 'what did I log lately' checks without any filter criteria. For filtered queries, use wolo_search instead.",
        [("limit", "integer", "Number of records (default 10).", False)],
    )


def _tool_search() -> ToolDefinition:
    return _definition(
        "wolo_search",
        (
            "Search through wolo work records with precise filters (keywords, date range, tags, status labels). "
            "Use this for targeted lookups like 'find all records tagged blocker in May' or 'records mentioning gateway'. "
            "For open-ended work-history questions (e.g. 'what did I accomplish last week'), prefer wolo_work_query which aggregates records + decisions + highlights."
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


def _tool_experiments() -> ToolDefinition:
    return _definition(
        "wolo_experiments",
        "List strategy experiments derived from wolo records, optionally filtered by status, project, or query.",
        [
            ("status", "string", "Experiment status: active/completed/abandoned/all. Defaults to active.", False),
            ("project", "string", "Project filter.", False),
            ("query", "string", "Text query.", False),
            ("limit", "integer", "Number of experiments.", False),
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
        "List blocker highlights derived from work records. This is the dedicated tool for blocker queries — prefer this over wolo_highlights(kind='blocker').",
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
        (
            "List important work highlights including prompt lessons, tool lessons, and risks. "
            "For blocker-specific queries, prefer the dedicated wolo_blockers tool. "
            "For decision-specific queries, prefer wolo_decisions."
        ),
        [
            ("kind", "string", "Highlight kind: important/prompt/tool/blocker/risk.", False),
            ("project", "string", "Project filter.", False),
            ("query", "string", "Text query.", False),
            ("limit", "integer", "Number of highlights.", False),
        ],
    )


def _tool_add_todo() -> ToolDefinition:
    return _definition(
        "wolo_add_todo",
        (
            "Create a new work todo item. Use when the user explicitly lists work "
            "tasks they plan to do or need to do (e.g. '这周完成...', "
            "'明天约...对齐'). Call once per distinct todo; do NOT bundle "
            "several items into a single call. If the todo was mentioned alongside "
            "a record you just saved via wolo_record, pass its record_id so the "
            "todo stays linked to its source."
        ),
        [
            ("title", "string", "Clear, actionable todo title describing one concrete work task.", True),
            ("project", "string", "Related project name.", False),
            ("priority", "string", "Priority: high/medium/low (default medium).", False),
            ("due_date", "string", "Optional due date YYYY-MM-DD.", False),
            ("record_id", "string", "Optional related wolo_record id.", False),
        ],
    )


def _tool_add_decision() -> ToolDefinition:
    return _definition(
        "wolo_add_decision",
        (
            "Record a conclusion-level decision the user explicitly made "
            "(e.g. '决定用 X 方案', '我们定了 Y 不做 Z', '最终选了 A 因为 B'). "
            "Do NOT record tentative thoughts, open questions, or options still "
            "being weighed — only decisions that have actually been settled."
        ),
        [
            ("title", "string", "Short statement of the decision itself.", True),
            ("rationale", "string", "Why this option was picked (the reasoning given).", False),
            ("impact", "string", "Expected impact or scope of the decision.", False),
            ("project", "string", "Related project name.", False),
            ("record_id", "string", "Optional related wolo_record id.", False),
        ],
    )


def _tool_add_highlight() -> ToolDefinition:
    return _definition(
        "wolo_add_highlight",
        (
            "Create a new highlight entry. Use when the user calls out an important "
            "matter, a prompt/tool lesson, a blocker, or a risk. Set 'kind' to one "
            "of: important, prompt, tool, blocker, risk. Call once per distinct "
            "highlight. Kind hints: important = milestone / sync fact; "
            "prompt = reusable prompt pattern or anti-pattern; "
            "tool = tool / CLI / API usage lesson; "
            "blocker = something currently blocking delivery; "
            "risk = signal that may worsen later if ignored."
        ),
        [
            ("title", "string", "Clear, informative highlight title.", True),
            ("kind", "string", "important/prompt/tool/blocker/risk (required).", True),
            ("detail", "string", "Optional supporting detail or context.", False),
            ("project", "string", "Optional related project name.", False),
            ("tags", "string", "Optional comma-separated tags.", False),
            ("record_id", "string", "Optional related wolo_record id.", False),
        ],
    )


def _tool_add_experiment() -> ToolDefinition:
    return _definition(
        "wolo_add_experiment",
        (
            "Record a verifiable engineering or process experiment the user "
            "explicitly proposed (e.g. '试试把 X 改成 Y，看 Z 指标会不会涨', "
            "'本周试一下每日站会缩到 10 分钟'). Do NOT record vague wishes."
        ),
        [
            ("title", "string", "Short descriptive experiment title.", True),
            ("hypothesis", "string", "What we expect to observe if the change works.", False),
            ("problem", "string", "The problem or friction this experiment targets.", False),
            ("strategy", "string", "What will be changed or tried.", False),
            ("next_move", "string", "The smallest concrete next step.", False),
            ("success_signal", "string", "How to tell the experiment worked.", False),
            ("deadline", "string", "Observation window or deadline YYYY-MM-DD.", False),
            ("project", "string", "Optional related project name.", False),
            ("record_id", "string", "Optional related wolo_record id.", False),
        ],
    )


def _tool_work_query() -> ToolDefinition:
    return _definition(
        "wolo_work_query",
        (
            "Answer open-ended work-history questions by aggregating matching records, decisions, highlights, AND experiments into one response. "
            "Use for questions like 'what did I do last week', 'summarize project X progress', 'any unresolved blockers'. "
            "For precise filtering by specific date range/tags/emotions, use wolo_search instead."
        ),
        [
            ("query", "string", "Question or search query, e.g. 'what did I do last week'.", False),
            ("project", "string", "Project filter.", False),
            ("limit", "integer", "Number of items per section.", False),
        ],
    )


def _tool_patterns() -> ToolDefinition:
    return _definition(
        "wolo_patterns",
        "Summarize recent repeated problem essences, strategies, and validation signals from work records.",
        [
            ("days", "integer", "How many recent days to analyze. Defaults to 30.", False),
            ("project", "string", "Project filter.", False),
            ("query", "string", "Text query.", False),
            ("limit", "integer", "Number of items per section. Defaults to 5.", False),
        ],
    )


def _tool_playbook() -> ToolDefinition:
    return _definition(
        "wolo_playbook",
        "Generate a reusable playbook from recent work records, decisions, and active experiments.",
        [
            ("project", "string", "Project filter.", False),
            ("query", "string", "Text query.", False),
            ("limit", "integer", "Maximum items per section.", False),
        ],
    )


def _tool_update_record() -> ToolDefinition:
    return _definition(
        "wolo_update_record",
        (
            "Modify an EXISTING structured work record (one that was created BEFORE this user turn) "
            "ONLY when (a) the user's latest message explicitly points out a factual mistake "
            "(e.g. '刚才说错了，其实是...', '改成已完成'), OR (b) visible record/search results "
            "directly evidence a mistake. "
            "FORBIDDEN on a record you just created in this same turn via wolo_record or "
            "wolo_import_records — such calls are rejected with is_error and do NOT modify the "
            "record. If you forgot a field, the right move is to fold it into the next wolo_record "
            "call or ask the user via wolo_clarify, not to patch it in via update. "
            "Subjective / inferential fields (emotion, emotion_reason, sample_type, problem_essence, "
            "available_cards, strategy, next_move, deadline, validation_signal) must NOT be inferred "
            "and added via update — only set them when the user explicitly states them IN THIS TURN. "
            "CRITICAL: a prior-turn correction applied to one record does NOT authorize you to call "
            "this tool to apply the same label to a different, newer record. Each record's subjective "
            "fields are independent and must come from the current message. "
            "Also do NOT re-call this tool with identical arguments: if the prior result said the "
            "update was a no-op (is_error), stop — do not issue another call for the same record."
        ),
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
            ("sample_type", "string", "New sample type (tension_success/aware_failure/avoidance_design/neutral).", False),
            ("problem_essence", "string", "New problem essence.", False),
            ("available_cards", "string", "New available cards.", False),
            ("strategy", "string", "New strategy.", False),
            ("next_move", "string", "New next move.", False),
            ("deadline", "string", "New deadline.", False),
            ("validation_signal", "string", "New validation signal.", False),
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


def _tool_llm_usage() -> ToolDefinition:
    return _definition(
        "wolo_llm_usage",
        "Report how many LLM calls wolo has made so far and how many input/output tokens were consumed, broken down by model name. Use this when the user asks about LLM usage, token consumption, or API call costs.",
        [],
    )


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
            "Store a suggested update for TRANSIENT or evolving work profile info that may change within weeks or months "
            "(e.g. current sprint focus, temporary blockers, active experiment parameters, reporting preferences). "
            "These are reviewed and may expire. "
            "For STABLE facts expected to last 3+ months (project goals, team structure, toolchains), use wolo_remember instead."
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
            "Store STABLE facts into the long-term memory directory — information expected to remain valid for 3+ months. "
            "Covers TWO categories: "
            "(1) Work context: project goals, repository ownership, team conventions, toolchains, recurring prompt patterns, reporting cadence. "
            "(2) Personal facts: the user's own biographical or life information such as join date / tenure, health conditions, "
            "family situation, long-term preferences, work style, and relationship dynamics. "
            "Call this tool directly whenever the user states a stable personal fact — even if no wolo_record is needed for the same message. "
            "For transient/evolving info that may change within weeks (sprint focus, temporary blockers), use wolo_profile_update instead."
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
            ("type", "string", "Type of visualization: emotion_distribution, tag_cloud, activity_heatmap, sample_type_distribution.", False),
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


def _tool_heartbeat_task() -> ToolDefinition:
    return _definition(
        "wolo_heartbeat_task",
        (
            "Manage periodic/recurring heartbeat tasks in HEARTBEAT.md. These tasks are automatically "
            "executed by the heartbeat watchdog every 30 minutes — use for RECURRING checks only. "
            "Examples: '每小时提醒我喝水', '检查邮件有没有紧急回复', '看一下 CI 有没有失败'. "
            "For ONE-TIME reminders, use wolo_remind instead. "
            "For ONE-TIME scheduled tasks, use wolo_schedule instead. "
            "Actions: add (add a new periodic task), remove (remove by keyword match), "
            "update (replace an existing task), list (show all current tasks)."
        ),
        [
            ("action", "string", "One of: add, remove, update, list.", True),
            ("task", "string", "The task content (for add: new task text; for remove/update: keyword to match existing task).", False),
            ("new_task", "string", "New task text when action=update (replaces the matched task).", False),
        ],
    )


def _tool_fetch_digest() -> ToolDefinition:
    return _definition(
        "wolo_fetch_digest",
        (
            "Start an on-demand feed digest (news briefing) in the background. "
            "Use when the user explicitly asks for a news briefing / 新闻报告 / AI热点 / 资讯简报 / feed digest. "
            "The digest is collected from external sources (GitHub, HackerNews, RSS), scored and filtered by AI, "
            "synthesized into a Markdown report, and archived. "
            "When no domain is given, all enabled domains (enable_domains) are run and merged into one report. "
            "Returns immediately with a background-started status; the final Markdown report is pushed when ready. "
            "NOTE: The background task may take several minutes to complete."
        ),
        [
            ("domain", "string", "Domain ID to fetch (e.g. 'ai_news', 'tech', 'finance', 'politics'). Omit to run all enabled domains.", False),
            ("date", "string", "Target date YYYY-MM-DD. Defaults to today.", False),
        ],
    )


def _tool_projects() -> ToolDefinition:
    return _definition(
        "wolo_projects",
        (
            "List user's work projects with status and progress. "
            "Use when the user asks about their projects, or when you need project context to make better decisions."
        ),
        [
            ("status", "string", "Filter: 'active' (default), 'completed', 'archived', 'all'.", False),
        ],
    )


def _tool_project_scan() -> ToolDefinition:
    return _definition(
        "wolo_project_scan",
        (
            "Scan recent work records to discover potential new projects. "
            "Use proactively when you notice recurring themes, goal-oriented work, or sustained tracking "
            "in the user's records that might indicate an untracked project."
        ),
        [],
    )


def _tool_project_create() -> ToolDefinition:
    return _definition(
        "wolo_project_create",
        (
            "Create a new work project. Use when the user explicitly states a new project, initiative, or ongoing endeavor, "
            "or when you identify a clear project from records and the user confirms."
        ),
        [
            ("title", "string", "Project title — specific and goal-oriented.", True),
            ("description", "string", "Brief description of the project goal.", False),
            ("priority", "string", "'high', 'medium' (default), or 'low'.", False),
            ("milestones", "string", "JSON array of milestone title strings.", False),
        ],
    )


def _tool_project_suggestions() -> ToolDefinition:
    return _definition(
        "wolo_project_suggestions",
        (
            "List pending project suggestions from AI scans or record linking. "
            "Use to review AI-discovered project candidates before accepting or rejecting them."
        ),
        [
            ("status", "string", "Filter: 'pending' (default), 'accepted', 'rejected', 'all'.", False),
        ],
    )


def _tool_project_review() -> ToolDefinition:
    return _definition(
        "wolo_project_review",
        (
            "Accept or reject a project suggestion. Use after reviewing suggestions from wolo_project_suggestions."
        ),
        [
            ("suggestion_id", "string", "ID of the suggestion to review.", True),
            ("action", "string", "'accept' or 'reject'.", True),
        ],
    )


def _tool_project_detail() -> ToolDefinition:
    return _definition(
        "wolo_project_detail",
        (
            "Get detailed project information including milestones, linked records, signals, and recent activity. "
            "Use when the user asks about a specific project's progress."
        ),
        [
            ("project_id", "string", "Project ID.", True),
        ],
    )


def _tool_project_update() -> ToolDefinition:
    return _definition(
        "wolo_project_update",
        "Update project fields (title, description, priority, target_date, tags, stakeholders, success_criteria).",
        [
            ("project_id", "string", "Project ID.", True),
            ("title", "string", "New title.", False),
            ("description", "string", "New description.", False),
            ("priority", "string", "New priority (high/medium/low).", False),
            ("target_date", "string", "New target date (YYYY-MM-DD).", False),
            ("tags", "string", "Comma-separated tags.", False),
            ("stakeholders", "string", "Comma-separated stakeholders.", False),
            ("success_criteria", "string", "Success criteria.", False),
        ],
    )


def _tool_project_delete() -> ToolDefinition:
    return _definition(
        "wolo_project_delete",
        "Delete a project and all its associated data (milestones, links, aliases). Use for removing duplicate or erroneous projects.",
        [
            ("project_id", "string", "Project ID to delete.", True),
        ],
    )


def _tool_project_complete() -> ToolDefinition:
    return _definition(
        "wolo_project_complete",
        "Mark a project as completed.",
        [
            ("project_id", "string", "Project ID.", True),
        ],
    )


def _tool_project_archive() -> ToolDefinition:
    return _definition(
        "wolo_project_archive",
        "Archive a project (soft delete, can be reactivated later).",
        [
            ("project_id", "string", "Project ID.", True),
            ("reason", "string", "Reason for archiving.", False),
        ],
    )


def _tool_project_reactivate() -> ToolDefinition:
    return _definition(
        "wolo_project_reactivate",
        "Reactivate an archived project.",
        [
            ("project_id", "string", "Project ID.", True),
        ],
    )


def _tool_milestone_create() -> ToolDefinition:
    return _definition(
        "wolo_milestone_create",
        "Create a new milestone for a project. Supports completed_at for backfilling already-completed milestones.",
        [
            ("project_id", "string", "Project ID.", True),
            ("title", "string", "Milestone title.", True),
            ("description", "string", "Milestone description.", False),
            ("target_date", "string", "Target date (YYYY-MM-DD).", False),
            ("completed_at", "string", "Completion date for backfill (YYYY-MM-DD). If set, milestone is created as completed.", False),
        ],
    )


def _tool_milestone_update() -> ToolDefinition:
    return _definition(
        "wolo_milestone_update",
        "Update milestone fields. Use completed_at to set the real completion date.",
        [
            ("milestone_id", "string", "Milestone ID.", True),
            ("title", "string", "New title.", False),
            ("description", "string", "New description.", False),
            ("target_date", "string", "New target date (YYYY-MM-DD).", False),
            ("completed_at", "string", "Real completion date (YYYY-MM-DD).", False),
        ],
    )


def _tool_milestone_complete() -> ToolDefinition:
    return _definition(
        "wolo_milestone_complete",
        "Mark a milestone as completed. Supports completed_at parameter to set the real completion date instead of now.",
        [
            ("milestone_id", "string", "Milestone ID.", True),
            ("completed_at", "string", "Real completion date (YYYY-MM-DD). If omitted, uses current time.", False),
        ],
    )


def _tool_milestone_delete() -> ToolDefinition:
    return _definition(
        "wolo_milestone_delete",
        "Delete a milestone.",
        [
            ("milestone_id", "string", "Milestone ID to delete.", True),
        ],
    )


def _tool_project_link_create() -> ToolDefinition:
    return _definition(
        "wolo_project_link_create",
        "Link a record, todo, decision, highlight, or experiment to a project.",
        [
            ("project_id", "string", "Project ID.", True),
            ("entity_type", "string", "Entity type (record, todo, decision, highlight, experiment).", True),
            ("entity_id", "string", "Entity ID.", True),
            ("source", "string", "Source of the link (user, ai_high_confidence, ai_candidate, migration).", False),
        ],
    )


def _tool_project_link_delete() -> ToolDefinition:
    return _definition(
        "wolo_project_link_delete",
        "Remove a project link.",
        [
            ("link_id", "string", "Link ID to delete.", True),
        ],
    )


def _tool_project_alias_create() -> ToolDefinition:
    return _definition(
        "wolo_project_alias_create",
        "Add an alias to a project for easier identification.",
        [
            ("project_id", "string", "Project ID.", True),
            ("alias", "string", "Alias name.", True),
            ("source", "string", "Source of the alias (user, migration, ai).", False),
        ],
    )


def _tool_project_link_backfill() -> ToolDefinition:
    return _definition(
        "wolo_project_link_backfill",
        (
            "Bulk-link historical work records to a project. Searches all records for matches "
            "against project title, aliases, and optional keywords, then creates links "
            "for any unlinked matches. Use when organizing a project that has existing "
            "records not yet associated with it."
        ),
        [
            ("project_id", "string", "Project ID to backfill links for.", True),
            ("search_keywords", "string", "Optional extra search keywords to find related records. If omitted, uses project title and aliases.", False),
        ],
    )


def _tool_project_snapshot_create() -> ToolDefinition:
    return _definition(
        "wolo_project_snapshot_create",
        (
            "Create a project snapshot capturing current progress, health, and next action. "
            "Auto-computes completion percentage, 7-day activity, blocker count, and health status if not provided. "
            "Use during project organization or periodic review."
        ),
        [
            ("project_id", "string", "Project ID.", True),
            ("summary", "string", "Snapshot summary text. Auto-generated if omitted.", False),
            ("health", "string", "Project health: 'normal', 'attention', or 'at_risk'. Auto-computed if omitted.", False),
            ("next_action", "string", "Recommended next action. Auto-derived from pending milestones if omitted.", False),
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


def _format_records(store: WoloStore, records: list[WoloRecord]) -> str:
    if not records:
        return "暂无 wolo 记录。"
    lines: list[str] = []
    for record in records:
        sample = f" [{record.sample_type}]" if record.sample_type and record.sample_type != "neutral" else ""
        lines.append(f"- [{record.id}] {record.date}{sample} {record.summary or record.raw_content}")
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


def _format_experiments(experiments: list[Any]) -> str:
    if not experiments:
        return "暂无匹配实验。"
    return "\n".join(
        f"- [{item.id}] {item.status} {item.project} {item.title}：{item.hypothesis} -> {item.next_move}".strip()
        for item in experiments
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


def _is_recently_created_record(
    record: "WoloRecord",
    *,
    window_seconds: int = SAME_TURN_RECENT_WINDOW_SECONDS,
) -> bool:
    """Return True when the record's created_at falls within the recency window.

    Used by the Layer-4 hallucination guard as a time-based fallback when the
    exact same-turn set in ``WoloToolRegistry._created_record_ids`` is not
    available (i.e. when Layer-2 is disabled via flag).
    """
    created_at = getattr(record, "created_at", None)
    if not isinstance(created_at, str) or not created_at:
        return False
    try:
        created_dt = datetime.fromisoformat(created_at)
    except ValueError:
        return False
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    return (now_utc - created_dt).total_seconds() <= window_seconds



def _resolve_project_by_name(store: WoloStore, name: str) -> "Project":
    name_norm = name.strip().lower()
    if not name_norm:
        return None
    active_projects = store.list_projects(status="active")
    for p in active_projects:
        if p.title.strip().lower() == name_norm:
            return p
    for p in active_projects:
        for alias in store.list_project_aliases(p.id):
            if alias.alias.strip().lower() == name_norm:
                return p
    matches = [p for p in active_projects if name_norm in p.title.strip().lower() or p.title.strip().lower() in name_norm]
    if len(matches) == 1:
        return matches[0]
    return None


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
