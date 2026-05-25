"""Unified gateway lifecycle routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from onboard.services.solo_service import SoloService
from onboard.services.wolo_service import WoloService


router = APIRouter(prefix="/api/lifecycle", tags=["lifecycle"])


def _service(app_name: str) -> SoloService | WoloService:
    if app_name == "solo":
        return SoloService()
    if app_name == "wolo":
        return WoloService()
    raise HTTPException(status_code=404, detail="Unknown app")


@router.get("/{app_name}/gateway/status")
def status(app_name: str) -> dict[str, Any]:
    return _service(app_name).gateway_status()


@router.post("/{app_name}/gateway/start")
def start(app_name: str, cwd: str = str(Path.cwd())) -> dict[str, Any]:
    return _service(app_name).start_gateway(cwd)


@router.post("/{app_name}/gateway/stop")
def stop(app_name: str, cwd: str = str(Path.cwd())) -> dict[str, Any]:
    return _service(app_name).stop_gateway(cwd)
