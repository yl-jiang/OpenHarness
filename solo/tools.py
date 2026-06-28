"""Self-log domain tools used by the standalone app agent."""

from __future__ import annotations

import json
import os
import re
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
from openharness.tools.skill_delete_tool import SkillDeleteTool
from openharness.tools.skill_list_tool import SkillListTool
from openharness.tools.skill_load_tool import SkillLoadTool
from openharness.tools.skill_patch_tool import SkillPatchTool
from openharness.tools.skill_search_tool import SkillSearchTool
from openharness.tools.skill_write_tool import SkillWriteTool
from openharness.utils.log import get_logger

from common.constants import EMOTION_MAX_LENGTH, SUMMARY_MAX_LENGTH
from solo.core.memory import add_memory_entry
from solo.core.models import (
    ProfileUpdate,
    Project,
    ProjectAlias,
    ProjectLink,
    ProjectSnapshot,
    ProjectSuggestion,
    SoloEntry,
    SoloFinanceBudget,
    SoloFinanceTransaction,
    SoloHealthRecord,
    SoloRecord,
    SoloTodo,
)
from solo.commands import format_solo_llm_usage
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


# Feature-flag names read via os.environ at handler time.
_SOLO_DISABLE_SAME_TURN_UPDATE_GUARD = "SOLO_DISABLE_SAME_TURN_UPDATE_GUARD"
_SOLO_DISABLE_HALLUCINATION_GUARD = "SOLO_DISABLE_HALLUCINATION_GUARD"

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
        "trigger_scene",
        "friction_signal",
        "awareness_timing",
        "break_point",
        "bridge_action",
        "environment_design",
        "next_experiment",
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
        progress_callback: Callable[[str], Any] | None = None,
    ) -> None:
        self.store = store
        self.processor = processor
        self._agent_factory = agent_factory
        self._source_context = dict(source_context or {})
        self._progress_callback = progress_callback
        self._background_tasks: set[Any] = set()
        self._created_record_ids: set[str] = set()
        self._pending_health_ids: list[str] = []
        self._pending_finance_ids: list[str] = []

    def _processor(self) -> SoloProcessor:
        if self.processor is None:
            self.processor = SoloProcessor(
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

    def post_turn_backfill(self) -> None:
        """Backfill record_id into health/finance records created in the same turn.

        solo_record and domain tools run concurrently via asyncio.gather(),
        so domain records are created without a record_id. After all tools in the
        turn complete, this method links them to the most recent record.
        """
        if not self._created_record_ids:
            self._pending_health_ids.clear()
            self._pending_finance_ids.clear()
            return

        record_id = list(self._created_record_ids)[-1]
        for health_id in self._pending_health_ids:
            self.store.update_health_record(health_id, record_id=record_id)
        self._pending_health_ids.clear()

        for fid in self._pending_finance_ids:
            self.store.update_finance_transaction(fid, record_id=record_id)
        self._pending_finance_ids.clear()

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
            SoloDomainTool(_tool_report_list(), self._handle_report_list),
            SoloDomainTool(_tool_report_show(), self._handle_report_show),
            SoloDomainTool(_tool_report_delete(), self._handle_report_delete),
            SoloDomainTool(_tool_report_update(), self._handle_report_update),
            SoloDomainTool(_tool_report_search(), self._handle_report_search),
            SoloDomainTool(_tool_view(), self._handle_view),
            SoloDomainTool(_tool_search(), self._handle_search),
            SoloDomainTool(_tool_show(), self._handle_show),
            SoloDomainTool(_tool_todos(), self._handle_todos),
            SoloDomainTool(_tool_add_todo(), self._handle_add_todo),
            SoloDomainTool(_tool_experiments(), self._handle_experiments),
            SoloDomainTool(_tool_patterns(), self._handle_patterns),
            SoloDomainTool(_tool_rulebook(), self._handle_rulebook),
            SoloDomainTool(_tool_done(), self._handle_done),
            SoloDomainTool(_tool_update_todo(), self._handle_update_todo),
            SoloDomainTool(_tool_update_record(), self._handle_update_record),
            SoloDomainTool(_tool_delete_record(), self._handle_delete_record),
            SoloDomainTool(_tool_status(), self._handle_status),
            SoloDomainTool(_tool_llm_usage(), self._handle_llm_usage),
            SoloDomainTool(_tool_get_now(), self._handle_get_now),
            SoloDomainTool(_tool_profile_update(), self._handle_profile_update),
            SoloDomainTool(_tool_remember(), self._handle_remember),
            SoloDomainTool(_tool_health_record(), self._handle_health_record),
            SoloDomainTool(_tool_update_health_record(), self._handle_update_health_record),
            SoloDomainTool(_tool_health_summary(), self._handle_health_summary),
            SoloDomainTool(_tool_finance_transaction(), self._handle_finance_transaction),
            SoloDomainTool(_tool_update_finance_transaction(), self._handle_update_finance_transaction),
            SoloDomainTool(_tool_finance_budget(), self._handle_finance_budget),
            SoloDomainTool(_tool_finance_summary(), self._handle_finance_summary),
            SoloDomainTool(_tool_suggest_reflection(), self._handle_suggest_reflection),
            SoloDomainTool(_tool_sync_context(), self._handle_sync_context),
            SoloDomainTool(_tool_visualize(), self._handle_visualize),
            SoloDomainTool(_tool_export(), self._handle_export),
            SoloDomainTool(_tool_heartbeat_task(), self._handle_heartbeat_task),
            SoloDomainTool(_tool_fetch_digest(), self._handle_fetch_digest),
            SoloDomainTool(_tool_projects(), self._handle_projects),
            SoloDomainTool(_tool_project_scan(), self._handle_project_scan),
            SoloDomainTool(_tool_project_create(), self._handle_project_create),
            SoloDomainTool(_tool_project_suggestions(), self._handle_project_suggestions),
            SoloDomainTool(_tool_project_review(), self._handle_project_review),
            SoloDomainTool(_tool_project_detail(), self._handle_project_detail),
            SoloDomainTool(_tool_project_update(), self._handle_project_update),
            SoloDomainTool(_tool_project_delete(), self._handle_project_delete),
            SoloDomainTool(_tool_project_complete(), self._handle_project_complete),
            SoloDomainTool(_tool_project_archive(), self._handle_project_archive),
            SoloDomainTool(_tool_project_reactivate(), self._handle_project_reactivate),
            SoloDomainTool(_tool_milestone_create(), self._handle_milestone_create),
            SoloDomainTool(_tool_milestone_update(), self._handle_milestone_update),
            SoloDomainTool(_tool_milestone_complete(), self._handle_milestone_complete),
            SoloDomainTool(_tool_milestone_delete(), self._handle_milestone_delete),
            SoloDomainTool(_tool_project_link_create(), self._handle_project_link_create),
            SoloDomainTool(_tool_project_link_delete(), self._handle_project_link_delete),
            SoloDomainTool(_tool_project_alias_create(), self._handle_project_alias_create),
            SoloDomainTool(_tool_project_link_backfill(), self._handle_project_link_backfill),
            SoloDomainTool(_tool_project_snapshot_create(), self._handle_project_snapshot_create),
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
                        pass  # duplicate link or constraint violation — silently ignore

            message = "收到～已记下这条。"
            if link_info:
                message += f" 已关联到项目「{link_info['project_title']}」。"

            result: dict[str, Any] = {
                "ok": True,
                "entry_id": entry.id,
                "record_id": record.id,
                "message": message,
                "_metadata": {
                    "app": "solo",
                    "domain_event": "record_created",
                    "record_ids": [record.id],
                },
            }
            if link_info:
                result["linked_project"] = link_info
            return result

        # No structured fields provided — entry saved but not yet structured.
        # It will be processed by the next `solo_process` / `process_pending()` call.
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
        self._created_record_ids.update(created)
        logger.info("_handle_import_records imported=%d", len(created))
        ids_hint = "，可通过 solo_search/view 获取" if len(created) > 5 else "：" + ", ".join(created)
        return {
            "ok": True,
            "record_ids": created,
            "imported": len(created),
            "message": f"收到～已记下 {len(created)} 条{ids_hint}。",
            "_metadata": {
                "app": "solo",
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
        from solo.gateway.todo_cron import schedule_one_shot_reminder

        reminder_message = _required_text(arguments, "message")
        due_at_utc, notify, local_due, delay_text = self._prepare_one_shot(arguments, time_field="remind_at")
        session_key = str(self._source_context.get("session_key") or "").strip()
        job = schedule_one_shot_reminder(
            "solo",
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
        category = _optional_text(arguments, "category")
        limit = int(arguments.get("limit") or 20)
        todos = self.store.list_todos(status=status, category=category, limit=limit)
        return {
            "ok": True,
            "todos": [todo.to_dict() for todo in todos],
            "message": _format_todos(todos),
        }

    async def _handle_add_todo(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Create a single personal todo item.

        Intended for explicit, user-stated action items the model captured from
        the current message (e.g. "这周要做 X", "明天记得 Y"). Call once
        per distinct todo; link to the originating record via ``record_id`` when
        the todo was mentioned alongside a record you just saved.
        """
        title = _required_text(arguments, "title")
        todo = SoloTodo(
            id=uuid4().hex[:12],
            record_id=str(arguments.get("record_id") or ""),
            title=title,
            category=str(arguments.get("category") or ""),
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
                "app": "solo",
                "domain_event": "todo_created",
                "todo_ids": [todo.id],
            },
        }

    async def _handle_experiments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = str(arguments.get("status") or "active").strip().lower()
        limit = int(arguments.get("limit") or 20)
        experiments = self.store.list_experiments(
            status=None if status in {"", "all"} else status,
            limit=limit,
        )
        return {
            "ok": True,
            "experiments": [item.to_dict() for item in experiments],
            "message": _format_experiments(experiments),
        }

    async def _handle_patterns(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from collections import Counter

        days = int(arguments.get("days") or 30)
        limit = int(arguments.get("limit") or 5)
        start_date = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        records = self.store.search_records(start_date=start_date, limit=300)
        if not records:
            return {"ok": False, "message": f"最近 {days} 天暂无记录，无法总结模式。"}

        sample_counts = Counter(record.sample_type or "neutral" for record in records)
        trigger_counts = Counter(record.trigger_scene for record in records if record.trigger_scene)
        break_counts = Counter(record.break_point for record in records if record.break_point)
        bridge_counts = Counter(record.bridge_action for record in records if record.bridge_action)
        design_counts = Counter(record.environment_design for record in records if record.environment_design)

        sections = [
            "## Sample Types\n" + "\n".join(f"- {name}: {count}" for name, count in sample_counts.most_common(limit)),
            "## Trigger Scenes\n" + ("\n".join(f"- {name}: {count}" for name, count in trigger_counts.most_common(limit)) or "- 暂无"),
            "## Break Points\n" + ("\n".join(f"- {name}: {count}" for name, count in break_counts.most_common(limit)) or "- 暂无"),
            "## Bridge Actions\n" + ("\n".join(f"- {name}: {count}" for name, count in bridge_counts.most_common(limit)) or "- 暂无"),
            "## Avoidance Designs\n" + ("\n".join(f"- {name}: {count}" for name, count in design_counts.most_common(limit)) or "- 暂无"),
        ]
        return {
            "ok": True,
            "records": [record.to_dict() for record in records],
            "message": "\n\n".join(sections),
        }

    async def _handle_rulebook(self, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = int(arguments.get("limit") or 5)
        records = self.store.list_records(limit=100)
        experiments = self.store.list_experiments(status="active", limit=limit)

        avoidance = [
            f"- 当 {record.trigger_scene} 时，提前 {record.environment_design}"
            for record in records
            if record.sample_type == "avoidance_design" and record.trigger_scene and record.environment_design
        ][:limit]
        bridge = [
            f"- 当 {record.trigger_scene} 时，先做 {record.bridge_action}"
            for record in records
            if record.sample_type == "tension_success" and record.trigger_scene and record.bridge_action
        ][:limit]
        failures = [
            f"- 当 {record.trigger_scene} 时，注意卡在 {record.break_point}；下一轮实验：{record.next_experiment}"
            for record in records
            if record.sample_type == "aware_failure" and record.trigger_scene and record.break_point
        ][:limit]
        experiment_lines = [
            f"- {item.title}: {item.hypothesis} -> {item.desired_action}（成功标准：{item.success_criteria}）"
            for item in experiments
        ]

        sections = [
            "## Avoidance Designs\n" + ("\n".join(avoidance) or "- 暂无"),
            "## Bridge Actions\n" + ("\n".join(bridge) or "- 暂无"),
            "## Failure Recovery Rules\n" + ("\n".join(failures) or "- 暂无"),
            "## Active Experiments\n" + ("\n".join(experiment_lines) or "- 暂无"),
        ]
        return {
            "ok": True,
            "experiments": [item.to_dict() for item in experiments],
            "message": "\n\n".join(sections),
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

        if not re.fullmatch(r"^[0-9a-f]{12}$", record_id):
            return {
                "ok": False,
                "message": (
                    f"❌ record_id '{record_id}' 不是有效的 12 位小写十六进制记录 ID。"
                    "请从之前工具返回的 _metadata.record_ids 中取用真实 ID，"
                    "或使用 solo_search / solo_view 查找后再更新。"
                ),
            }

        existing = self.store.get_record(record_id)
        if existing is None:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}

        # Valid fields for update
        VALID_FIELDS = (
            "summary", "tags", "emotion", "emotion_reason", "events", "period",
            "corrected_content", "related_people", "related_places", "date",
            "sample_type", "trigger_scene", "friction_signal", "awareness_timing",
            "break_point", "bridge_action", "environment_design", "next_experiment",
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
        # The rejection is returned as ok=False so the adapter surfaces it
        # as is_error=True; the model then writes its own natural reply.
        same_turn_guard_enabled = os.environ.get(_SOLO_DISABLE_SAME_TURN_UPDATE_GUARD) != "1"
        if same_turn_guard_enabled and record_id in self._created_record_ids:
            logger.info(
                "_handle_update_record same-turn guard rejected record_id=%s", record_id
            )
            return {
                "ok": False,
                "message": (
                    f"❌ 同轮创建保护：record_id={record_id} 是本轮刚创建的记录，"
                    "不允许用 solo_update_record 补充或润色。\n"
                    "请将所有已知字段在 solo_record 调用时一次性填入；"
                    "缺失或模糊的事实请用 solo_clarify 向用户确认，不要推断。\n"
                    "不要重试本次 update —— 直接用一句自然的话回复用户即可。"
                ),
            }

        # Layer 4 — Hallucinated-field rejection on fresh records. Independent
        # of Layer-2: Layer-2 only catches updates to records created in the
        # *current* turn, but the model can also pollute an older record by
        # pulling a subjective field out of conversation-history context (e.g.
        # a prior user correction like "改成积极" being replayed onto a brand
        # new record this turn). Layer-4 is the belt-and-suspenders guard for
        # that case — it rejects inferred subjective fields on any record
        # younger than SAME_TURN_RECENT_WINDOW_SECONDS.
        hallucination_guard_enabled = os.environ.get(_SOLO_DISABLE_HALLUCINATION_GUARD) != "1"
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
                        "请在 solo_record 调用时依据用户原文一次性填写；"
                        "如果用户没有明确说明，请保持为空或中性。\n"
                        "不要从对话历史中把之前用户纠正过的字段搬到这条新记录上。"
                    ),
                }

        # No-op shortcut: if every proposed field already matches the existing
        # value, short-circuit and surface the result as is_error=True with
        # metadata.noop=True so the adapter + engine loop guard can block any
        # second identical no-op. This breaks the self-reinforcing loop where
        # the model reads a no-op "success" as a reward and re-issues the tool.
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
        """Permanently delete an existing solo record."""
        record_id = _required_text(arguments, "record_id")
        success = self.store.delete_record(record_id)
        if success:
            return {"ok": True, "message": f"🗑️ 已永久删除记录 {record_id}。"}
        else:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {record_id} 的记录。"}

    async def _handle_update_health_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing health record."""
        health_record_id = _required_text(arguments, "health_record_id")
        if not re.fullmatch(r"^[0-9a-f]{12}$", health_record_id):
            return {
                "ok": False,
                "message": (
                    f"❌ health_record_id '{health_record_id}' 不是有效的 12 位小写十六进制 ID。"
                    "请从之前 solo_health_record 返回的 _metadata.record_ids 中取用真实 ID。"
                ),
            }

        existing = self.store.get_health_record(health_record_id)
        if existing is None:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {health_record_id} 的健康记录。"}

        VALID_FIELDS = (
            "subject", "date", "description", "body_part", "severity", "status",
            "medication_name", "dosage", "frequency", "duration",
            "exercise_type", "exercise_duration_min", "exercise_intensity",
            "sleep_hours", "sleep_quality", "mood", "mood_sentiment",
            "stress_level", "metrics_json", "tags",
        )
        updates: dict[str, Any] = {}
        for field in VALID_FIELDS:
            if field in arguments and arguments[field] is not None:
                updates[field] = arguments[field]

        if not updates:
            return {"ok": False, "message": "未提供任何更新字段。"}

        if "exercise_duration_min" in updates:
            updates["exercise_duration_min"] = int(updates["exercise_duration_min"])
        if "sleep_hours" in updates:
            updates["sleep_hours"] = float(updates["sleep_hours"])

        success = self.store.update_health_record(health_record_id, **updates)
        if success:
            return {"ok": True, "message": f"✅ 已成功更新健康记录 {health_record_id}。"}
        return {"ok": False, "message": f"❌ 未找到 ID 为 {health_record_id} 的健康记录。"}

    async def _handle_update_finance_transaction(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing finance transaction."""
        transaction_id = _required_text(arguments, "transaction_id")
        if not re.fullmatch(r"^[0-9a-f]{12}$", transaction_id):
            return {
                "ok": False,
                "message": (
                    f"❌ transaction_id '{transaction_id}' 不是有效的 12 位小写十六进制 ID。"
                    "请从之前 solo_finance_transaction 返回的 _metadata.record_ids 中取用真实 ID。"
                ),
            }

        existing = self.store.get_finance_transaction(transaction_id)
        if existing is None:
            return {"ok": False, "message": f"❌ 未找到 ID 为 {transaction_id} 的财务记录。"}

        new_type = arguments.get("type", existing.type)
        if new_type not in self._FINANCE_TYPES:
            return {"ok": False, "message": f"Invalid type '{new_type}'."}
        if "category" in arguments and not self._is_valid_finance_category(new_type, arguments["category"]):
            return {"ok": False, "message": f"Invalid category '{arguments['category']}' for type '{new_type}'."}
        if "amount" in arguments and float(arguments["amount"]) <= 0:
            return {"ok": False, "message": f"amount must be positive, got {arguments['amount']}"}

        VALID_FIELDS = (
            "type", "category", "amount", "currency", "date",
            "account", "counterparty", "description", "tags", "metrics_json",
        )
        updates: dict[str, Any] = {}
        for field in VALID_FIELDS:
            if field in arguments and arguments[field] is not None:
                updates[field] = arguments[field]

        if not updates:
            return {"ok": False, "message": "未提供任何更新字段。"}

        if "amount" in updates:
            updates["amount"] = float(updates["amount"])

        success = self.store.update_finance_transaction(transaction_id, **updates)
        if success:
            return {"ok": True, "message": f"✅ 已成功更新财务记录 {transaction_id}。"}
        return {"ok": False, "message": f"❌ 未找到 ID 为 {transaction_id} 的财务记录。"}

    async def _handle_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        status = self.store.status()
        message = (
            f"solo 状态：entries={status['entries']}，records={status['records']}，"
            f"todos={status['todos']}，experiments={status['experiments']}，"
            f"pending={status['pending_confirmations']}，path={status['path']}"
        )
        return {"ok": True, **status, "message": message}

    async def _handle_llm_usage(self, arguments: dict[str, Any]) -> dict[str, Any]:
        summary = self.store.llm_usage_summary()
        return {"ok": True, **summary, "message": format_solo_llm_usage(summary)}

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

    _HEALTH_STANDARD_CATEGORIES = frozenset({
        "medical", "symptom", "medication", "fitness",
        "sleep", "nutrition", "mental", "vital", "period",
    })
    _HEALTH_VAGUE_NAMES = frozenset({
        "other", "misc", "general", "unknown", "custom", "test",
    })

    async def _handle_health_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
        category = _required_text(arguments, "category")
        item = _required_text(arguments, "item")

        if category not in self._HEALTH_STANDARD_CATEGORIES:
            if not category.isalpha() or not category.islower() or len(category) > 20:
                return {"ok": False, "error": f"Invalid category '{category}'. Use a standard category or a single lowercase English word."}
            if category in self._HEALTH_VAGUE_NAMES:
                return {"ok": False, "error": f"Category '{category}' is too vague. Use a descriptive name."}

        local_today = _now()[:10]
        subject_raw = str(arguments.get("subject") or "self").strip()
        record = SoloHealthRecord(
            id=uuid4().hex[:12],
            record_id="",
            date=str(arguments.get("date") or local_today),
            subject=subject_raw or "self",
            category=category,
            item=item,
            description=str(arguments.get("description") or ""),
            body_part=str(arguments.get("body_part") or ""),
            severity=str(arguments.get("severity") or ""),
            status=str(arguments.get("status") or "active"),
            medication_name=str(arguments.get("medication_name") or ""),
            dosage=str(arguments.get("dosage") or ""),
            frequency=str(arguments.get("frequency") or ""),
            duration=str(arguments.get("duration") or ""),
            exercise_type=str(arguments.get("exercise_type") or ""),
            exercise_duration_min=int(arguments.get("exercise_duration_min") or 0),
            exercise_intensity=str(arguments.get("exercise_intensity") or ""),
            sleep_hours=float(arguments.get("sleep_hours") or 0),
            sleep_quality=str(arguments.get("sleep_quality") or ""),
            mood=str(arguments.get("mood") or ""),
            mood_sentiment=str(arguments.get("mood_sentiment") or ""),
            stress_level=str(arguments.get("stress_level") or ""),
            metrics_json=str(arguments.get("metrics_json") or "{}"),
            tags=str(arguments.get("tags") or ""),
            source="agent",
            linked_memory_id=str(arguments.get("linked_memory_id") or ""),
            created_at=_now(),
            updated_at=_now(),
        )
        self.store.add_health_record(record)
        self._pending_health_ids.append(record.id)
        return {
            "ok": True,
            "message": f"健康记录已入库：{category}/{item} ({record.date})",
            "health_record_id": record.id,
            "_metadata": {
                "app": "solo",
                "domain_event": "health_record_created",
                "record_ids": [record.id],
            },
        }

    async def _handle_health_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        subject = str(arguments.get("subject") or "").strip() or None
        category = str(arguments.get("category") or "").strip() or None
        days = int(arguments.get("days") or 30)
        status = str(arguments.get("status") or "").strip() or None
        date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        records = self.store.list_health_records(
            subject=subject, category=category, status=status, date_from=date_from,
        )

        if not records:
            subj_desc = f" ({subject})" if subject else ""
            return {
                "ok": True, "total": 0,
                "message": f"过去 {days} 天{subj_desc}没有{' (' + category + ')' if category else ''}健康记录。",
            }

        from collections import Counter
        cat_counts = Counter(r.category for r in records)
        subj_counts = Counter(r.subject for r in records)
        items_summary = Counter(r.item for r in records).most_common(10)
        recent = [r.to_dict() for r in records[:10]]

        return {
            "ok": True, "total": len(records), "days": days,
            "subject_filter": subject, "category_filter": category,
            "by_category": dict(cat_counts), "by_subject": dict(subj_counts),
            "top_items": [{"item": i, "count": c} for i, c in items_summary],
            "recent_records": recent,
        }

    # ── Finance category validation ─────────────────────────────

    _EXPENSE_CATEGORIES = frozenset({
        "dining", "groceries", "transport", "shopping", "housing",
        "health", "education", "entertainment", "family", "social",
    })
    _INCOME_CATEGORIES = frozenset({
        "salary", "bonus", "refund", "gift", "other_income",
    })
    _INVEST_CATEGORIES = frozenset({
        "stocks", "fund", "bond", "crypto", "gold", "savings", "insurance",
    })
    _FINANCE_VAGUE_NAMES = frozenset({
        "other", "misc", "general", "unknown", "custom", "test",
    })
    _FINANCE_TYPES = frozenset({
        "expense", "income", "transfer", "invest_gain", "invest_loss",
    })

    def _is_valid_finance_category(self, txn_type: str, category: str) -> bool:
        """Validate category: preferred set passes; new categories must meet constraints."""
        preferred = self._EXPENSE_CATEGORIES | self._INCOME_CATEGORIES | self._INVEST_CATEGORIES
        if category in preferred:
            return True
        if category in self._FINANCE_VAGUE_NAMES:
            return False
        return category.isalpha() and category.islower() and len(category) <= 20

    # ── Finance transaction handler ─────────────────────────────

    async def _handle_finance_transaction(self, arguments: dict[str, Any]) -> dict[str, Any]:
        txn_type = _required_text(arguments, "type")
        category = _required_text(arguments, "category")
        amount = float(arguments.get("amount") or 0)
        if amount <= 0:
            return {"ok": False, "error": f"amount must be positive, got {amount}"}
        if txn_type not in self._FINANCE_TYPES:
            return {"ok": False, "error": f"Invalid type '{txn_type}'."}
        if not self._is_valid_finance_category(txn_type, category):
            return {"ok": False, "error": f"Invalid category '{category}' for type '{txn_type}'."}

        local_today = _now()[:10]
        txn = SoloFinanceTransaction(
            id=uuid4().hex[:12],
            record_id=str(arguments.get("record_id") or ""),
            date=str(arguments.get("date") or local_today),
            type=txn_type,
            category=category,
            amount=amount,
            currency=str(arguments.get("currency") or "CNY").upper(),
            account=str(arguments.get("account") or ""),
            counterparty=str(arguments.get("counterparty") or ""),
            description=str(arguments.get("description") or ""),
            tags=str(arguments.get("tags") or ""),
            source="agent",
            metrics_json=str(arguments.get("metrics_json") or "{}"),
            created_at=_now(),
            updated_at=_now(),
        )
        self.store.add_finance_transaction(txn)
        self._pending_finance_ids.append(txn.id)
        return {
            "ok": True,
            "message": f"财务记录已入库：{txn_type}/{category} {amount} {txn.currency} ({txn.date})",
            "transaction_id": txn.id,
            "_metadata": {
                "app": "solo",
                "domain_event": "finance_transaction_created",
                "record_ids": [txn.id],
            },
        }

    # ── Finance budget handler ──────────────────────────────────

    async def _handle_finance_budget(self, arguments: dict[str, Any]) -> dict[str, Any]:
        period = str(arguments.get("period") or "monthly").lower()
        category = str(arguments.get("category") or "").strip()
        amount = float(arguments.get("amount") or 0)
        if amount <= 0:
            return {"ok": False, "error": f"amount must be positive, got {amount}"}
        if period not in {"monthly", "weekly", "yearly"}:
            return {"ok": False, "error": f"Invalid period '{period}'."}

        existing = self.store.find_budget(period, category)
        if existing:
            self.store.update_finance_budget(
                existing.id, amount=amount,
                currency=str(arguments.get("currency") or existing.currency).upper(),
                name=str(arguments.get("name") or existing.name),
                note=str(arguments.get("note") or existing.note),
                updated_at=_now(),
            )
            return {"ok": True, "message": f"预算已更新：{period}/{category or '全部'} {amount}"}
        b = SoloFinanceBudget(
            id=uuid4().hex[:12], period=period, category=category, amount=amount,
            currency=str(arguments.get("currency") or "CNY").upper(),
            name=str(arguments.get("name") or ""), active=1,
            created_at=_now(), updated_at=_now(),
            note=str(arguments.get("note") or ""),
        )
        self.store.add_finance_budget(b)
        return {"ok": True, "message": f"预算已设置：{period}/{category or '全部'} {amount}"}

    # ── Finance summary handler ─────────────────────────────────

    async def _handle_finance_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from collections import Counter

        txn_type = str(arguments.get("type") or "").strip() or None
        category = str(arguments.get("category") or "").strip() or None
        account = str(arguments.get("account") or "").strip() or None
        days = int(arguments.get("days") or 30)
        date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        records = self.store.list_finance_transactions(
            type=txn_type, category=category, account=account, date_from=date_from,
        )
        if not records:
            return {"ok": True, "total": 0,
                    "message": f"过去 {days} 天没有相关财务记录。"}

        expense = sum(r.amount for r in records if r.type == "expense" and r.currency == "CNY")
        income = sum(r.amount for r in records if r.type == "income" and r.currency == "CNY")
        invest_net = (sum(r.amount for r in records if r.type == "invest_gain" and r.currency == "CNY")
                      - sum(r.amount for r in records if r.type == "invest_loss" and r.currency == "CNY"))
        by_category = Counter(r.category for r in records)

        return {
            "ok": True, "total": len(records), "days": days,
            "type_filter": txn_type, "category_filter": category,
            "account_filter": account,
            "expense_cny": round(expense, 2), "income_cny": round(income, 2),
            "invest_net_cny": round(invest_net, 2),
            "by_category": dict(by_category),
            "recent_records": [r.to_dict() for r in records[:10]],
        }

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
            f"- [{r.date}] {r.summary} sample={r.sample_type} trigger={r.trigger_scene} "
            f"break={r.break_point} design={r.environment_design} next={r.next_experiment}"
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
        end_date = datetime.now(timezone.utc).date().isoformat()
        records = self.store.search_records(start_date=start_date, limit=200)

        if not records:
            return {"ok": False, "message": f"最近 {days} 天暂无记录，无法可视化。"}

        from collections import Counter

        if viz_type == "emotion_distribution":
            emotions = [r.emotion for r in records if r.emotion]
            counts = Counter(emotions)
            total = sum(counts.values())
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
            lines = ["| 周 | 一 | 二 | 三 | 四 | 五 | 六 | 日 |", "|---|---|---|---|---|---|---|---|"]
            current = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
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
                if r.sample_type != "neutral":
                    content += (
                        f"- 样本类型：{r.sample_type}\n"
                        f"- 触发场景：{r.trigger_scene}\n"
                        f"- 断裂点：{r.break_point}\n"
                        f"- 跨越动作：{r.bridge_action}\n"
                        f"- 规避设计：{r.environment_design}\n"
                        f"- 下一轮实验：{r.next_experiment}\n\n---\n\n"
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
            name="solo-fetch-digest",
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
        from solo.feed_digest import run_feed_digest

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
        from solo.core.models import Milestone
        default_milestones = [
            Milestone(
                id=str(uuid4()), project_id=project.id,
                title=f"创建 {title}", status="completed",
                completed_at=now, created_at=now, updated_at=now, sort_order=0,
            ),
            Milestone(
                id=str(uuid4()), project_id=project.id,
                title=f"完成 {title}", status="pending",
                created_at=now, updated_at=now, sort_order=1,
            ),
        ]
        for ms in default_milestones:
            self.store.create_milestone(ms)
        milestones = arguments.get("milestones") or []
        if isinstance(milestones, list):
            for i, ms_title in enumerate(milestones):
                if isinstance(ms_title, str) and ms_title.strip():
                    ms = Milestone(
                        id=str(uuid4()),
                        project_id=project.id,
                        title=ms_title.strip(),
                        created_at=now,
                        updated_at=now,
                        sort_order=i + 2,
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
        from solo.core.models import Milestone
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

        if not health:
            health = "normal"
            if project.target_date:
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
                f"近7天活跃度{activity_7d}。"
            )

        snapshot = ProjectSnapshot(
            id=str(uuid4()),
            project_id=project_id,
            snapshot_date=now.strftime("%Y-%m-%d"),
            summary=summary,
            health=health,
            completion_pct=completion_pct,
            activity_7d=activity_7d,
            open_blocker_count=0,
            next_action=next_action,
            created_at=now.isoformat(),
        )
        self.store.create_project_snapshot(snapshot)
        return {"ok": True, "snapshot": snapshot.to_dict()}

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
        return self.name in {
            "solo_view",
            "solo_search",
            "solo_show",
            "solo_status",
            "solo_experiments",
            "solo_patterns",
            "solo_rulebook",
        }

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        raw = arguments.model_dump()
        try:
            result = await self._domain_tool.handler(raw)
            output = str(result.get("message") or result)
            if not output.strip():
                logger.warning("solo tool %s returned empty output raw_keys=%s", self.name, list(result.keys())[:10])
            metadata: dict[str, Any] = {}
            if result.get("path"):
                metadata["paths"] = [str(result["path"])]
            handler_metadata = result.get("_metadata")
            if isinstance(handler_metadata, dict):
                metadata.update(handler_metadata)
            is_error = bool(result.get("_is_error")) or result.get("ok") is False
            return ToolResult(output=output, metadata=metadata, is_error=is_error)
        except Exception as exc:
            logger.warning("solo tool %s execution failed: %s args_preview=%s",
                           self.name, exc, {k: str(v)[:100] for k, v in raw.items()})
            return ToolResult(output=str(exc), is_error=True)


def build_oh_registry(registry: SoloToolRegistry) -> ToolRegistry:
    """Build an OpenHarness ToolRegistry from a SoloToolRegistry."""
    oh_registry = ToolRegistry()
    for domain_tool in registry.tools():
        oh_registry.register(_SoloToolAdapter(domain_tool))
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
        "solo_record",
        (
            "Record a SINGLE-DATE solo entry when the intent and core content are clear enough to understand. "
            "IMPORTANT: This tool only accepts ONE date. If the user's message spans multiple dates "
            "(e.g. '昨天11点睡的，今天7点醒来'), use solo_import_records to split into separate records per date. "
            "Do NOT call this when the user's intent is ambiguous or the record is unintelligible — "
            "call solo_clarify instead. Fill in ALL known structured fields (summary, tags, emotion, "
            "emotion_reason, related_people, related_places, date, period, events) in this single call — "
            "missing or uncertain facts must be clarified via solo_clarify, NEVER patched in later via "
            "solo_update_record. Subjective fields (emotion, emotion_reason, sample_type, trigger_scene, "
            "break_point, bridge_action, environment_design, next_experiment) must come from the user's "
            "own words; when the user did not state them, leave them empty / neutral. Do not add unstated "
            "causes, diagnoses, motives, timelines, or explanations. Do NOT rewrite a future plan as a "
            "past event; preserve the original tense. SIDE-EFFECT CHECK: If this message reveals persistent personal facts (chronic health conditions, new relationships, life structure changes, long-term preferences), also call solo_remember in the SAME turn to store them in long-term memory. If this message contains health-related events (symptoms, medication, exercise, sleep, mood changes, medical visits, vital signs), also call solo_health_record in the SAME turn — once per distinct health event. Health info may be mentioned INCIDENTALLY as a side note (e.g. '小红没去游乐场，因为她去体检了' → extract 小红's medical visit). Set the subject parameter correctly — default is 'self'; use the family member's name if the event is about them. If this message contains money flows (spending, income, transfers, or an investment GAIN/LOSS RESULT with specific amounts), also call solo_finance_transaction in the SAME turn — once per distinct transaction. Extract ONLY the EXACT amount the user stated; do NOT estimate or split. For investment, record only the gain/loss result (e.g. '基金赚了300'), NOT buy/sell actions. If the user sets a spending budget, call solo_finance_budget."
        ),
        [
            ("content", "string", "Faithful paraphrase of the user's current message. Must preserve all facts, opinions, and claims the user actually expressed. Do NOT add facts, opinions, or reflections the user did not state in this turn — even if a recent conversation topic suggests them.", True),
            ("corrected_content", "string", "Cleanup of speech-to-text artifacts in `content` ONLY: removing redundancy/filler words, fixing typos, adding punctuation, smoothing broken grammar. Think copy-editor pass, not rewrite. MUST NOT introduce any fact, opinion, or claim that is not already present in `content` — every fact in `corrected_content` must also appear in `content`. NEVER change the event tense (planned→happened / future→past). Example: 'too late last night, will watch today' → keep the future tense, do NOT rewrite as 'watched last night'.", False),
            ("summary", "string", f"One-sentence summary (≤{SUMMARY_MAX_LENGTH} chars). Keep semantics complete and grammar natural — do not over-compress.", False),
            ("tags", "string", "Comma-separated tags.", False),
            ("emotion", "string", f"Short emotion keyword (≤{EMOTION_MAX_LENGTH} chars), e.g. 积极/消极/中性/复杂. Must NOT be a full sentence.", False),
            ("date", "string", "YYYY-MM-DD. Only provide this if the user explicitly mentions a specific date (e.g. '昨天', '5月18日', '上周三'). If no date is mentioned, leave this empty and the system will default to today's local date.", False),
            ("period", "string", "Semantic time period extracted from content (e.g. 凌晨, 上午).", False),
            ("events", "string", "Holidays, anniversaries, or birthdays.", False),
            ("emotion_reason", "string", "Brief reason for the emotion label.", False),
            ("related_people", "string", "Comma-separated people mentioned.", False),
            ("related_places", "string", "Comma-separated places mentioned.", False),
            ("source", "string", "Record source, e.g. 原始/补录.", False),
            ("linked_project", "string", "Project title or alias this record is related to. Fill this when the record content clearly relates to an ongoing project (e.g. progress update, milestone, activity log). The system will auto-link the record to the matched project.", False),
        ],
    )


def _tool_import_records() -> ToolDefinition:
    return ToolDefinition(
        name="solo_import_records",
        description=(
            "Import multiple structured records, each with its own date. "
            "Use when: (1) a single message contains events spanning MULTIPLE dates "
            "(e.g. '昨天11点睡的，今天7点醒来') — split by date, assign correct date to each; "
            "(2) batch import from messy diary entries or chat logs. "
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
        "Generate weekly, monthly, or yearly solo report. "
        "Supports custom date ranges to generate reports for any past period (e.g. '上周的周报', '3月份的月报').",
        [
            ("type", "string", "weekly/monthly/yearly.", True),
            ("start_date", "string", "Optional start date (YYYY-MM-DD). If omitted, defaults to 7/30/365 days ago based on type.", False),
            ("end_date", "string", "Optional end date (YYYY-MM-DD). If omitted, defaults to today.", False),
        ],
    )


def _tool_report_list() -> ToolDefinition:
    return _definition(
        "solo_report_list",
        "List all existing reports, optionally filtered by type. Returns id, type, and created_at for each.",
        [("type", "string", "Optional filter: weekly/monthly/yearly.", False)],
    )


def _tool_report_show() -> ToolDefinition:
    return _definition(
        "solo_report_show",
        "Show the full content of a specific report by its ID.",
        [("report_id", "string", "The report ID to show.", True)],
    )


def _tool_report_delete() -> ToolDefinition:
    return _definition(
        "solo_report_delete",
        "Permanently delete a report by its ID.",
        [("report_id", "string", "The report ID to delete.", True)],
    )


def _tool_report_update() -> ToolDefinition:
    return _definition(
        "solo_report_update",
        "Update/replace the content of an existing report.",
        [
            ("report_id", "string", "The report ID to update.", True),
            ("content", "string", "New markdown content for the report.", True),
        ],
    )


def _tool_report_search() -> ToolDefinition:
    return _definition(
        "solo_report_search",
        "Search reports by keyword in their content. Returns matching report IDs and metadata.",
        [("keyword", "string", "Search keyword.", True)],
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


def _tool_add_todo() -> ToolDefinition:
    return _definition(
        "solo_add_todo",
        (
            "Create a new personal todo item. Use when the user explicitly lists "
            "things they plan to do, need to do, or want to be reminded of "
            "(e.g. '这周要...', '明天记得...', '买...'). "
            "Call this tool once per distinct todo; do NOT bundle several items "
            "into a single call. If the todo was mentioned alongside a record "
            "you just saved via solo_record, pass its record_id so the todo "
            "stays linked to its source."
        ),
        [
            ("title", "string", "Clear, actionable todo title describing one concrete task.", True),
            ("category", "string", "Category: 健康/家庭/社交/购物/学习/工作/家务/其他.", False),
            ("priority", "string", "Priority: high/medium/low (default medium).", False),
            ("due_date", "string", "Optional due date YYYY-MM-DD.", False),
            ("record_id", "string", "Optional related solo_record id returned by solo_record.", False),
        ],
    )


def _tool_experiments() -> ToolDefinition:
    return _definition(
        "solo_experiments",
        "List behavior experiments derived from solo records, optionally filtered by status.",
        [
            ("status", "string", "Experiment status: active/completed/abandoned/all. Defaults to active.", False),
            ("limit", "integer", "Number of experiments.", False),
        ],
    )


def _tool_patterns() -> ToolDefinition:
    return _definition(
        "solo_patterns",
        "Summarize recent trigger scenes, break points, bridge actions, and avoidance designs from solo records.",
        [
            ("days", "integer", "How many recent days to analyze. Defaults to 30.", False),
            ("limit", "integer", "Number of items per section. Defaults to 5.", False),
        ],
    )


def _tool_rulebook() -> ToolDefinition:
    return _definition(
        "solo_rulebook",
        "Generate a reusable personal rulebook from recent solo records and active behavior experiments.",
        [("limit", "integer", "Maximum rules per section.", False)],
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
        (
            "Modify an EXISTING structured record (one that was created BEFORE this user turn) "
            "ONLY when (a) the user's latest message explicitly points out a factual mistake "
            "(e.g. '刚才说错了，其实是...', '改成已看'), OR (b) visible record/search results "
            "directly evidence a mistake. "
            "FORBIDDEN on a record you just created in this same turn via solo_record or "
            "solo_import_records — such calls are rejected with is_error and do NOT modify the "
            "record. If you forgot a field, the right move is to fold it into the next solo_record "
            "call or ask the user via solo_clarify, not to patch it in via update. "
            "Subjective / inferential fields (emotion, emotion_reason, sample_type, trigger_scene, "
            "friction_signal, awareness_timing, break_point, bridge_action, environment_design, "
            "next_experiment) must NOT be inferred and added via update — only set them when the "
            "user explicitly states them IN THIS TURN. "
            "CRITICAL: a prior-turn correction like '改成积极' applied to one record does NOT "
            "authorize you to call this tool to apply the same label to a different, newer record. "
            "Each record's subjective fields are independent and must come from the current message. "
            "Also do NOT re-call this tool with identical arguments: if the prior result said the "
            "update was a no-op (is_error), stop — do not issue another call for the same record. "
            "Use ONLY a valid 12-character lowercase hex ID returned by a previous tool; "
            "if you do not know the ID, call solo_search or solo_view first."
        ),
        [
            ("record_id", "string", "The 12-character lowercase hex ID of the record to update.", True, {"pattern": "^[0-9a-f]{12}$"}),
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
            ("trigger_scene", "string", "New trigger scene.", False),
            ("friction_signal", "string", "New friction signal.", False),
            ("awareness_timing", "string", "New awareness timing.", False),
            ("break_point", "string", "New break point.", False),
            ("bridge_action", "string", "New bridge action.", False),
            ("environment_design", "string", "New environment design.", False),
            ("next_experiment", "string", "New next experiment.", False),
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


def _tool_llm_usage() -> ToolDefinition:
    return _definition(
        "solo_llm_usage",
        "Report how many LLM calls solo has made so far and how many input/output tokens were consumed, broken down by model name. Use this when the user asks about LLM usage, token consumption, or API call costs.",
        [],
    )


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
            "Call this tool directly whenever the user states a stable personal fact — even if no solo_record is needed for the same message. "
            "For transient/evolving info that may change within months (current habits, temporary preferences), use solo_profile_update instead."
        ),
        [
            ("title", "string", "A short English title for this memory entry (used as filename, ASCII only, e.g. 'family_members', 'medical_history').", True),
            ("content", "string", "The markdown content to store. Be factual and concise.", True),
        ],
    )


def _tool_health_record() -> ToolDefinition:
    return _definition(
        "solo_health_record",
        (
            "Record a STRUCTURED health-related event into the dedicated health database. "
            "Call this tool whenever the user's message contains information about: "
            "physical symptoms, medical visits, medications, exercise/fitness activities, "
            "sleep patterns, nutrition/diet, mental health/mood changes, or vital signs. "
            "IMPORTANT: Health info may be mentioned INCIDENTALLY as a side note in a daily record "
            "(e.g. '小红没去游乐场，因为她去体检了' → extract 小红's medical visit). "
            "Scan the ENTIRE message for health signals, not just the main topic. "
            "Call this in the SAME TURN as solo_record when the user's message "
            "contains both daily events AND health information. "
            "You may call this tool MULTIPLE TIMES per turn if the message contains "
            "different types of health events (e.g. exercise + medication). "
            "For STABLE health facts (chronic conditions, allergies), use solo_remember instead. When recording mood (category=mental), always classify the sentiment as positive, neutral, or negative in the mood_sentiment field."
        ),
        [
            ("category", "string",
             "Health category. PREFERRED: Use one of these standard categories if applicable: "
             "medical (doctor visits, checkups, surgery), "
             "symptom (headache, allergy, pain, fatigue), "
             "medication (drugs, prescriptions, supplements), "
             "fitness (running, swimming, gym, yoga), "
             "sleep (sleep duration, quality, insomnia), "
             "nutrition (diet habits, supplements, fasting), "
             "mental (mood, stress, anxiety, meditation), "
             "vital (weight, heart rate, blood pressure, temperature). "
             "If NONE of the above fit, you may create a new category using a single lowercase English word "
             "(e.g. 'dental', 'dermatology'). Do NOT use vague names like 'other' or 'misc'.",
             True),
            ("item", "string", "Primary item name (e.g. '跑步', '布洛芬', '头疼', '年度体检').", True),
            ("date", "string", "Date in YYYY-MM-DD format. Defaults to today.", False),
            ("subject", "string",
             "Who this health record is about: 'self' (the user), or a family member name (e.g. '小明', '小红'). "
             "Default: 'self'. Set this when the health event is about a family member, especially children.",
             False),
            ("description", "string", "Detailed description of the health event.", False),
            ("body_part", "string", "Affected body part (e.g. '膝盖', '头', '腰').", False),
            ("severity", "string", "Severity: mild, moderate, severe. Leave empty if N/A.", False),
            ("status", "string", "Status: active, resolved, chronic, recurring. Default: active.", False),
            ("medication_name", "string", "Medication name (for category=medication).", False),
            ("dosage", "string", "Dosage (e.g. '1颗', '5ml').", False),
            ("frequency", "string", "Frequency (e.g. '每日两次', '按需').", False),
            ("duration", "string", "Duration (e.g. '2小时', '3天').", False),
            ("exercise_type", "string", "Exercise type (for category=fitness).", False),
            ("exercise_duration_min", "integer", "Exercise duration in minutes.", False),
            ("exercise_intensity", "string", "Exercise intensity: low, moderate, high.", False),
            ("sleep_hours", "number", "Hours of sleep (for category=sleep).", False),
            ("sleep_quality", "string", "Sleep quality: good, fair, poor.", False),
            ("mood", "string", "Mood description (for category=mental).", False),
            ("mood_sentiment", "string", "Sentiment classification of the mood: positive, neutral, or negative. Required when mood is set.", False),
            ("stress_level", "string", "Stress level: low, moderate, high.", False),
            ("metrics_json", "string",
             "JSON string for extra metrics (e.g. '{\"weight_kg\": 72.5, \"steps\": 8000}').",
             False),
            ("tags", "string", "Comma-separated tags.", False),
        ],
    )


def _tool_update_health_record() -> ToolDefinition:
    return _definition(
        "solo_update_health_record",
        (
            "Modify an EXISTING health record created in a previous turn. "
            "Use when the user's latest message corrects or adds factual details to a prior health event "
            "(e.g. '那药要吃三个月', '剂量改成一天三次'). "
            "FORBIDDEN on a health record you just created in the same turn — if you forgot a field, "
            "fold it into the next solo_record instead. "
            "Use ONLY the 12-character lowercase hex ID returned by a previous solo_health_record call."
        ),
        [
            ("health_record_id", "string", "The 12-character lowercase hex ID of the health record to update.", True, {"pattern": "^[0-9a-f]{12}$"}),
            ("subject", "string", "Who this health record is about: 'self' or a family member name.", False),
            ("date", "string", "Date in YYYY-MM-DD format.", False),
            ("description", "string", "Detailed description of the health event.", False),
            ("body_part", "string", "Affected body part.", False),
            ("severity", "string", "Severity: mild, moderate, severe.", False),
            ("status", "string", "Status: active, resolved, chronic, recurring.", False),
            ("medication_name", "string", "Medication name.", False),
            ("dosage", "string", "Dosage.", False),
            ("frequency", "string", "Frequency.", False),
            ("duration", "string", "Duration (e.g. '3个月', '2小时').", False),
            ("exercise_type", "string", "Exercise type.", False),
            ("exercise_duration_min", "integer", "Exercise duration in minutes.", False),
            ("exercise_intensity", "string", "Exercise intensity: low, moderate, high.", False),
            ("sleep_hours", "number", "Hours of sleep.", False),
            ("sleep_quality", "string", "Sleep quality: good, fair, poor.", False),
            ("mood", "string", "Mood description.", False),
            ("mood_sentiment", "string", "Sentiment: positive, neutral, negative.", False),
            ("stress_level", "string", "Stress level: low, moderate, high.", False),
            ("metrics_json", "string", "JSON string for extra metrics.", False),
            ("tags", "string", "Comma-separated tags.", False),
        ],
    )


def _tool_health_summary() -> ToolDefinition:
    return _definition(
        "solo_health_summary",
        (
            "Query structured health records for a given time range, subject, and/or category. "
            "Use when the user asks about their own or a family member's health history, medication usage, "
            "exercise patterns, sleep quality, etc. "
            "Returns aggregated statistics and recent records."
        ),
        [
            ("subject", "string",
             "Filter by subject: 'self' (the user) or a family member name (e.g. '小明', '小红'). "
             "Leave empty to query all subjects.",
             False),
            ("category", "string",
             "Filter by health category: medical, symptom, medication, fitness, sleep, nutrition, mental, vital.",
             False),
            ("days", "integer", "Look back N days (default 30).", False),
            ("status", "string", "Filter by status: active, resolved, chronic, recurring.", False),
        ],
    )


def _tool_finance_transaction() -> ToolDefinition:
    return _definition(
        "solo_finance_transaction",
        (
            "Record a STRUCTURED finance transaction into the dedicated finance database. "
            "Call this whenever the user's message contains a money flow: spending, income, "
            "transfer, or an investment gain/loss RESULT. Extract the EXACT amount the user "
            "stated — do NOT estimate, infer, or split amounts the user did not specify. "
            "IMPORTANT: Finance info may appear INCIDENTALLY in a daily record "
            "(e.g. '和朋友吃饭花了120' → record expense 120). Scan the ENTIRE message. "
            "Call this in the SAME TURN as solo_record when the message contains both daily events "
            "AND money flows. You may call MULTIPLE TIMES per turn for distinct transactions. "
            "For investment, only record the GAIN or LOSS result (e.g. '基金赚了300' → invest_gain 300), "
            "NOT buy/sell actions. For STABLE financial facts (monthly salary, mortgage rate), "
            "use solo_remember instead."
        ),
        [
            ("type", "string",
             "Transaction type. MUST be one of: "
             "expense (dining, transport, shopping, housing, etc.), "
             "income (salary, bonus, refund, gift received), "
             "transfer (moving money between own accounts), "
             "invest_gain (realized/unrealized investment profit, interest, dividend received), "
             "invest_loss (realized/unrealized investment loss).",
             True),
            ("category", "string",
             "Category. For expense PREFER: dining, groceries, transport, shopping, housing, "
             "health, education, entertainment, family, social. "
             "For income PREFER: salary, bonus, refund, gift, other_income. "
             "For invest_gain/loss PREFER: stocks, fund, bond, crypto, gold, savings, insurance. "
             "If none fit, use a single lowercase English word. No vague names like 'other'/'misc'.",
             True),
            ("amount", "number",
             "Exact amount in the original currency (positive number). Extract ONLY what the user "
             "stated. Do not split or estimate. e.g. 'AA花了120' → 120 (per person), not 240. "
             "For invest_loss, store the POSITIVE loss amount (e.g. '亏了500' → 500).",
             True),
            ("currency", "string",
             "ISO currency code (CNY, USD, HKD, EUR, ...). Default CNY. Not converted, just labeled.",
             False),
            ("date", "string", "YYYY-MM-DD. Defaults to today.", False),
            ("account", "string",
             "Optional note: payment method / account (支付宝, 微信, 招行卡, ...). Searchable but not separately aggregated.",
             False),
            ("counterparty", "string",
             "Counterparty person/company (e.g. 同事老王, 房东). Optional.",
             False),
            ("description", "string",
             "Detailed description. Put investment target info (e.g. '茅台') here.",
             False),
            ("tags", "string", "Comma-separated tags.", False),
        ],
    )


def _tool_update_finance_transaction() -> ToolDefinition:
    return _definition(
        "solo_update_finance_transaction",
        (
            "Modify an EXISTING finance transaction created in a previous turn. "
            "Use when the user's latest message corrects a prior money flow record "
            "(e.g. '刚才那笔不是 35 是 53', '那笔打车费是 AA 的'). "
            "FORBIDDEN on a transaction you just created in the same turn. "
            "Use ONLY the 12-character lowercase hex ID returned by a previous solo_finance_transaction call."
        ),
        [
            ("transaction_id", "string", "The 12-character lowercase hex ID of the transaction to update.", True, {"pattern": "^[0-9a-f]{12}$"}),
            ("type", "string",
             "Transaction type: expense, income, transfer, invest_gain, invest_loss.", False),
            ("category", "string",
             "Category. For expense prefer: dining, groceries, transport, shopping, housing, health, education, entertainment, family, social. "
             "For income prefer: salary, bonus, refund, gift, other_income. "
             "For invest_gain/loss prefer: stocks, fund, bond, crypto, gold, savings, insurance.", False),
            ("amount", "number", "Exact positive amount.", False),
            ("currency", "string", "ISO currency code. Default CNY.", False),
            ("date", "string", "YYYY-MM-DD.", False),
            ("account", "string", "Payment method / account note.", False),
            ("counterparty", "string", "Counterparty person/company.", False),
            ("description", "string", "Detailed description.", False),
            ("tags", "string", "Comma-separated tags.", False),
        ],
    )


def _tool_finance_budget() -> ToolDefinition:
    return _definition(
        "solo_finance_budget",
        (
            "Set or update a recurring spending budget. Use when the user sets a spending limit, "
            "e.g. '餐饮预算每月2000', '这个月尽量控制在5000以内'. "
            "If a budget for the same period+category already exists, update its amount rather than "
            "creating a duplicate. category='' means a total budget across all categories."
        ),
        [
            ("period", "string",
             "Budget period: monthly, weekly, yearly. Default monthly.", False),
            ("category", "string",
             "Spending category this budget limits (dining, transport, ...). "
             "Leave empty for a total budget across all categories.", False),
            ("amount", "number",
             "Budget amount (in the user's main currency, default CNY).", True),
            ("currency", "string", "ISO currency code. Default CNY.", False),
            ("name", "string", "Budget name (optional).", False),
            ("note", "string", "Note.", False),
        ],
    )


def _tool_finance_summary() -> ToolDefinition:
    return _definition(
        "solo_finance_summary",
        (
            "Query structured finance transactions for a time range. "
            "Use when the user asks about spending, income, or investment gains/losses history. "
            "Returns aggregated statistics and recent transactions."
        ),
        [
            ("type", "string",
             "Filter by transaction type: expense, income, transfer, invest_gain, invest_loss.",
             False),
            ("category", "string", "Filter by category.", False),
            ("account", "string", "Filter by account note.", False),
            ("days", "integer", "Look back N days (default 30).", False),
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
            ("type", "string", "Type of visualization: emotion_distribution, tag_cloud, activity_heatmap, sample_type_distribution.", False),
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


def _tool_fetch_digest() -> ToolDefinition:
    return _definition(
        "solo_fetch_digest",
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
        "solo_projects",
        (
            "List user's projects with status and progress. "
            "Use when the user asks about their projects, or when you need project context to make better decisions."
        ),
        [
            ("status", "string", "Filter: 'active' (default), 'completed', 'archived', 'all'.", False),
        ],
    )


def _tool_project_scan() -> ToolDefinition:
    return _definition(
        "solo_project_scan",
        (
            "Scan recent records to discover potential new projects. "
            "Use proactively when you notice recurring themes, goal-oriented behavior, or sustained tracking "
            "in the user's records that might indicate an untracked project."
        ),
        [],
    )


def _tool_project_create() -> ToolDefinition:
    return _definition(
        "solo_project_create",
        (
            "Create a new project. Use when the user explicitly states a new goal, commitment, or ongoing endeavor, "
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
        "solo_project_suggestions",
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
        "solo_project_review",
        (
            "Accept or reject a project suggestion. Use after reviewing suggestions from solo_project_suggestions."
        ),
        [
            ("suggestion_id", "string", "ID of the suggestion to review.", True),
            ("action", "string", "'accept' or 'reject'.", True),
        ],
    )


def _tool_project_detail() -> ToolDefinition:
    return _definition(
        "solo_project_detail",
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
        "solo_project_update",
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
        "solo_project_delete",
        "Delete a project and all its associated data (milestones, links, aliases). Use for removing duplicate or erroneous projects.",
        [
            ("project_id", "string", "Project ID to delete.", True),
        ],
    )


def _tool_project_complete() -> ToolDefinition:
    return _definition(
        "solo_project_complete",
        "Mark a project as completed.",
        [
            ("project_id", "string", "Project ID.", True),
        ],
    )


def _tool_project_archive() -> ToolDefinition:
    return _definition(
        "solo_project_archive",
        "Archive a project (soft delete, can be reactivated later).",
        [
            ("project_id", "string", "Project ID.", True),
            ("reason", "string", "Reason for archiving.", False),
        ],
    )


def _tool_project_reactivate() -> ToolDefinition:
    return _definition(
        "solo_project_reactivate",
        "Reactivate an archived project.",
        [
            ("project_id", "string", "Project ID.", True),
        ],
    )


def _tool_milestone_create() -> ToolDefinition:
    return _definition(
        "solo_milestone_create",
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
        "solo_milestone_update",
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
        "solo_milestone_complete",
        "Mark a milestone as completed. Supports completed_at parameter to set the real completion date instead of now.",
        [
            ("milestone_id", "string", "Milestone ID.", True),
            ("completed_at", "string", "Real completion date (YYYY-MM-DD). If omitted, uses current time.", False),
        ],
    )


def _tool_milestone_delete() -> ToolDefinition:
    return _definition(
        "solo_milestone_delete",
        "Delete a milestone.",
        [
            ("milestone_id", "string", "Milestone ID to delete.", True),
        ],
    )


def _tool_project_link_create() -> ToolDefinition:
    return _definition(
        "solo_project_link_create",
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
        "solo_project_link_delete",
        "Remove a project link.",
        [
            ("link_id", "string", "Link ID to delete.", True),
        ],
    )


def _tool_project_alias_create() -> ToolDefinition:
    return _definition(
        "solo_project_alias_create",
        "Add an alias to a project for easier identification.",
        [
            ("project_id", "string", "Project ID.", True),
            ("alias", "string", "Alias name.", True),
            ("source", "string", "Source of the alias (user, migration, ai).", False),
        ],
    )


def _tool_project_link_backfill() -> ToolDefinition:
    return _definition(
        "solo_project_link_backfill",
        (
            "Bulk-link historical records to a project. Searches all records for matches "
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
        "solo_project_snapshot_create",
        (
            "Create a project snapshot capturing current progress, health, and next action. "
            "Auto-computes completion percentage, 7-day activity, and health status if not provided. "
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
    params: list[tuple[str, str, str, bool] | tuple[str, str, str, bool, dict[str, Any]]],
) -> ToolDefinition:
    properties: dict[str, Any] = {}
    for param in params:
        key, type_, desc, required = param[:4]
        prop: dict[str, Any] = {"type": type_, "description": desc}
        if len(param) > 4 and isinstance(param[4], dict):
            prop.update(param[4])
        properties[key] = prop
    return ToolDefinition(
        name=name,
        description=description,
        input_schema=ToolParameterSchema(
            type="object",
            properties=properties,
            required=[key for key, _, _, required, *_ in params if required],
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
        sample = f" [{record.sample_type}]" if record.sample_type and record.sample_type != "neutral" else ""
        lines.append(f"- [{record.id}] {record.date}{sample} {record.summary or record.raw_content}")
        lines.extend(_format_attachment_refs(store, record))
    return "\n".join(lines)


def _format_todos(todos: list[Any]) -> str:
    if not todos:
        return "暂无匹配待办。"
    return "\n".join(
        f"- [{todo.id}] {todo.status} {todo.priority} {todo.category} {todo.title}".strip()
        for todo in todos
    )


def _format_experiments(experiments: list[Any]) -> str:
    if not experiments:
        return "暂无匹配实验。"
    return "\n".join(
        f"- [{item.id}] {item.status} {item.title}：{item.hypothesis} -> {item.desired_action}".strip()
        for item in experiments
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


def _is_recently_created_record(
    record: "SoloRecord",
    *,
    window_seconds: int = SAME_TURN_RECENT_WINDOW_SECONDS,
) -> bool:
    """Return True when the record's created_at falls within the recency window.

    Used by the Layer-4 hallucination guard as a time-based fallback when the
    exact same-turn set in ``SoloToolRegistry._created_record_ids`` is not
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



def _resolve_project_by_name(store: SoloStore, name: str) -> Project | None:
    """Resolve a project title or alias to a Project object.

    Performs case-insensitive exact match against active project titles
    and all project aliases.  Returns None if no unique match is found.
    """
    name_norm = name.strip().lower()
    if not name_norm:
        return None
    active_projects = store.list_projects(status="active")
    # 1) Exact title match
    for p in active_projects:
        if p.title.strip().lower() == name_norm:
            return p
    # 2) Alias match across all active projects
    for p in active_projects:
        for alias in store.list_project_aliases(p.id):
            if alias.alias.strip().lower() == name_norm:
                return p
    # 3) Substring match on title (fallback — only if exactly one project matches)
    matches = [p for p in active_projects if name_norm in p.title.strip().lower() or p.title.strip().lower() in name_norm]
    if len(matches) == 1:
        return matches[0]
    return None


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
