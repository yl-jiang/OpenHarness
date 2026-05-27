"""Read/write facade for solo data used by onboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from solo.agent import OpenHarnessSoloAgent
from solo.config import load_config
from solo.core.store import SoloStore
from solo.gateway.service import gateway_status, start_gateway_process, stop_gateway_process
from solo.processor import SoloProcessor

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
    to_jsonable,
    top_tags,
)


class SoloService:
    """Small service wrapper around SoloStore for WebUI routes."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self.workspace = workspace
        self.store = SoloStore(workspace)

    def stats(self) -> dict[str, Any]:
        status = self.store.status()
        records = self.store.list_records()
        todos = self.store.list_todos()
        pending_todos = [todo for todo in todos if todo.status != "done"]
        return {
            "total_entries": int(status["entries"]),
            "total_records": int(status["records"]),
            "pending_entries": int(status["pending_confirmations"]),
            "total_todos": int(status["todos"]),
            "pending_todos": len(pending_todos),
            "this_week_records": count_this_week(records),
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

    async def generate_report(
        self, report_type: str, profile: str | None = None, start_date: str | None = None, end_date: str | None = None,
    ) -> dict[str, Any]:
        config = load_config(self.workspace)
        agent = OpenHarnessSoloAgent(profile=profile or config.provider_profile)
        report = await SoloProcessor(self.store, agent).generate_report(
            report_type, start_date=start_date, end_date=end_date,
        )
        return to_jsonable(report)

    async def process_pending(self, limit: int = 20) -> dict[str, Any]:
        config = load_config(self.workspace)
        agent = OpenHarnessSoloAgent(profile=config.provider_profile)
        result = await SoloProcessor(self.store, agent).process_pending(limit=limit)
        return to_jsonable(result)

    def config(self) -> dict[str, Any]:
        config = load_config(self.workspace)
        return {"workspace": str(self.store.workspace), **config.model_dump()}

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
