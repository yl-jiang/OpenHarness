"""Self-log processing workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from openharness.utils.log import get_logger

from self_log.agent import OpenHarnessSelfLogAgent
from self_log.models import (
    PendingConfirmation,
    ProcessResult,
    ProfileUpdate,
    SelfLogEntry,
    SelfLogRecord,
    SelfLogReport,
)
from self_log.store import SelfLogStore

logger = get_logger(__name__)


class SelfLogProcessor:
    """Process raw entries into structured records."""

    def __init__(
        self,
        store: SelfLogStore,
        agent: OpenHarnessSelfLogAgent | None = None,
    ) -> None:
        self.store = store
        self.agent = agent or OpenHarnessSelfLogAgent()

    async def process_pending(
        self,
        *,
        limit: int = 20,
        backfill_missing_yesterday: bool = False,
        process_date: str | None = None,
        backfill_content: str | None = None,
    ) -> ProcessResult:
        if backfill_content:
            target_date = _previous_day(process_date)
            self.store.record(
                backfill_content,
                metadata={"record_date": target_date, "source": "补录"},
                created_at=f"{target_date}T00:00:00+00:00",
            )
            backfill_missing_yesterday = False
            backfilled = True
            backfill_date = target_date
        else:
            backfilled = False
            backfill_date = None

        records = self.store.list_records()
        processed_entry_ids = {record.entry_id for record in records}
        processed_entry_ids.update(pending.entry_id for pending in self.store.list_pending_confirmations())
        entries = [
            entry for entry in self.store.list_entries(limit=limit) if entry.id not in processed_entry_ids
        ]
        logger.info("process_pending start unprocessed=%d limit=%d backfill_missing_yesterday=%s", len(entries), limit, backfill_missing_yesterday)
        auto_processed = 0
        pending_confirmations = 0
        for entry in entries:
            logger.debug("processing entry id=%s created_at=%s content=%r", entry.id, entry.created_at, entry.content[:80])
            
            # Retrieve relevant context from past records (RAG)
            relevant_context = self._retrieve_relevant_context(entry.content)
            full_context = self._profile_context()
            if relevant_context:
                full_context += "\n\n## Relevant Past Records\n" + relevant_context

            result = await self.agent.process_record(entry.content, full_context)
            if result.get("records"):
                for item in result["records"]:
                    if not isinstance(item, dict):
                        continue
                    self.store.add_record(self._record_from_import(entry, item))
                    auto_processed += 1
                continue
            if result.get("needs_clarification"):
                logger.debug("entry id=%s needs clarification reason=%r", entry.id, result.get("clarification_reason"))
                self.store.add_pending_confirmation(self._pending_from_result(entry, result))
                pending_confirmations += 1
                continue
            record = self._record_from_result(entry, result)
            self.store.add_record(record)
            auto_processed += 1
            for update in result.get("suggested_profile_updates") or []:
                if isinstance(update, dict) and str(update.get("confidence") or "").lower() != "low":
                    self.store.add_profile_update(self._profile_update(record.id, update))

        backfill_prompt = None
        if backfill_missing_yesterday or process_date is not None:
            yesterday = _previous_day(process_date)
            if not self.store.has_activity_on(yesterday):
                backfilled = True
                backfill_date = yesterday
                backfill_prompt = f"发现昨天（{yesterday}）没有记录。可以回复 `/self-log backfill {yesterday} 具体内容` 补录。"
                logger.info("missing yesterday activity detected date=%s", yesterday)

        pending_reminder = self._pending_reminder()
        missing_streak, missing_day_reminder = self._missing_day_reminder(process_date)
        daily_question = await self._generate_daily_question(process_date) if not entries else None
        
        logger.info(
            "process_pending done auto_processed=%d pending_confirmations=%d missing_streak=%d",
            auto_processed,
            pending_confirmations,
            missing_streak,
        )
        return ProcessResult(
            auto_processed=auto_processed,
            pending_confirmations=pending_confirmations,
            backfilled=backfilled,
            backfill_date=backfill_date,
            backfill_prompt=backfill_prompt,
            consecutive_missing_days=missing_streak,
            pending_reminder=pending_reminder,
            missing_day_reminder=missing_day_reminder,
            daily_question=daily_question,
        )

    async def _generate_daily_question(self, process_date: str | None = None) -> str | None:
        """Generate a personalized daily question based on the user's profile and context."""
        today = datetime.fromisoformat(process_date).date() if process_date else datetime.now(timezone.utc).date()
        if self.store.has_activity_on(today.isoformat()):
            return None

        logger.info("generating daily question for date=%s", today)
        context = self._profile_context()
        question = await self.agent.generate_daily_question(context)
        return question

    async def generate_report(self, report_type: str) -> SelfLogReport:
        records = [record.to_dict() for record in self.store.list_records()]
        logger.info("generate_report start type=%s records=%d", report_type, len(records))
        content = await self.agent.generate_report(report_type, records, self._profile_context())
        report = SelfLogReport(
            id=uuid4().hex[:12],
            report_type=report_type,
            content=content,
            created_at=_now(),
        )
        self.store.add_report(report)
        logger.info("generate_report done id=%s type=%s", report.id, report.report_type)
        return report

    def empty_result(
        self,
        *,
        backfill_date: str | None = None,
        backfill_prompt: str | None = None,
        pending_reminder: str | None = None,
        missing_day_reminder: str | None = None,
    ) -> ProcessResult:
        return ProcessResult(
            auto_processed=0,
            pending_confirmations=0,
            backfilled=backfill_date is not None,
            backfill_date=backfill_date,
            backfill_prompt=backfill_prompt,
            pending_reminder=pending_reminder,
            missing_day_reminder=missing_day_reminder,
        )

    def _record_from_result(self, entry: SelfLogEntry, result: dict[str, object]) -> SelfLogRecord:
        metadata = entry.metadata or {}
        date = str(metadata.get("record_date") or entry.created_at[:10])
        return SelfLogRecord(
            id=uuid4().hex[:12],
            entry_id=entry.id,
            date=date,
            raw_content=entry.content,
            corrected_content=str(result.get("corrected_content") or entry.content),
            summary=str(result.get("summary") or ""),
            tags=str(result.get("tags") or ""),
            emotion=str(result.get("emotion") or "中性"),
            emotion_reason=str(result.get("emotion_reason") or ""),
            related_people=str(result.get("related_people") or ""),
            related_places=str(result.get("related_places") or ""),
            source=str(metadata.get("source") or "原始"),
            created_at=_now(),
        )

    def _record_from_import(self, entry: SelfLogEntry, item: dict[str, object]) -> SelfLogRecord:
        metadata = entry.metadata or {}
        date = str(item.get("date") or metadata.get("record_date") or entry.created_at[:10])
        raw = str(item.get("content") or item.get("raw_content") or item.get("corrected_content") or "")
        return SelfLogRecord(
            id=uuid4().hex[:12],
            entry_id=entry.id,
            date=date,
            raw_content=raw or entry.content,
            corrected_content=str(item.get("corrected_content") or raw or entry.content),
            summary=str(item.get("summary") or ""),
            tags=str(item.get("tags") or ""),
            emotion=str(item.get("emotion") or "中性"),
            emotion_reason=str(item.get("emotion_reason") or ""),
            related_people=str(item.get("related_people") or ""),
            related_places=str(item.get("related_places") or ""),
            source=str(item.get("source") or metadata.get("source") or "补录"),
            created_at=_now(),
        )

    def _pending_from_result(self, entry: SelfLogEntry, result: dict[str, object]) -> PendingConfirmation:
        questions = result.get("clarification_questions")
        if not isinstance(questions, list):
            questions = []
        return PendingConfirmation(
            id=uuid4().hex[:12],
            entry_id=entry.id,
            raw_content=entry.content,
            clarification_reason=str(result.get("clarification_reason") or "信息不完整"),
            questions=[str(question) for question in questions],
            created_at=_now(),
        )

    def _profile_update(self, record_id: str, update: dict[str, object]) -> ProfileUpdate:
        return ProfileUpdate(
            id=uuid4().hex[:12],
            record_id=record_id,
            category=str(update.get("category") or ""),
            entity_type=str(update.get("entity_type") or ""),
            entity_name=str(update.get("entity_name") or ""),
            suggested_value=str(update.get("suggested_value") or ""),
            confidence=str(update.get("confidence") or "low"),
        )

    def _profile_context(self) -> str:
        from self_log.memory import load_memory_prompt
        from self_log.workspace import get_soul_path, get_user_path

        sections: list[str] = []

        # 0. Temporal awareness (Crucial for grounding)
        now = datetime.now()
        local_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        weekday_str = now.strftime("%A")
        sections.append(f"## Current Time\n- Local Time: {local_time_str}\n- Day of Week: {weekday_str}")

        # 1. Soul & User profile (The "Static" core)
        soul_path = get_soul_path(self.store.workspace)
        if soul_path.exists():
            sections.append(f"## Soul\n{soul_path.read_text(encoding='utf-8')}")

        user_path = get_user_path(self.store.workspace)
        if user_path.exists():
            sections.append(f"## User Profile\n{user_path.read_text(encoding='utf-8')}")

        # 2. Long-term memory (The "Structured" facts)
        memory_prompt = load_memory_prompt(self.store.workspace)
        if memory_prompt:
            sections.append(memory_prompt)

        # 3. Dynamic profile updates (The "Recent" learnings)
        updates = [
            update
            for update in self.store.list_profile_updates()
            if update.status in {"accepted", "pending"}
        ]
        if updates:
            lines = [
                f"- [{update.status}] {update.category}/{update.entity_type}/{update.entity_name}: "
                f"{update.suggested_value}（confidence={update.confidence}）"
                for update in updates[-30:]
            ]
            sections.append("## Recent Profile Observations\n" + "\n".join(lines))

        if not sections:
            return "## Known Context\nNo prior context available."

        return "\n\n".join(sections)

    def _retrieve_relevant_context(self, content: str, limit: int = 5) -> str | None:
        """Find past records related to the current input to provide context."""
        records = self.store.search_records(query=content, limit=limit)
        if not records:
            return None
        
        lines = [
            f"- [{record.date}] {record.summary} ({record.tags})"
            for record in records
        ]
        return "\n".join(lines)

    def _pending_reminder(self) -> str | None:
        pending_count = len(self.store.list_pending_confirmations())
        state = self.store.reminder_state()
        if pending_count >= 5 and pending_count > state["last_pending_count"]:
            self.store.update_reminder_state(pending_count=pending_count)
            return f"还有 {pending_count} 条待确认 self-log 需要你确认。"
        self.store.update_reminder_state(pending_count=pending_count)
        return None

    def _missing_day_reminder(self, process_date: str | None = None) -> tuple[int, str | None]:
        today = datetime.fromisoformat(process_date).date() if process_date else datetime.now(timezone.utc).date()
        dates = self.store.dates_with_activity()
        streak = 0
        for offset in range(1, 31):
            day = (today - timedelta(days=offset)).isoformat()
            if day in dates:
                break
            streak += 1
        state = self.store.reminder_state()
        reminder = None
        if streak >= 3 and streak > state["last_missing_streak"]:
            reminder = f"你已经连续 {streak} 天没有 self-log 记录，要不要补一下？"
        self.store.update_reminder_state(missing_streak=streak)
        return streak, reminder


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _previous_day(process_date: str | None) -> str:
    if process_date:
        return (datetime.fromisoformat(process_date).date() - timedelta(days=1)).isoformat()
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
