"""Self-log processing workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from openharness.utils.log import get_logger

from common.constants import (
    DEFAULT_EMOTION,
    DEFAULT_SAMPLE_TYPE,
    DEFAULT_SOURCE_BACKFILL,
    DEFAULT_SOURCE_ORIGINAL,
    PROFILE_UPDATE_ACCEPTED_STATUSES,
    REPORT_WINDOW_DAYS,
    WEEKDAYS_ZH,
)
from solo.agent import OpenHarnessSoloAgent
from solo.strings import MISSING_DAY_REMINDER_TMPL, PENDING_REMINDER_TMPL
from solo.core.artifacts import persist_personal_artifacts
from solo.core.models import (
    PendingConfirmation,
    ProcessResult,
    ProfileUpdate,
    ProjectLink,
    ProjectSuggestion,
    SoloEntry,
    SoloRecord,
    SoloReport,
)
from solo.core.store import SoloStore
from solo.core.utils import (
    _get_holiday,
    _get_period,
    _get_season,
    _get_weekday,
    _is_weekend,
    _now,
    _previous_day,
)

logger = get_logger(__name__)


def _build_report_stats(records: list, start_date: str, end_date: str) -> str:
    """Build a statistical summary of records for report context."""
    from collections import Counter

    if not records:
        return f"- 周期: {start_date} ~ {end_date}\n- 记录条数: 0\n- 数据不足，无法生成统计"

    # Active days
    dates = [r.date for r in records]
    unique_dates = set(dates)
    total_days = max(1, (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days)

    # Emotion distribution
    emotions = Counter(r.emotion for r in records if r.emotion)
    emotion_lines = ", ".join(f"{emo}({cnt})" for emo, cnt in emotions.most_common(8))

    # Tag distribution
    all_tags: list[str] = []
    for r in records:
        all_tags.extend(t.strip() for t in r.tags.split(",") if t.strip())
    tag_counts = Counter(all_tags).most_common(10)
    tag_lines = ", ".join(f"{tag}({cnt})" for tag, cnt in tag_counts)

    # Sample type distribution
    sample_types = Counter(r.sample_type for r in records if r.sample_type)
    sample_lines = ", ".join(f"{st}({cnt})" for st, cnt in sample_types.most_common(5))

    # Day-of-week activity
    day_activity = Counter(dates)
    busiest = max(day_activity.items(), key=lambda x: x[1]) if day_activity else ("N/A", 0)

    lines = [
        f"- **周期**: {start_date} ~ {end_date}",
        f"- **记录总条数**: {len(records)}",
        f"- **活跃天数**: {len(unique_dates)}/{total_days} 天",
        f"- **情绪分布**: {emotion_lines or '无数据'}",
        f"- **高频标签 Top 10**: {tag_lines or '无数据'}",
        f"- **样本类型分布**: {sample_lines or '无数据'}",
        f"- **最高产日**: {busiest[0]} ({busiest[1]} 条)",
    ]
    return "\n".join(lines)


def _build_report_visual_appendix(records: list, start_date: str, end_date: str) -> str:
    """Build a rich visual data appendix appended to the final report output."""
    from collections import Counter

    if not records:
        return ""

    sections: list[str] = []
    sections.append("\n\n---\n\n## 📊 数据可视化附录\n")

    dates = [r.date for r in records]
    unique_dates = set(dates)
    total_days = max(1, (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days)

    # Overview banner
    sections.append(
        f"> 📅 **{start_date} ~ {end_date}** | "
        f"活跃 **{len(unique_dates)}/{total_days}** 天 | "
        f"共 **{len(records)}** 条记录 | "
        f"日均 **{len(records) / max(1, len(unique_dates)):.1f}** 条\n"
    )

    # Emotion distribution table
    emotions = Counter(r.emotion for r in records if r.emotion)
    if emotions:
        total_emo = sum(emotions.values())
        sections.append("### 情绪分布\n")
        sections.append("| 情绪 | 次数 | 占比 | 分布 |")
        sections.append("|------|------|------|------|")
        for emo, count in emotions.most_common():
            pct = count * 100 // total_emo
            bar_len = max(1, count * 15 // max(emotions.values()))
            bar = "▓" * bar_len + "░" * (15 - bar_len)
            sections.append(f"| {emo} | {count} | {pct}% | `{bar}` |")
        sections.append("")

    # Tag Top 10 table
    all_tags: list[str] = []
    for r in records:
        all_tags.extend(t.strip() for t in r.tags.split(",") if t.strip())
    tag_counts = Counter(all_tags).most_common(10)
    if tag_counts:
        sections.append("### 高频标签 Top 10\n")
        sections.append("| # | 标签 | 次数 | 热度 |")
        sections.append("|---|------|------|------|")
        for i, (tag, count) in enumerate(tag_counts, 1):
            heat = "🔥" * min(count, 5)
            sections.append(f"| {i} | `{tag}` | {count} | {heat} |")
        sections.append("")

    # Activity heatmap (week grid)
    day_counts = Counter(dates)
    sections.append("### 活动热力图\n")
    sections.append("| 周 | 一 | 二 | 三 | 四 | 五 | 六 | 日 |")
    sections.append("|---|---|---|---|---|---|---|---|")
    current = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    start_dt = current
    # Align to Monday
    while current.weekday() != 0:
        current = current - timedelta(days=1)
    week_num = 1
    while current <= end_dt:
        row = [f"W{week_num}"]
        for _ in range(7):
            d = current.isoformat()
            count = day_counts.get(d, 0)
            if current > end_dt or current < start_dt:
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
        sections.append("| " + " | ".join(row) + " |")
        week_num += 1
    sections.append("\n> 图例: · 非周期 | ░ 无记录 | ▒ 1条 | ▓ 2条 | █ 3+条")
    sections.append("")

    # Sample type distribution
    sample_types = Counter(r.sample_type for r in records if r.sample_type)
    if sample_types:
        total_st = sum(sample_types.values())
        sections.append("### 样本类型分布\n")
        sections.append("| 类型 | 次数 | 占比 |")
        sections.append("|------|------|------|")
        for kind, count in sample_types.most_common():
            pct = count * 100 // total_st
            sections.append(f"| {kind} | {count} | {pct}% |")
        sections.append("")

    return "\n".join(sections)


class SoloProcessor:
    """Process raw entries into structured records."""

    def __init__(
        self,
        store: SoloStore,
        agent: OpenHarnessSoloAgent | None = None,
    ) -> None:
        self.store = store
        self.agent = agent or OpenHarnessSoloAgent(record_model_call=store.record_llm_call)

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
            
            # Determine target date for context grounding
            metadata = entry.metadata or {}
            target_date = str(metadata.get("record_date") or entry.created_at[:10])
            
            full_context = self._profile_context(target_date=target_date)
            if relevant_context:
                full_context += "\n\n## Relevant Past Records\n" + relevant_context

            result = await self.agent.process_record(entry.content, full_context)
            if result.get("records"):
                for item in result["records"]:
                    if not isinstance(item, dict):
                        continue
                    record = self._record_from_import(entry, item)
                    self.store.add_record(record)
                    await self._persist_artifacts_for_record(record, entry.content, full_context)
                    auto_processed += 1
                continue
            if result.get("needs_clarification"):
                logger.debug("entry id=%s needs clarification reason=%r", entry.id, result.get("clarification_reason"))
                self.store.add_pending_confirmation(self._pending_from_result(entry, result))
                pending_confirmations += 1
                continue
            record = self._record_from_result(entry, result)
            self.store.add_record(record)
            await self._persist_artifacts_for_record(record, entry.content, full_context)
            auto_processed += 1

        backfill_prompt = None
        if backfill_missing_yesterday or process_date is not None:
            yesterday = _previous_day(process_date)
            if not self.store.has_activity_on(yesterday):
                backfilled = True
                backfill_date = yesterday
                backfill_prompt = f"发现昨天（{yesterday}）没有记录。可以回复 `/solo backfill {yesterday} 具体内容` 补录。"
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
        context = self._profile_context(target_date=today.isoformat())
        question = await self.agent.generate_daily_question(context)
        return question

    async def _persist_artifacts_for_record(
        self,
        record: SoloRecord,
        raw_content: str,
        profile_context: str,
    ) -> None:
        try:
            artifacts = await self.agent.extract_artifacts(
                record.to_dict(),
                raw_content,
                profile_context,
            )
        except Exception as exc:
            logger.warning("artifact extraction failed record_id=%s: %s", record.id, exc)
            return

        persist_personal_artifacts(self.store, record, artifacts)
        for update in artifacts.get("suggested_profile_updates") or []:
            if isinstance(update, dict) and str(update.get("confidence") or "").lower() != "low":
                self.store.add_profile_update(self._profile_update(record.id, update))

        # Phase 1: auto-link record to projects
        await self._link_record_to_projects(record, artifacts)

    async def _link_record_to_projects(
        self,
        record: SoloRecord,
        artifacts: dict[str, object],
    ) -> None:
        """Deterministic auto-linking: match record to existing projects.

        Called after artifacts are persisted.  Uses todo categories as
        project hints (solo has no ``project`` field on artifacts).
        """
        from common.project_ai.matcher import match_record

        # Gather active projects
        projects = self.store.list_projects(status="active")
        if not projects:
            return

        project_dicts = [p.to_dict() for p in projects]
        aliases_by_project: dict[str, list[str]] = {}
        for p in projects:
            aliases = self.store.list_project_aliases(p.id)
            aliases_by_project[p.id] = [a.alias for a in aliases]

        # Solo: use todo categories as project hints
        artifact_projects: list[str] = []
        for item in (artifacts.get("todos") or []):
            if isinstance(item, dict):
                cat = str(item.get("category") or "")
                if cat:
                    artifact_projects.append(cat)

        try:
            result = await match_record(
                record_id=record.id,
                record_content=record.raw_content or "",
                record_summary=record.summary or "",
                artifact_projects=artifact_projects,
                projects=project_dicts,
                aliases_by_project=aliases_by_project,
                agent=None,  # deterministic only for now
            )
        except Exception:
            logger.warning("project linking failed record_id=%s", record.id, exc_info=True)
            return

        now = _now()

        # Auto-links: high confidence → create active ProjectLink
        for candidate in result.auto_links:
            try:
                link = ProjectLink(
                    id=str(uuid4()),
                    project_id=candidate.project_id,
                    entity_type="record",
                    entity_id=record.id,
                    source="ai_high_confidence",
                    confidence="high",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                self.store.create_project_link(link)
            except Exception:
                logger.debug("duplicate auto-link skipped project=%s record=%s", candidate.project_id, record.id)

        # Suggestions: medium confidence → create pending suggestion
        for candidate in result.suggestions:
            suggestion = ProjectSuggestion(
                id=str(uuid4()),
                suggestion_type="link_entity",
                project_id=candidate.project_id,
                title=f"关联记录到「{candidate.project_title}」",
                rationale=candidate.rationale,
                proposed_payload_json=json.dumps({
                    "entity_type": "record",
                    "entity_id": record.id,
                }),
                evidence_json=json.dumps(candidate.evidence),
                confidence=candidate.confidence,
                status="pending",
                source="ai",
                created_at=now,
                updated_at=now,
            )
            self.store.create_project_suggestion(suggestion)

    async def generate_report(
        self,
        report_type: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> SoloReport:
        # Time window: use explicit dates if provided, otherwise infer from report_type
        now = datetime.now(timezone.utc)
        if start_date and end_date:
            start, end = start_date, end_date
        elif start_date:
            start, end = start_date, now.strftime("%Y-%m-%d")
        else:
            _window = REPORT_WINDOW_DAYS
            start = (now - timedelta(days=_window.get(report_type, 30))).strftime("%Y-%m-%d")
            end = now.strftime("%Y-%m-%d")

        all_records = self.store.list_records()
        filtered = [r for r in all_records if start <= r.date <= end]

        records_dicts = [record.to_dict() for record in filtered]
        logger.info("generate_report start type=%s records=%d (filtered from %d) range=%s~%s", report_type, len(filtered), len(all_records), start, end)

        if not filtered:
            content = (
                f"# {report_type.capitalize()} Report\n\n"
                f"> 📅 {start} ~ {end}\n\n"
                f"该时间段内没有记录，无法生成报告。"
            )
        else:
            # Build statistics summary
            stats_summary = _build_report_stats(filtered, start, end)

            context = self._profile_context()
            artifacts_context = self._iteration_artifacts_context()
            if artifacts_context:
                context = f"{context}\n\n{artifacts_context}"
            project_ctx = self._project_context()
            if project_ctx:
                context = f"{context}\n\n{project_ctx}"
            content = await self.agent.generate_report(
                report_type, records_dicts, context, stats_summary=stats_summary,
            )
            content = content.strip()
            if not content:
                raise RuntimeError("report generation returned empty response")

            # Append precise visual data appendix (code-generated, not LLM)
            visual_appendix = _build_report_visual_appendix(filtered, start, end)
            if visual_appendix:
                content = content + visual_appendix

        report = SoloReport(
            id=uuid4().hex[:12],
            report_type=report_type,
            content=content,
            created_at=_now(),
            period_start=start,
            period_end=end,
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

    def _build_record(
        self,
        entry: SoloEntry,
        data: dict[str, object],
        *,
        raw_content: str | None = None,
        default_source: str = DEFAULT_SOURCE_ORIGINAL,
    ) -> SoloRecord:
        """Build a SoloRecord from entry + extracted/imported data dict.

        Args:
            entry: The source entry.
            data: LLM result or imported item dict.
            raw_content: Pre-resolved raw content; defaults to entry.content.
            default_source: Fallback source label when not in data or metadata.
        """
        metadata = entry.metadata or {}
        date = str(data.get("date") or metadata.get("record_date") or entry.created_at[:10])
        raw = raw_content if raw_content is not None else entry.content
        events = str(data.get("events") or "")
        holiday = _get_holiday(date)
        if holiday and holiday not in events:
            events = f"{holiday}, {events}" if events else holiday

        return SoloRecord(
            id=uuid4().hex[:12],
            entry_id=entry.id,
            date=date,
            raw_content=raw,
            corrected_content=str(data.get("corrected_content") or raw),
            summary=str(data.get("summary") or ""),
            tags=str(data.get("tags") or ""),
            emotion=str(data.get("emotion") or DEFAULT_EMOTION),
            weekday=_get_weekday(date),
            events=events,
            period=str(data.get("period") or _get_period(entry.created_at)),
            season=_get_season(date),
            is_weekend=_is_weekend(date),
            content_length=len(raw),
            emotion_reason=str(data.get("emotion_reason") or ""),
            related_people=str(data.get("related_people") or ""),
            related_places=str(data.get("related_places") or ""),
            source=str(data.get("source") or metadata.get("source") or default_source),
            created_at=_now(),
            attachments=list(entry.attachments),
            sample_type=str(data.get("sample_type") or DEFAULT_SAMPLE_TYPE),
            trigger_scene=str(data.get("trigger_scene") or ""),
            friction_signal=str(data.get("friction_signal") or ""),
            awareness_timing=str(data.get("awareness_timing") or ""),
            break_point=str(data.get("break_point") or ""),
            bridge_action=str(data.get("bridge_action") or ""),
            environment_design=str(data.get("environment_design") or ""),
            next_experiment=str(data.get("next_experiment") or ""),
        )

    def _record_from_result(self, entry: SoloEntry, result: dict[str, object]) -> SoloRecord:
        return self._build_record(entry, result, default_source=DEFAULT_SOURCE_ORIGINAL)

    def _record_from_import(self, entry: SoloEntry, item: dict[str, object]) -> SoloRecord:
        raw = str(item.get("content") or item.get("raw_content") or item.get("corrected_content") or "")
        return self._build_record(
            entry, item, raw_content=raw or entry.content, default_source=DEFAULT_SOURCE_BACKFILL,
        )

    def _pending_from_result(self, entry: SoloEntry, result: dict[str, object]) -> PendingConfirmation:
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

    def _iteration_artifacts_context(self) -> str:
        sections: list[str] = []
        todos = self.store.list_todos(status="pending", limit=30)
        if todos:
            sections.append(
                "## Open Personal Todos\n"
                + "\n".join(
                    f"- [{todo.priority}] {todo.title} category={todo.category} due={todo.due_date}"
                    for todo in todos
                )
            )
        experiments = self.store.list_experiments(status="active", limit=30)
        if experiments:
            sections.append(
                "## Active Behavior Experiments\n"
                + "\n".join(
                    f"- {item.title}; hypothesis={item.hypothesis}; trigger={item.trigger}; "
                    f"desired_action={item.desired_action}; design={item.environment_design}; "
                    f"success={item.success_criteria}; window={item.observation_window}"
                    for item in experiments
                )
            )
        return "## Iteration Artifacts\n\n" + "\n\n".join(sections) if sections else ""

    def _project_context(self) -> str:
        """Build a project summary section for report context."""
        projects = self.store.list_projects(status="active")
        if not projects:
            return ""
        lines: list[str] = ["## Active Projects\n"]
        for p in projects:
            detail = self.store.get_project_detail(p.id)
            if detail is None:
                continue
            pct = detail.get("completion_pct", "N/A")
            risk = detail.get("risk_status", "normal")
            a7 = detail.get("activity_7d", 0)
            ms = f"{detail.get('completed_milestone_count', 0)}/{detail.get('milestone_count', 0)}"
            lines.append(
                f"- **{p.title}**: {pct}% done, {ms} milestones, "
                f"risk={risk}, activity_7d={a7}"
            )
        return "\n".join(lines) if len(lines) > 1 else ""

    def _profile_context(self, target_date: str | None = None) -> str:
        from solo.core.memory import load_memory_prompt
        from solo.core.workspace import get_soul_path, get_user_path

        sections: list[str] = []

        # 0. Temporal awareness (Crucial for grounding)
        now = datetime.now()
        local_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        if target_date:
            try:
                # Handle YYYY-MM-DD
                if len(target_date) == 10:
                    dt = datetime.strptime(target_date, "%Y-%m-%d")
                else:
                    dt = datetime.fromisoformat(target_date)
                
                # Use Chinese weekday names for better LLM understanding in Chinese context
                weekdays = WEEKDAYS_ZH
                weekday_str = weekdays[dt.weekday()]
                date_str = dt.strftime("%Y-%m-%d")
                
                sections.append(
                    f"## Temporal Context\n"
                    f"- Current System Time: {local_time_str}\n"
                    f"- Record Target Date: {date_str}\n"
                    f"- Record Day of Week: {weekday_str}"
                )
            except Exception as e:
                logger.warning("failed to parse target_date=%r: %s", target_date, e)
                sections.append(f"## Current Time\n- Local Time: {local_time_str}\n- Day of Week: {now.strftime('%A')}")
        else:
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
            if update.status in PROFILE_UPDATE_ACCEPTED_STATUSES
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
            f"- [{record.date}] {record.summary} ({record.tags}) sample={record.sample_type} "
            f"trigger={record.trigger_scene} design={record.environment_design}"
            for record in records
        ]
        return "\n".join(lines)

    def _pending_reminder(self) -> str | None:
        pending_count = len(self.store.list_pending_confirmations())
        state = self.store.reminder_state()
        if pending_count >= 1 and pending_count > state["last_pending_count"]:
            self.store.update_reminder_state(pending_count=pending_count)
            return PENDING_REMINDER_TMPL.format(count=pending_count)
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
        if streak >= 1 and streak > state["last_missing_streak"]:
            reminder = MISSING_DAY_REMINDER_TMPL.format(streak=streak)
        self.store.update_reminder_state(missing_streak=streak)
        return streak, reminder
