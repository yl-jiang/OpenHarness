"""Read/write facade for wolo data used by onboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wolo.agent import OpenHarnessWoloAgent
from wolo.config import load_config
from wolo.core.store import WoloStore
from wolo.gateway.service import gateway_status, start_gateway_process, stop_gateway_process
from wolo.processor import WoloProcessor

from onboard.services.common import (
    count_this_week,
    daily_counts,
    emotion_distribution,
    filter_entries,
    filter_records,
    find_by_id,
    newest_first,
    paginate,
    split_csv,
    stream_feed_digest_run,
    to_jsonable,
    top_tags,
)


class WoloService:
    """Small service wrapper around WoloStore for WebUI routes."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self.workspace = workspace
        self.store = WoloStore(workspace)

    def stats(self) -> dict[str, Any]:
        status = self.store.status()
        records = self.store.list_records()
        todos = self.store.list_todos()
        blockers = self.store.list_highlights(kind="blocker")
        pending_todos = [todo for todo in todos if todo.status != "done"]
        open_blockers = [item for item in blockers if "resolved" not in item.tags.lower()]
        llm_usage = self.store.llm_usage_summary()
        return {
            "total_entries": int(status["entries"]),
            "total_records": int(status["records"]),
            "pending_entries": int(status["pending_confirmations"]),
            "total_todos": int(status["todos"]),
            "pending_todos": len(pending_todos),
            "this_week_records": count_this_week(records),
            "total_decisions": int(status["decisions"]),
            "total_highlights": int(status["highlights"]),
            "open_blockers": len(open_blockers),
            "llm_total_calls": int(llm_usage["total_calls"]),
            "llm_usage_models": llm_usage["models"],
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
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            to_jsonable(todo)
            for todo in self.store.list_todos(status=status, project=project)
        ]

    def complete_todo(self, todo_id: str) -> bool:
        return self.store.complete_todo(todo_id)

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
        from wolo.feed_digest import run_feed_digest
        report = await run_feed_digest(workspace=self.workspace, preset_name=preset)
        return to_jsonable(report)

    def run_feed_digest_stream(self, preset: str | None = None):
        from wolo.feed_digest import run_feed_digest
        return stream_feed_digest_run(run_feed_digest, workspace=self.workspace, preset=preset)

    async def generate_report(
        self, report_type: str, profile: str | None = None, start_date: str | None = None, end_date: str | None = None,
    ) -> dict[str, Any]:
        config = load_config(self.workspace)
        agent = OpenHarnessWoloAgent(
            profile=profile or config.provider_profile,
            record_model_call=self.store.record_llm_call,
        )
        report = await WoloProcessor(self.store, agent).generate_report(
            report_type, start_date=start_date, end_date=end_date,
        )
        return to_jsonable(report)

    async def process_pending(self, limit: int = 20) -> dict[str, Any]:
        config = load_config(self.workspace)
        agent = OpenHarnessWoloAgent(
            profile=config.provider_profile,
            record_model_call=self.store.record_llm_call,
        )
        result = await WoloProcessor(self.store, agent).process_pending(limit=limit)
        return to_jsonable(result)

    def config(self) -> dict[str, Any]:
        config = load_config(self.workspace)
        return {"workspace": str(self.store.workspace), **config.model_dump()}

    def list_decisions(
        self,
        *,
        project: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            to_jsonable(item)
            for item in self.store.list_decisions(project=project, query=query)
        ]

    def list_highlights(
        self,
        *,
        kind: str | None = None,
        project: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            to_jsonable(item)
            for item in self.store.list_highlights(kind=kind, project=project, query=query)
        ]

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
