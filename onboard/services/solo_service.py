"""Read/write facade for solo data used by onboard."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from solo.agent import OpenHarnessSoloAgent
from solo.config import load_config, save_config as _save_config
from solo.core.models import ProjectSignal, ProjectSnapshot, ProjectCheckin
from solo.core.store import SoloStore
from solo.gateway.service import gateway_status, start_gateway_process, stop_gateway_process
from solo.processor import SoloProcessor

from common.project_ai.signals import analyze_project_state, generate_daily_snapshot, generate_checkin_questions

from onboard.services.common import (
    count_this_week,
    current_month_range,
    daily_counts,
    emotion_distribution,
    latest_llm_usage_date,
    filter_entries,
    filter_records,
    find_by_id,
    newest_first,
    paginate,
    resolve_current_model,
    resolve_vision_model,
    split_csv,
    stream_feed_digest_run,
    to_jsonable,
    top_tags,
)


class SoloService:
    """Small service wrapper around SoloStore for WebUI routes."""

    _app_type: str = "solo"

    def __init__(self, workspace: str | Path | None = None) -> None:
        self.workspace = workspace
        self.store = SoloStore(workspace)

    def stats(self) -> dict[str, Any]:
        status = self.store.status()
        records = self.store.list_records()
        todos = self.store.list_todos()
        pending_todos = [todo for todo in todos if todo.status != "done"]
        llm_usage = self.store.llm_usage_summary()
        vision_usage = self.store.vision_usage_summary()
        month_start, month_end = current_month_range()
        target_tz = datetime.now().astimezone().tzinfo or timezone.utc
        monthly_tokens = self.store.llm_token_daily_summary(
            start_date=month_start.isoformat(),
            end_date=month_end.isoformat(),
            target_tz=target_tz,
        )
        monthly_model_calls = self.store.llm_call_daily_summary(
            start_date=month_start.isoformat(),
            end_date=month_end.isoformat(),
            target_tz=target_tz,
        )
        llm_daily_focus_date = latest_llm_usage_date(
            monthly_tokens,
            fallback=datetime.now(tz=target_tz).date().isoformat(),
        )
        daily_llm_usage = self.store.llm_usage_summary(
            start_date=llm_daily_focus_date,
            end_date=llm_daily_focus_date,
            target_tz=target_tz,
        )
        config = load_config(self.workspace)
        return {
            "total_entries": int(status["entries"]),
            "total_records": int(status["records"]),
            "pending_entries": int(status["pending_confirmations"]),
            "total_todos": int(status["todos"]),
            "pending_todos": len(pending_todos),
            "this_week_records": count_this_week(records),
            "llm_total_calls": int(llm_usage["total_calls"]),
            "llm_total_input_tokens": int(llm_usage["total_input_tokens"]),
            "llm_total_output_tokens": int(llm_usage["total_output_tokens"]),
            "llm_usage_models": llm_usage["models"],
            "llm_monthly_start_date": month_start.isoformat(),
            "llm_monthly_end_date": month_end.isoformat(),
            "llm_monthly_tokens": monthly_tokens,
            "llm_monthly_model_calls": monthly_model_calls,
            "llm_daily_focus_date": llm_daily_focus_date,
            "llm_daily_total_calls": int(daily_llm_usage["total_calls"]),
            "llm_daily_input_tokens": int(daily_llm_usage["total_input_tokens"]),
            "llm_daily_output_tokens": int(daily_llm_usage["total_output_tokens"]),
            "llm_daily_usage_models": daily_llm_usage["models"],
            "vision_total_calls": int(vision_usage["total_calls"]),
            "current_model": resolve_current_model(config.provider_profile),
            "vision_model": resolve_vision_model(),
            "emotion_distribution": emotion_distribution(records),
            "daily_counts": daily_counts(records),
            "top_tags": top_tags(records),
        }

    def list_entries(
        self,
        *,
        limit: int,
        offset: int,
        channel: str | None = None,
    ) -> dict[str, Any]:
        entries = newest_first(filter_entries(self.store.list_entries(), channel=channel))
        return paginate([to_jsonable(entry) for entry in entries], limit=limit, offset=offset)

    def get_entry(self, entry_id: str) -> dict[str, Any] | None:
        entry = self.store.get_entry(entry_id)
        return to_jsonable(entry) if entry else None

    def list_records(
        self,
        *,
        limit: int,
        offset: int,
        tag: str | None = None,
        emotion: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        records = newest_first(
            filter_records(
                self.store.list_records(),
                tag=tag,
                emotion=emotion,
                date_from=date_from,
                date_to=date_to,
            )
        )
        return paginate([to_jsonable(record) for record in records], limit=limit, offset=offset)

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        record = self.store.get_record(record_id)
        return to_jsonable(record) if record else None

    def search(
        self,
        *,
        query: str | None,
        tags: str | None,
        emotions: str | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
    ) -> dict[str, Any]:
        records = self.store.search_records(
            query=query,
            tags=split_csv(tags),
            emotions=split_csv(emotions),
            start_date=date_from,
            end_date=date_to,
            limit=limit,
        )
        return {
            "records": [to_jsonable(record) for record in records],
            "total": len(records),
            "query": query or "",
        }

    def list_todos(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            to_jsonable(todo)
            for todo in self.store.list_todos(status=status, category=category)
        ]

    def start_todo(self, todo_id: str) -> bool:
        return self.store.start_todo(todo_id)

    def revert_todo(self, todo_id: str) -> bool:
        return self.store.revert_todo(todo_id)

    def complete_todo(self, todo_id: str) -> bool:
        return self.store.complete_todo(todo_id)

    def reopen_todo(self, todo_id: str) -> bool:
        return self.store.reopen_todo(todo_id)

    def cancel_todo(self, todo_id: str) -> bool:
        return self.store.cancel_todo(todo_id)

    def delete_todo(self, todo_id: str) -> bool:
        return self.store.delete_todo(todo_id)

    def list_reports(self, report_type: str | None = None) -> list[dict[str, Any]]:
        reports = self.store.list_reports()
        if report_type:
            reports = [report for report in reports if report.report_type == report_type]
        # Sort by content period (period_start desc), fallback to created_at desc
        reports.sort(key=lambda r: r.period_start or r.created_at, reverse=True)
        return [to_jsonable(report) for report in reports]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        report = find_by_id(self.store.list_reports(), report_id)
        return to_jsonable(report) if report else None

    def delete_report(self, report_id: str) -> bool:
        return self.store.delete_report(report_id)

    def list_feed_digests(self, preset: str | None = None) -> list[dict[str, Any]]:
        reports = self.store.list_reports()
        digests = [report for report in reports if report.report_type == "feed_digest"]
        if preset:
            digests = [report for report in digests if (report.metadata or {}).get("preset") == preset]
        digests.sort(key=lambda report: report.period_start or report.created_at, reverse=True)
        return [to_jsonable(report) for report in digests]

    def get_feed_digest(self, digest_id: str) -> dict[str, Any] | None:
        report = find_by_id(self.store.list_reports(), digest_id)
        if report is None or report.report_type != "feed_digest":
            return None
        return to_jsonable(report)

    def delete_feed_digest(self, digest_id: str) -> bool:
        report = find_by_id(self.store.list_reports(), digest_id)
        if report is None or report.report_type != "feed_digest":
            return False
        return self.store.delete_report(digest_id)

    async def run_feed_digest(self, preset: str | None = None) -> dict[str, Any]:
        from solo.feed_digest import run_feed_digest
        report = await run_feed_digest(workspace=self.workspace, preset_name=preset)
        return to_jsonable(report)

    def run_feed_digest_stream(self, preset: str | None = None):
        from solo.feed_digest import run_feed_digest
        return stream_feed_digest_run(run_feed_digest, workspace=self.workspace, preset=preset)

    async def generate_report(
        self, report_type: str, profile: str | None = None, start_date: str | None = None, end_date: str | None = None,
    ) -> dict[str, Any]:
        config = load_config(self.workspace)
        agent = OpenHarnessSoloAgent(
            profile=profile or config.provider_profile,
            record_model_call=self.store.record_llm_call,
        )
        report = await SoloProcessor(self.store, agent).generate_report(
            report_type, start_date=start_date, end_date=end_date,
        )
        return to_jsonable(report)

    async def process_pending(self, limit: int = 20) -> dict[str, Any]:
        config = load_config(self.workspace)
        agent = OpenHarnessSoloAgent(
            profile=config.provider_profile,
            record_model_call=self.store.record_llm_call,
        )
        result = await SoloProcessor(self.store, agent).process_pending(limit=limit)
        return to_jsonable(result)

    def config(self) -> dict[str, Any]:
        config = load_config(self.workspace)
        return {"workspace": str(self.store.workspace), **config.model_dump()}

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        from solo.core.models import SoloConfig
        current = load_config(self.workspace)
        merged = {**current.model_dump(), **updates}
        merged.pop("workspace", None)
        new_config = SoloConfig.model_validate(merged)
        _save_config(new_config, self.workspace)
        return self.config()

    def gateway_status(self) -> dict[str, Any]:
        state = gateway_status(workspace=self.workspace)
        return {
            "status": "running" if state.running else "stopped",
            "pid": state.pid,
            "uptime_seconds": None,
            "port": None,
            "provider_profile": state.provider_profile,
            "enabled_channels": state.enabled_channels,
            "last_error": state.last_error,
        }

    def start_gateway(self, cwd: str | Path | None = None) -> dict[str, Any]:
        pid = start_gateway_process(cwd, self.workspace)
        return {"status": "running", "pid": pid}

    def stop_gateway(self, cwd: str | Path | None = None) -> dict[str, Any]:
        stopped = stop_gateway_process(cwd, self.workspace)
        return {"status": "stopped" if stopped else "unknown", "stopped": stopped}

    # ── Project management ──────────────────────────────────────────

    def list_project_templates(self):
        from common.project_ai.templates import list_templates
        return [t.to_dict() for t in list_templates(self._app_type)]

    def list_projects(self, status=None, limit=None, offset=0):
        return self.store.list_projects_with_detail(status=status, limit=limit, offset=offset)

    def get_project(self, project_id):
        return self.store.get_project_detail(project_id)

    def create_project(self, data):
        from uuid import uuid4
        from solo.core.utils import _now
        from solo.core.models import Project, Milestone
        from common.project_ai.templates import get_template

        tpl_id = data.get("template", "")
        tpl = get_template(tpl_id) if tpl_id else None

        now = _now()
        project = Project(
            id=str(uuid4()),
            title=data["title"],
            description=data.get("description") or (tpl.description if tpl else ""),
            priority=data.get("priority") or (tpl.priority if tpl else "medium"),
            start_date=data.get("start_date", ""),
            target_date=data.get("target_date", ""),
            tags=data.get("tags") or (tpl.tags if tpl else ""),
            created_at=now,
            updated_at=now,
        )
        self.store.create_project(project)

        if tpl and tpl.milestones:
            for i, title in enumerate(tpl.milestones):
                ms = Milestone(
                    id=str(uuid4()),
                    project_id=project.id,
                    title=title,
                    created_at=now,
                    updated_at=now,
                )
                self.store.create_milestone(ms)

        return self.store.get_project_detail(project.id)

    def update_project(self, project_id, data):
        self.store.update_project(project_id, **data)
        return self.store.get_project_detail(project_id)

    def complete_project(self, project_id):
        self.store.complete_project(project_id)
        return self.store.get_project_detail(project_id)

    def archive_project(self, project_id, reason=""):
        self.store.archive_project(project_id, reason)
        return self.store.get_project_detail(project_id)

    def reactivate_project(self, project_id):
        self.store.reactivate_project(project_id)
        return self.store.get_project_detail(project_id)

    def delete_project(self, project_id):
        return self.store.delete_project(project_id)

    def get_project_timeline(self, project_id, limit=50):
        """Aggregate milestones, signals, snapshots into a unified timeline."""
        events = []

        # Milestones
        for m in self.store.list_milestones(project_id):
            events.append({
                "date": m.completed_at or m.target_date or m.created_at,
                "type": "milestone_completed" if m.status == "completed" else "milestone",
                "title": m.title,
                "detail": "",
            })
            if m.target_date and m.status != "completed":
                events.append({
                    "date": m.target_date,
                    "type": "milestone_target",
                    "title": f"Target: {m.title}",
                    "detail": "",
                })

        # Linked records (work entries tied to this project)
        for lk in self.store.list_project_links(project_id=project_id, entity_type="record"):
            if lk.status != "active":
                continue
            record = self.store.get_record(lk.entity_id)
            if record:
                events.append({
                    "date": record.date or record.created_at,
                    "type": "record",
                    "title": record.summary or record.raw_content[:80],
                    "detail": record.tags,
                })

        # Signals (recent, high-severity)
        for s in self.store.list_project_signals(project_id, limit=20):
            events.append({
                "date": s.created_at,
                "type": f"signal_{s.signal_type}",
                "title": s.summary,
                "detail": s.severity,
            })

        # Snapshots
        for snap in self.store.list_project_snapshots(project_id, limit=10):
            events.append({
                "date": snap.snapshot_date,
                "type": "snapshot",
                "title": snap.summary or f"Health: {snap.health}",
                "detail": f"{snap.completion_pct or 0}% done, activity_7d={snap.activity_7d}",
            })

        # Project itself
        project = self.store.get_project(project_id)
        if project:
            events.append({
                "date": project.created_at,
                "type": "project_created",
                "title": f"Project created: {project.title}",
                "detail": "",
            })
            if project.completed_at:
                events.append({
                    "date": project.completed_at,
                    "type": "project_completed",
                    "title": "Project completed",
                    "detail": "",
                })

        # Sort descending by date, cap
        events.sort(key=lambda e: e.get("date", ""), reverse=True)
        return events[:limit]

    def list_milestones(self, project_id):
        milestones = self.store.list_milestones(project_id)
        return [m.to_dict() for m in milestones]

    def create_milestone(self, project_id, data):
        from uuid import uuid4
        from solo.core.utils import _now
        from solo.core.models import Milestone

        now = _now()
        milestone = Milestone(
            id=str(uuid4()),
            project_id=project_id,
            title=data["title"],
            description=data.get("description", ""),
            target_date=data.get("target_date", ""),
            created_at=now,
            updated_at=now,
        )
        self.store.create_milestone(milestone)
        return milestone.to_dict()

    def update_milestone(self, milestone_id, data):
        self.store.update_milestone(milestone_id, **data)
        return True

    def complete_milestone(self, milestone_id):
        self.store.complete_milestone(milestone_id)
        return True

    def delete_milestone(self, milestone_id):
        return self.store.delete_milestone(milestone_id)

    def reorder_milestones(self, project_id, milestone_ids):
        self.store.reorder_milestones(project_id, milestone_ids)
        return {"ok": True}

    def list_project_links(self, project_id):
        links = self.store.list_project_links(project_id=project_id)
        result = []
        for lnk in links:
            if not self.store.entity_exists(lnk.entity_type, lnk.entity_id):
                self.store.delete_project_link(lnk.id)
                continue
            d = lnk.to_dict()
            d["entity_title"] = self.store.resolve_entity_summary(lnk.entity_type, lnk.entity_id)
            result.append(d)
        return result

    def create_project_link(self, project_id, data):
        from uuid import uuid4
        from solo.core.utils import _now
        from solo.core.models import ProjectLink

        now = _now()
        link = ProjectLink(
            id=str(uuid4()),
            project_id=project_id,
            entity_type=data["entity_type"],
            entity_id=data["entity_id"],
            source=data.get("source", "user"),
            created_at=now,
            updated_at=now,
        )
        self.store.create_project_link(link)
        return link.to_dict()

    def delete_project_link(self, link_id):
        return self.store.delete_project_link(link_id)

    def accept_project_link(self, link_id):
        return self.store.accept_project_link(link_id)

    def reject_project_link(self, link_id):
        return self.store.reject_project_link(link_id)

    def reorder_project_links(self, project_id, link_ids):
        self.store.reorder_project_links(project_id, link_ids)
        return {"ok": True}

    def list_project_aliases(self, project_id):
        return [a.to_dict() for a in self.store.list_project_aliases(project_id)]

    def create_project_alias(self, project_id, alias_text):
        from uuid import uuid4
        from solo.core.models import ProjectAlias
        from solo.core.utils import _now

        pa = ProjectAlias(
            id=str(uuid4()),
            project_id=project_id,
            alias=alias_text.strip(),
            source="user",
            created_at=_now(),
        )
        self.store.create_project_alias(pa)
        return pa.to_dict()

    def delete_project_alias(self, alias_id):
        return {"deleted": self.store.delete_project_alias(alias_id)}

    def get_git_context(self, project_id, repo_path, since_days=7):
        """Fetch recent git commits filtered by project title and aliases."""
        from common.project_ai.external_context import fetch_git_commits, filter_commits_by_project

        project = self.store.get_project(project_id)
        if not project:
            return []
        aliases = [a.alias for a in self.store.list_project_aliases(project_id)]
        commits = fetch_git_commits(repo_path, since_days=since_days)
        return [c.to_dict() for c in filter_commits_by_project(commits, project.title, aliases)]

    # ── Project suggestions ─────────────────────────────────────────

    def list_project_suggestions(self, status=None, limit=None):
        return [
            s.to_dict()
            for s in self.store.list_project_suggestions(status=status, limit=limit)
        ]

    def accept_project_suggestion(self, suggestion_id):
        suggestion = self._get_suggestion_or_none(suggestion_id)
        if suggestion is None:
            return False
        ok = self.store.accept_project_suggestion(suggestion_id)
        if not ok:
            return False

        import json
        from uuid import uuid4
        from solo.core.models import Project, ProjectLink, Milestone
        from solo.core.utils import _now

        payload = json.loads(suggestion.proposed_payload_json)

        if suggestion.suggestion_type == "link_entity":
            now = _now()
            link = ProjectLink(
                id=str(uuid4()),
                project_id=suggestion.project_id,
                entity_type=payload.get("entity_type", "record"),
                entity_id=payload.get("entity_id", ""),
                source="ai_candidate",
                confidence="medium",
                status="active",
                created_at=now,
                updated_at=now,
            )
            try:
                self.store.create_project_link(link)
            except Exception:
                pass

        elif suggestion.suggestion_type == "create_project":
            now = _now()
            project = Project(
                id=str(uuid4()),
                title=payload.get("title", suggestion.title),
                description=payload.get("description", suggestion.rationale),
                status="active",
                priority="medium",
                tags=payload.get("tags", ""),
                start_date=now[:10],
                created_at=now,
                updated_at=now,
            )
            self.store.create_project(project)
            # Create suggested milestones
            for ms_title in payload.get("suggested_milestones", []):
                ms = Milestone(
                    id=str(uuid4()),
                    project_id=project.id,
                    title=ms_title,
                    created_at=now,
                    updated_at=now,
                )
                self.store.create_milestone(ms)
            # Link evidence records
            evidence = json.loads(suggestion.evidence_json)
            for ev in evidence:
                if ev.get("entity_type") == "record" and ev.get("entity_id"):
                    try:
                        link = ProjectLink(
                            id=str(uuid4()),
                            project_id=project.id,
                            entity_type="record",
                            entity_id=ev["entity_id"],
                            source="ai_candidate",
                            confidence="medium",
                            status="active",
                            created_at=now,
                            updated_at=now,
                        )
                        self.store.create_project_link(link)
                    except Exception:
                        pass

        return ok

    def reject_project_suggestion(self, suggestion_id):
        return self.store.reject_project_suggestion(suggestion_id)

    def snooze_project_suggestion(self, suggestion_id):
        return self.store.snooze_project_suggestion(suggestion_id)

    async def scan_for_projects(self):
        """Trigger project discovery scan on recent records."""
        from common.project_ai.discovery import scan_for_projects
        from solo.core.models import ProjectSuggestion
        from solo.core.utils import _now
        from uuid import uuid4
        import json

        candidates = await scan_for_projects(store=self.store, agent=None)
        created = 0
        now = _now()
        for c in candidates:
            suggestion = ProjectSuggestion(
                id=str(uuid4()),
                suggestion_type="create_project",
                title=c["title"],
                rationale=c.get("rationale", ""),
                proposed_payload_json=json.dumps({
                    "title": c["title"],
                    "description": c.get("rationale", ""),
                    "suggested_milestones": c.get("suggested_milestones", []),
                }),
                evidence_json=json.dumps(c.get("evidence", [])),
                confidence=c.get("confidence", 0.6),
                status="pending",
                source="ai",
                created_at=now,
                updated_at=now,
            )
            self.store.create_project_suggestion(suggestion)
            created += 1
        return {"created": created, "candidates": candidates}

    def get_project_brief(self):
        """Return dashboard brief: at-risk projects, attention projects, pending suggestion count."""
        all_projects = self.store.list_projects_with_detail(status="active", limit=50)
        at_risk = [p for p in all_projects if p.get("risk_status") == "at_risk"]
        attention = [p for p in all_projects if p.get("risk_status") == "attention"]
        pending_count = len(self.store.list_project_suggestions(status="pending"))
        return {
            "at_risk": at_risk[:5],
            "attention": attention[:5],
            "pending_suggestion_count": pending_count,
            "active_project_count": len(all_projects),
        }

    # --- Project State Analysis ---

    async def analyze_project_state(self, project_id: str) -> dict:
        """Analyze a project's state and persist signals."""
        from uuid import uuid4
        result = await analyze_project_state(
            store=self.store, project_id=project_id, agent=None,
        )
        # Persist signals
        for sig in result.get("signals", []):
            signal = ProjectSignal(
                id=str(uuid4()),
                project_id=project_id,
                signal_type=sig["signal_type"],
                summary=sig["summary"],
                severity=sig.get("severity", "info"),
                evidence_entity_type=sig.get("evidence_entity_type", ""),
                evidence_entity_id=sig.get("evidence_entity_id", ""),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self.store.create_project_signal(signal)
        return result

    def list_project_signals(self, project_id: str, *, limit: int | None = 50) -> list[dict]:
        signals = self.store.list_project_signals(project_id, limit=limit)
        return [s.to_dict() for s in signals]

    async def generate_project_snapshot(self, project_id: str) -> dict | None:
        """Generate and persist a daily snapshot."""
        from uuid import uuid4
        data = await generate_daily_snapshot(
            store=self.store, project_id=project_id, agent=None,
        )
        if not data:
            return None
        snapshot = ProjectSnapshot(
            id=str(uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            **data,
        )
        self.store.create_project_snapshot(snapshot)
        return snapshot.to_dict()

    def list_project_snapshots(self, project_id: str, *, limit: int | None = 30) -> list[dict]:
        snapshots = self.store.list_project_snapshots(project_id, limit=limit)
        return [s.to_dict() for s in snapshots]

    def generate_status_update(self, project_id: str) -> dict:
        """Generate a text status update from current project detail."""
        detail = self.store.get_project_detail(project_id)
        if detail is None:
            return {"text": ""}
        title = detail.get("title", "")
        pct = detail.get("completion_pct")
        risk = detail.get("risk_status", "normal")
        a7 = detail.get("activity_7d", 0)
        blockers = detail.get("open_blocker_count", 0)
        ms_done = detail.get("completed_milestone_count", 0)
        ms_total = detail.get("milestone_count", 0)

        lines = [f"## {title}"]
        if pct is not None:
            lines.append(f"Progress: {pct}%")
        if ms_total > 0:
            lines.append(f"Milestones: {ms_done}/{ms_total} completed")
        lines.append(f"Activity this week: {a7}")
        if blockers > 0:
            lines.append(f"Open blockers: {blockers}")
        if risk == "at_risk":
            lines.append("Status: AT RISK — needs immediate attention")
        elif risk == "attention":
            lines.append("Status: Needs attention")
        else:
            lines.append("Status: On track")
        return {"text": "\n".join(lines)}

    def generate_project_review(self, project_id: str) -> dict:
        """Generate a project review (template-based or LLM-enhanced)."""
        detail = self.store.get_project_detail(project_id)
        if detail is None:
            return {}

        milestones = self.store.list_milestones(project_id)
        links = self.store.list_project_links(project_id=project_id, status="active")

        # Build entity summaries
        entity_summaries: list[str] = []
        for lnk in links:
            summary = self.store.resolve_entity_summary(lnk.entity_type, lnk.entity_id)
            if summary:
                entity_summaries.append(f"- [{lnk.entity_type}] {summary}")

        # Build stats
        pct = detail.get("completion_pct", "N/A")
        ms_done = detail.get("completed_milestone_count", 0)
        ms_total = detail.get("milestone_count", 0)
        a7 = detail.get("activity_7d", 0)
        a30 = detail.get("activity_30d", 0)
        risk = detail.get("risk_status", "normal")
        blockers = detail.get("open_blocker_count", 0)

        # Build milestone list
        ms_lines = []
        for m in milestones:
            d = m.to_dict() if hasattr(m, "to_dict") else m
            status = "✓" if d.get("status") == "completed" else "○"
            date = f" ({d.get('target_date', '')})" if d.get("target_date") else ""
            ms_lines.append(f"- {status} {d.get('title', '')}{date}")

        # Build context for LLM
        context_parts = [
            f"Title: {detail.get('title', '')}",
            f"Status: {detail.get('status', '')}",
            f"Description: {detail.get('description', '')}",
            f"Completion: {pct}%",
            f"Milestones: {ms_done}/{ms_total}",
            f"Activity: {a7} (7d), {a30} (30d)",
            f"Risk: {risk}, Blockers: {blockers}",
            f"Target: {detail.get('target_date', 'none')}",
            f"Started: {detail.get('start_date', 'unknown')}",
            f"Completed: {detail.get('completed_at', 'N/A')}",
        ]
        if ms_lines:
            context_parts.append("Milestones:\n" + "\n".join(ms_lines))
        if entity_summaries:
            context_parts.append("Linked entities:\n" + "\n".join(entity_summaries[:20]))

        # Build template fallback
        lines = [f"# Project Review: {detail.get('title', '')}", ""]
        if detail.get("description"):
            lines.extend([detail["description"], ""])
        lines.extend([
            f"- Status: {detail.get('status', '')}",
            f"- Priority: {detail.get('priority', '')}",
            f"- Start: {detail.get('start_date', '')}",
            f"- Target: {detail.get('target_date', '')}",
            "",
            "## Milestones",
        ])
        lines.extend(ms_lines if ms_lines else ["- No milestones defined"])
        lines.extend(["", "## Statistics"])
        lines.extend([
            f"- Completion: {pct}%",
            f"- Milestones: {ms_done}/{ms_total}",
            f"- Activity (7d): {a7}, (30d): {a30}",
            f"- Risk: {risk}",
        ])
        template_content = "\n".join(lines)

        # Save as report
        from uuid import uuid4
        from solo.core.models import SoloReport
        from solo.core.utils import _now

        report = SoloReport(
            id=str(uuid4()),
            report_type="project_review",
            content=template_content,
            created_at=_now(),
            period_start=detail.get("start_date") or detail.get("created_at", ""),
            period_end=detail.get("completed_at") or _now(),
            metadata={"project_id": project_id, "project_title": detail.get("title", "")},
        )
        self.store.add_report(report)

        return {
            "id": report.id,
            "content": template_content,
            "report_type": "project_review",
            "context": "\n".join(context_parts),
        }

    async def generate_checkin_questions(self) -> list[dict]:
        """Generate project checkin questions."""
        return await generate_checkin_questions(
            store=self.store, agent=None,
            app_type=self._app_type,
        )

    def create_project_checkin(self, data: dict) -> dict:
        from uuid import uuid4
        checkin = ProjectCheckin(
            id=str(uuid4()),
            project_id=data.get("project_id", ""),
            channel=data.get("channel", "onboard"),
            question=data.get("question", ""),
            status=data.get("status", "sent"),
            response_record_id=data.get("response_record_id", ""),
            created_at=datetime.now(timezone.utc).isoformat(),
            responded_at=data.get("responded_at", ""),
        )
        self.store.create_project_checkin(checkin)
        return checkin.to_dict()

    def list_project_checkins(self, project_id: str, *, status: str | None = None, limit: int | None = 20) -> list[dict]:
        checkins = self.store.list_project_checkins(project_id, status=status, limit=limit)
        return [c.to_dict() for c in checkins]

    def update_project_checkin(self, checkin_id: str, **fields) -> bool:
        return self.store.update_project_checkin(checkin_id, **fields)

    def _get_suggestion_or_none(self, suggestion_id):
        suggestions = self.store.list_project_suggestions(limit=1000)
        for s in suggestions:
            if s.id == suggestion_id:
                return s
        return None

    # ── Memory management ───────────────────────────────────────────

    def list_memories(self) -> list[dict[str, Any]]:
        """List all memory entries from ~/.solo/memory/."""
        from openharness.memory.scan import scan_memory_files
        from openharness.utils.file_lock import exclusive_file_lock
        from pathlib import Path

        workspace_path = Path(self.workspace) if self.workspace else None
        memory_dir = self.store.workspace / "memory" if hasattr(self.store, 'workspace') else None
        
        if not memory_dir or not memory_dir.exists():
            return []

        # Use lock to ensure consistent read while agent might be writing
        lock_path = memory_dir / ".memory.lock"
        with exclusive_file_lock(lock_path):
            memories = []
            for header in scan_memory_files(
                workspace_path or memory_dir.parent,
                max_files=None,
                include_disabled=True,
                include_expired=True,
                memory_dir=memory_dir,
            ):
                try:
                    content = header.path.read_text(encoding="utf-8")
                    from openharness.memory.schema import split_memory_file
                    metadata, body, _, _ = split_memory_file(content)
                    
                    memories.append({
                        "id": str(metadata.get("id", "")),
                        "name": str(metadata.get("name", header.title)),
                        "description": str(metadata.get("description", "")),
                        "type": str(metadata.get("type", "user")),
                        "scope": str(metadata.get("scope", "private")),
                        "category": str(metadata.get("category", "")),
                        "importance": int(metadata.get("importance", 1)),
                        "source": str(metadata.get("source", "manual")),
                        "created_at": str(metadata.get("created_at", "")),
                        "updated_at": str(metadata.get("updated_at", "")),
                        "disabled": bool(metadata.get("disabled", False)),
                        "tags": list(metadata.get("tags", [])),
                        "content": body.strip(),
                        "file_path": str(header.path),
                    })
                except Exception:
                    continue

        # Sort by updated_at descending (outside lock for performance)
        memories.sort(key=lambda m: m["updated_at"], reverse=True)
        return memories

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Get a specific memory entry by ID."""
        from openharness.memory.scan import scan_memory_files
        from openharness.utils.file_lock import exclusive_file_lock
        from pathlib import Path

        workspace_path = Path(self.workspace) if self.workspace else None
        memory_dir = self.store.workspace / "memory" if hasattr(self.store, 'workspace') else None
        
        if not memory_dir or not memory_dir.exists():
            return None

        lock_path = memory_dir / ".memory.lock"
        with exclusive_file_lock(lock_path):
            for header in scan_memory_files(
                workspace_path or memory_dir.parent,
                max_files=None,
                include_disabled=True,
                include_expired=True,
                memory_dir=memory_dir,
            ):
                try:
                    content = header.path.read_text(encoding="utf-8")
                    from openharness.memory.schema import split_memory_file
                    metadata, body, _, _ = split_memory_file(content)
                    
                    if str(metadata.get("id")) == memory_id:
                        return {
                            "id": str(metadata.get("id", "")),
                            "name": str(metadata.get("name", header.title)),
                            "description": str(metadata.get("description", "")),
                            "type": str(metadata.get("type", "user")),
                            "scope": str(metadata.get("scope", "private")),
                            "category": str(metadata.get("category", "")),
                            "importance": int(metadata.get("importance", 1)),
                            "source": str(metadata.get("source", "manual")),
                            "created_at": str(metadata.get("created_at", "")),
                            "updated_at": str(metadata.get("updated_at", "")),
                            "disabled": bool(metadata.get("disabled", False)),
                            "tags": list(metadata.get("tags", [])),
                            "content": body.strip(),
                            "file_path": str(header.path),
                        }
                except Exception:
                    continue

        return None

    def create_memory(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new memory entry."""
        from solo.core.memory import add_memory_entry

        title = data.get("name", "")
        content = data.get("content", data.get("description", ""))
        
        # add_memory_entry already uses exclusive_file_lock internally
        path = add_memory_entry(self.workspace, title, content)
        
        # Read back the created memory (with lock)
        memories = self.list_memories()
        for memory in memories:
            if memory["file_path"] == str(path):
                return memory
        
        raise Exception("Failed to create memory")

    def update_memory(self, memory_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing memory entry."""
        from openharness.memory.schema import (
            split_memory_file,
            render_memory_file,
            utc_now,
            format_datetime,
        )
        from openharness.utils.file_lock import exclusive_file_lock
        from openharness.utils.fs import atomic_write_text
        from pathlib import Path

        # Find the memory file
        memory = self.get_memory(memory_id)
        if not memory:
            return None

        file_path = Path(memory["file_path"])
        if not file_path.exists():
            return None

        memory_dir = file_path.parent
        lock_path = memory_dir / ".memory.lock"

        try:
            # Lock during read-modify-write to prevent agent conflicts
            with exclusive_file_lock(lock_path):
                content = file_path.read_text(encoding="utf-8")
                metadata, body, _, _ = split_memory_file(content)

                # Update fields
                if "name" in updates:
                    metadata["name"] = updates["name"]
                if "description" in updates:
                    metadata["description"] = updates["description"]
                if "type" in updates:
                    metadata["type"] = updates["type"]
                if "scope" in updates:
                    metadata["scope"] = updates["scope"]
                if "category" in updates:
                    metadata["category"] = updates["category"]
                if "importance" in updates:
                    metadata["importance"] = updates["importance"]
                if "tags" in updates:
                    metadata["tags"] = updates["tags"]
                if "content" in updates:
                    body = updates["content"].strip() + "\n"

                # Update timestamp
                metadata["updated_at"] = format_datetime(utc_now())

                # Write back atomically (still within lock)
                atomic_write_text(file_path, render_memory_file(metadata, body))

            # Return updated memory
            return self.get_memory(memory_id)

        except Exception as e:
            raise Exception(f"Failed to update memory: {e}")

    def delete_memory(self, memory_id: str) -> bool:
        """Hard-delete a memory entry from disk."""
        from solo.core.memory import delete_memory_file

        memory = self.get_memory(memory_id)
        if not memory:
            return False

        return delete_memory_file(self.workspace, memory_id)
