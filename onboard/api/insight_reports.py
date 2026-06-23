"""Insight report API routes (solo-only, domain-specific)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from onboard.services.solo_service import SoloService


router = APIRouter(prefix="/api/solo/insight-reports", tags=["insight-reports"])


class InsightReportGenerateRequest(BaseModel):
    domain: str          # "health" | "finance"
    report_type: str     # "weekly" | "monthly" | "yearly"
    start_date: str | None = None
    end_date: str | None = None


def _service(workspace: str | None = None) -> SoloService:
    return SoloService(workspace)


@router.post("/generate")
async def generate_insight_report(
    request: InsightReportGenerateRequest,
    workspace: str | None = None,
) -> dict[str, Any]:
    if request.domain not in ("health", "finance"):
        raise HTTPException(status_code=400, detail="domain must be 'health' or 'finance'")
    if request.report_type not in ("weekly", "monthly", "yearly"):
        raise HTTPException(status_code=400, detail="report_type must be weekly/monthly/yearly")
    return await _service(workspace).generate_insight_report(
        domain=request.domain,
        report_type=request.report_type,
        start_date=request.start_date,
        end_date=request.end_date,
    )


@router.get("")
def list_insight_reports(
    domain: str | None = None,
    report_type: str | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    return _service(workspace).list_insight_reports(domain=domain, report_type=report_type)


@router.get("/{report_id}")
def get_insight_report(
    report_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    result = _service(workspace).get_insight_report(report_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Insight report not found")
    return result


@router.delete("/{report_id}")
def delete_insight_report(
    report_id: str,
    workspace: str | None = None,
) -> dict[str, bool]:
    ok = _service(workspace).delete_insight_report(report_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Insight report not found")
    return {"ok": True}
