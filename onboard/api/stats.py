"""Unified stats routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from onboard.services.solo_service import SoloService
from onboard.services.wolo_service import WoloService


router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/{app_name}")
def stats(app_name: str) -> dict[str, Any]:
    if app_name == "solo":
        return SoloService().stats()
    if app_name == "wolo":
        return WoloService().stats()
    raise HTTPException(status_code=404, detail="Unknown app")
