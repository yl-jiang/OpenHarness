"""Health API routes (solo-only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Query, UploadFile

from onboard.services.solo_service import SoloService


router = APIRouter(prefix="/api/solo/health", tags=["health"])


def _service(workspace: str | None = None) -> SoloService:
    return SoloService(workspace)


@router.get("/subjects")
def health_subjects(workspace: str | None = None) -> dict[str, Any]:
    return {"subjects": _service(workspace).health_subjects()}


@router.get("/overview")
def health_overview(
    subject: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).health_overview(subject=subject)


@router.get("/records")
def health_records(
    subject: str | None = None,
    category: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).list_health_records(
        subject=subject, category=category, status=status,
        date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )


def _month_range(month: str | None) -> tuple[str | None, str | None]:
    """Convert YYYY-MM to exact (date_from, date_to) boundaries."""
    if not month:
        return None, None
    from datetime import datetime, timedelta
    try:
        start = datetime.strptime(month + "-01", "%Y-%m-%d")
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    except ValueError:
        return None, None


@router.get("/fitness")
def health_fitness(
    subject: str | None = None,
    days: int = Query(30, ge=1, le=365),
    month: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    date_from, date_to = _month_range(month)
    svc = _service(workspace)
    if date_from:
        return svc.health_fitness_trend(subject=subject, date_from=date_from, date_to=date_to)
    return svc.health_fitness_trend(subject=subject, days=days)


@router.get("/sleep")
def health_sleep(
    subject: str | None = None,
    days: int = Query(30, ge=1, le=365),
    month: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    date_from, date_to = _month_range(month)
    svc = _service(workspace)
    if date_from:
        return svc.health_sleep_trend(subject=subject, date_from=date_from, date_to=date_to)
    return svc.health_sleep_trend(subject=subject, days=days)


@router.get("/symptoms")
def health_symptoms(
    subject: str | None = None,
    days: int = Query(90, ge=1, le=365),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).health_symptom_ranking(subject=subject, days=days)


@router.get("/medications")
def health_medications(
    subject: str | None = None,
    days: int = Query(90, ge=1, le=365),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).health_medications(subject=subject, days=days)


@router.get("/mental")
def health_mental(
    subject: str | None = None,
    days: int = Query(30, ge=1, le=365),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).health_mental_trend(subject=subject, days=days)


@router.get("/vitals")
def health_vitals(
    subject: str | None = None,
    days: int = Query(90, ge=1, le=365),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).health_vitals(subject=subject, days=days)


@router.get("/vital-trends")
def health_vital_trends(
    subject: str | None = None,
    month: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).health_vital_trends(subject=subject, month=month)


@router.get("/period")
def health_period(
    subject: str | None = None,
    days: int = Query(180, ge=1, le=3650),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).health_period_cycles(subject=subject, days=days)


@router.get("/timeline")
def health_timeline(
    subject: str | None = None,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).health_timeline(subject=subject, limit=limit, offset=offset)


@router.delete("/records/{record_id}")
def delete_health_record(record_id: str, workspace: str | None = None) -> dict[str, Any]:
    ok = _service(workspace).delete_health_record(record_id)
    return {"ok": ok}


@router.patch("/records/{record_id}")
def update_health_record(
    record_id: str,
    updates: dict,
    workspace: str | None = None,
) -> dict[str, Any]:
    ok = _service(workspace).update_health_record(record_id, updates)
    return {"ok": ok}


@router.post("/import/apple-health")
async def import_apple_health(
    file: UploadFile = File(...),
    workspace: str | None = None,
) -> dict[str, Any]:
    import logging
    import traceback
    logger = logging.getLogger(__name__)
    try:
        content = await file.read()
        logger.info("Apple Health import: received %s (%d bytes)", file.filename, len(content))
        return _service(workspace).import_apple_health(content, file.filename or "export.xml")
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Apple Health import failed:\n%s", tb)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
