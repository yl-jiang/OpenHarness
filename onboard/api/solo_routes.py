"""Solo REST routes for onboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from onboard.services.solo_service import SoloService


router = APIRouter(prefix="/api/solo", tags=["solo"])


class ReportRequest(BaseModel):
    type: str
    profile: str | None = None


class ProcessRequest(BaseModel):
    limit: int = 20


def _service(workspace: str | None = None) -> SoloService:
    return SoloService(workspace)


@router.get("/stats")
def stats(workspace: str | None = None) -> dict[str, Any]:
    return _service(workspace).stats()


@router.get("/entries")
def entries(
    workspace: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    channel: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).list_entries(limit=limit, offset=offset, channel=channel)


@router.get("/entries/{entry_id}")
def entry(entry_id: str, workspace: str | None = None) -> dict[str, Any]:
    result = _service(workspace).get_entry(entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return result


@router.get("/records")
def records(
    workspace: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tag: str | None = None,
    emotion: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).list_records(
        limit=limit,
        offset=offset,
        tag=tag,
        emotion=emotion,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/records/{record_id}")
def record(record_id: str, workspace: str | None = None) -> dict[str, Any]:
    result = _service(workspace).get_record(record_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return result


@router.get("/search")
def search(
    workspace: str | None = None,
    q: str | None = None,
    tags: str | None = None,
    emotions: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    return _service(workspace).search(
        query=q,
        tags=tags,
        emotions=emotions,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@router.get("/todos")
def todos(
    workspace: str | None = None,
    status: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    return _service(workspace).list_todos(status=status, category=category)


@router.put("/todos/{todo_id}/done")
def todo_done(todo_id: str, workspace: str | None = None) -> dict[str, bool]:
    if not _service(workspace).complete_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found or already done")
    return {"ok": True}


@router.get("/reports")
def reports(workspace: str | None = None, type: str | None = None) -> list[dict[str, Any]]:  # noqa: A002
    return _service(workspace).list_reports(report_type=type)


@router.get("/reports/{report_id}")
def report(report_id: str, workspace: str | None = None) -> dict[str, Any]:
    result = _service(workspace).get_report(report_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return result


@router.post("/reports/generate")
async def generate_report(request: ReportRequest, workspace: str | None = None) -> dict[str, Any]:
    return await _service(workspace).generate_report(request.type, profile=request.profile)


@router.post("/process")
async def process(request: ProcessRequest | None = None, workspace: str | None = None) -> dict[str, Any]:
    return await _service(workspace).process_pending(limit=request.limit if request else 20)


@router.get("/config")
def config(workspace: str | None = None) -> dict[str, Any]:
    return _service(workspace).config()


@router.get("/gateway/status")
def gateway_status(workspace: str | None = None) -> dict[str, Any]:
    return _service(workspace).gateway_status()


@router.post("/gateway/start")
def gateway_start(
    cwd: str = str(Path.cwd()),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).start_gateway(cwd)


@router.post("/gateway/stop")
def gateway_stop(
    cwd: str = str(Path.cwd()),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).stop_gateway(cwd)
