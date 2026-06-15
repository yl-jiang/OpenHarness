"""Solo REST routes for onboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from onboard.services.solo_service import SoloService


router = APIRouter(prefix="/api/solo", tags=["solo"])


class ReportRequest(BaseModel):
    type: str
    profile: str | None = None
    start_date: str | None = None
    end_date: str | None = None


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


@router.put("/todos/{todo_id}/start")
def todo_start(todo_id: str, workspace: str | None = None) -> dict[str, bool]:
    if not _service(workspace).start_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found or not in pending status")
    return {"ok": True}


@router.put("/todos/{todo_id}/revert")
def todo_revert(todo_id: str, workspace: str | None = None) -> dict[str, bool]:
    if not _service(workspace).revert_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found or not in progress")
    return {"ok": True}


@router.put("/todos/{todo_id}/done")
def todo_done(todo_id: str, workspace: str | None = None) -> dict[str, bool]:
    if not _service(workspace).complete_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found or already done")
    return {"ok": True}


@router.put("/todos/{todo_id}/reopen")
def todo_reopen(todo_id: str, workspace: str | None = None) -> dict[str, bool]:
    if not _service(workspace).reopen_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found or not in done status")
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


@router.delete("/reports/{report_id}")
def delete_report(report_id: str, workspace: str | None = None) -> dict[str, bool]:
    deleted = _service(workspace).delete_report(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"deleted": True}


@router.post("/reports/generate")
async def generate_report(request: ReportRequest, workspace: str | None = None) -> dict[str, Any]:
    return await _service(workspace).generate_report(
        request.type, profile=request.profile, start_date=request.start_date, end_date=request.end_date,
    )


@router.post("/process")
async def process(request: ProcessRequest | None = None, workspace: str | None = None) -> dict[str, Any]:
    return await _service(workspace).process_pending(limit=request.limit if request else 20)


@router.get("/config")
def config(workspace: str | None = None) -> dict[str, Any]:
    return _service(workspace).config()


@router.put("/config")
async def update_config(request: Request, workspace: str | None = None) -> dict[str, Any]:
    body = await request.json()
    return _service(workspace).update_config(body)


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


@router.get("/feed-digests")
def feed_digests(workspace: str | None = None, preset: str | None = None) -> list[dict[str, Any]]:
    return _service(workspace).list_feed_digests(preset=preset)


@router.get("/feed-digests/{digest_id}")
def feed_digest(digest_id: str, workspace: str | None = None) -> dict[str, Any]:
    result = _service(workspace).get_feed_digest(digest_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Feed digest not found")
    return result


@router.delete("/feed-digests/{digest_id}")
def delete_feed_digest(digest_id: str, workspace: str | None = None) -> dict[str, bool]:
    deleted = _service(workspace).delete_feed_digest(digest_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Feed digest not found")
    return {"deleted": True}


class RunFeedDigestRequest(BaseModel):
    preset: str | None = None


@router.post("/feed-digests/run")
async def run_feed_digest(request: RunFeedDigestRequest | None = None, workspace: str | None = None) -> dict[str, Any]:
    return await _service(workspace).run_feed_digest(preset=request.preset if request else None)


@router.post("/feed-digests/run/stream")
async def run_feed_digest_stream(
    request: RunFeedDigestRequest | None = None, workspace: str | None = None,
) -> StreamingResponse:
    preset = request.preset if request else None
    events = _service(workspace).run_feed_digest_stream(preset=preset)

    async def event_source() -> AsyncIterator[str]:
        async for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Project management ──────────────────────────────────────────────


class ProjectCreateRequest(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    start_date: str = ""
    target_date: str = ""
    tags: str = ""
    template: str = ""


class ProjectUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    start_date: str | None = None
    target_date: str | None = None
    tags: str | None = None


class MilestoneCreateRequest(BaseModel):
    title: str
    description: str = ""
    target_date: str = ""


class ProjectLinkCreateRequest(BaseModel):
    entity_type: str
    entity_id: str
    source: str = "user"


class ArchiveRequest(BaseModel):
    reason: str = ""


class ReorderLinksRequest(BaseModel):
    link_ids: list[str]


@router.get("/projects")
def projects(
    workspace: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return _service(workspace).list_projects(status=status, limit=limit, offset=offset)


@router.get("/projects/brief")
def project_brief(workspace: str | None = None):
    return _service(workspace).get_project_brief()


@router.get("/project-templates")
def project_templates(workspace: str | None = None):
    return _service(workspace).list_project_templates()


@router.post("/projects")
def create_project(request: ProjectCreateRequest, workspace: str | None = None):
    return _service(workspace).create_project(request.model_dump())


@router.get("/projects/{project_id}")
def get_project(project_id: str, workspace: str | None = None):
    result = _service(workspace).get_project(project_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.put("/projects/{project_id}")
def update_project(project_id: str, request: ProjectUpdateRequest, workspace: str | None = None):
    data = {k: v for k, v in request.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=422, detail="No fields to update")
    result = _service(workspace).update_project(project_id, data)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, workspace: str | None = None):
    ok = _service(workspace).delete_project(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": True}


@router.put("/projects/{project_id}/complete")
def complete_project(project_id: str, workspace: str | None = None):
    result = _service(workspace).complete_project(project_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.put("/projects/{project_id}/archive")
def archive_project(
    project_id: str,
    request: ArchiveRequest | None = None,
    workspace: str | None = None,
):
    reason = request.reason if request else ""
    result = _service(workspace).archive_project(project_id, reason)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.put("/projects/{project_id}/reactivate")
def reactivate_project(project_id: str, workspace: str | None = None):
    result = _service(workspace).reactivate_project(project_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.get("/projects/{project_id}/milestones")
def milestones(project_id: str, workspace: str | None = None):
    return _service(workspace).list_milestones(project_id)


@router.post("/projects/{project_id}/milestones")
def create_milestone(project_id: str, request: MilestoneCreateRequest, workspace: str | None = None):
    return _service(workspace).create_milestone(project_id, request.model_dump())


@router.put("/milestones/{milestone_id}/complete")
def complete_milestone(milestone_id: str, workspace: str | None = None):
    ok = _service(workspace).complete_milestone(milestone_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return {"ok": True}


@router.delete("/milestones/{milestone_id}")
def delete_milestone(milestone_id: str, workspace: str | None = None):
    ok = _service(workspace).delete_milestone(milestone_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return {"deleted": True}


@router.get("/projects/{project_id}/links")
def project_links(project_id: str, workspace: str | None = None):
    return _service(workspace).list_project_links(project_id)


@router.post("/projects/{project_id}/links")
def create_project_link(project_id: str, request: ProjectLinkCreateRequest, workspace: str | None = None):
    return _service(workspace).create_project_link(project_id, request.model_dump())


@router.put("/projects/{project_id}/links/reorder")
def reorder_project_links(project_id: str, request: ReorderLinksRequest, workspace: str | None = None):
    return _service(workspace).reorder_project_links(project_id, request.link_ids)


class AliasCreateRequest(BaseModel):
    alias: str


class GitContextRequest(BaseModel):
    repo_path: str
    since_days: int = 7


@router.get("/projects/{project_id}/aliases")
def list_project_aliases(project_id: str, workspace: str | None = None):
    return _service(workspace).list_project_aliases(project_id)


@router.post("/projects/{project_id}/aliases")
def create_project_alias(project_id: str, request: AliasCreateRequest, workspace: str | None = None):
    return _service(workspace).create_project_alias(project_id, request.alias)


@router.delete("/project-aliases/{alias_id}")
def delete_project_alias(alias_id: str, workspace: str | None = None):
    return _service(workspace).delete_project_alias(alias_id)


@router.post("/projects/{project_id}/git-context")
def git_context(project_id: str, request: GitContextRequest, workspace: str | None = None):
    return _service(workspace).get_git_context(project_id, request.repo_path, request.since_days)


@router.delete("/project-links/{link_id}")
def delete_project_link(link_id: str, workspace: str | None = None):
    ok = _service(workspace).delete_project_link(link_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"deleted": True}


@router.put("/project-links/{link_id}/accept")
def accept_project_link(link_id: str, workspace: str | None = None):
    ok = _service(workspace).accept_project_link(link_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"ok": True}


@router.put("/project-links/{link_id}/reject")
def reject_project_link(link_id: str, workspace: str | None = None):
    ok = _service(workspace).reject_project_link(link_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"ok": True}


@router.get("/project-suggestions")
def list_project_suggestions(
    status: str | None = None,
    limit: int | None = None,
    workspace: str | None = None,
):
    return _service(workspace).list_project_suggestions(status=status, limit=limit)


@router.put("/project-suggestions/{suggestion_id}/accept")
def accept_project_suggestion(suggestion_id: str, workspace: str | None = None):
    ok = _service(workspace).accept_project_suggestion(suggestion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"ok": True}


@router.put("/project-suggestions/{suggestion_id}/reject")
def reject_project_suggestion(suggestion_id: str, workspace: str | None = None):
    ok = _service(workspace).reject_project_suggestion(suggestion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"ok": True}


@router.put("/project-suggestions/{suggestion_id}/snooze")
def snooze_project_suggestion(suggestion_id: str, workspace: str | None = None):
    ok = _service(workspace).snooze_project_suggestion(suggestion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"ok": True}


@router.post("/projects/scan")
async def scan_projects(workspace: str | None = None):
    return await _service(workspace).scan_for_projects()


# ── Phase 3: Project state analysis ─────────────────────────────────


@router.get("/projects/{project_id}/timeline")
def project_timeline(project_id: str, limit: int = 50, workspace: str | None = None):
    return _service(workspace).get_project_timeline(project_id, limit=limit)


@router.get("/projects/{project_id}/signals")
async def list_project_signals(project_id: str, limit: int = 50, workspace: str | None = None):
    svc = _service(workspace)
    return svc.list_project_signals(project_id, limit=limit)


@router.get("/projects/{project_id}/snapshots")
async def list_project_snapshots(project_id: str, limit: int = 30, workspace: str | None = None):
    svc = _service(workspace)
    return svc.list_project_snapshots(project_id, limit=limit)


@router.post("/projects/{project_id}/analyze")
async def analyze_project_state(project_id: str, workspace: str | None = None):
    svc = _service(workspace)
    return await svc.analyze_project_state(project_id)


@router.post("/projects/{project_id}/snapshot")
async def generate_project_snapshot(project_id: str, workspace: str | None = None):
    svc = _service(workspace)
    return await svc.generate_project_snapshot(project_id)


@router.post("/projects/{project_id}/status-update")
async def generate_status_update(project_id: str, workspace: str | None = None):
    svc = _service(workspace)
    return svc.generate_status_update(project_id)


@router.get("/project-checkins")
async def list_checkin_questions(
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    workspace: str | None = None,
):
    svc = _service(workspace)
    if project_id:
        return svc.list_project_checkins(project_id, status=status, limit=limit)
    return await svc.generate_checkin_questions()


@router.post("/project-checkins")
async def create_checkin(body: dict, workspace: str | None = None):
    svc = _service(workspace)
    return svc.create_project_checkin(body)


@router.put("/project-checkins/{checkin_id}")
async def update_checkin(checkin_id: str, body: dict, workspace: str | None = None):
    svc = _service(workspace)
    return {"ok": svc.update_project_checkin(checkin_id, **body)}


@router.post("/projects/{project_id}/review")
def review_project(project_id: str, workspace: str | None = None):
    svc = _service(workspace)
    result = svc.generate_project_review(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


# ── Memory management ───────────────────────────────────────────────


class MemoryCreateRequest(BaseModel):
    name: str
    description: str = ""
    type: str = "user"
    scope: str = "private"
    category: str = ""
    importance: int = 1
    tags: list[str] = []
    content: str = ""


class MemoryUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    type: str | None = None
    scope: str | None = None
    category: str | None = None
    importance: int | None = None
    tags: list[str] | None = None
    content: str | None = None


@router.get("/memory")
def list_memories(workspace: str | None = None) -> list[dict[str, Any]]:
    return _service(workspace).list_memories()


@router.get("/memory/{memory_id}")
def get_memory(memory_id: str, workspace: str | None = None) -> dict[str, Any]:
    result = _service(workspace).get_memory(memory_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return result


@router.post("/memory")
async def create_memory(request: MemoryCreateRequest, workspace: str | None = None) -> dict[str, Any]:
    return _service(workspace).create_memory(request.model_dump())


@router.put("/memory/{memory_id}")
async def update_memory(
    memory_id: str, request: MemoryUpdateRequest, workspace: str | None = None,
) -> dict[str, Any]:
    data = {k: v for k, v in request.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=422, detail="No fields to update")
    result = _service(workspace).update_memory(memory_id, data)
    if result is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return result


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: str, workspace: str | None = None) -> dict[str, bool]:
    deleted = _service(workspace).delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}
