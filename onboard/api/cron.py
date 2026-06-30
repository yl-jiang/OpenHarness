"""Read-only cron scheduler dashboard routes.

Reads from the selected app's workspace cron registry (solo or wolo),
not the shared openharness registry.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/api/cron", tags=["cron"])


def _workspace_data_dir(app_name: str) -> Path:
    if app_name == "solo":
        from solo.core.workspace import get_data_dir as solo_get_data_dir

        return solo_get_data_dir()
    if app_name == "wolo":
        from wolo.core.workspace import get_data_dir as wolo_get_data_dir

        return wolo_get_data_dir()
    raise HTTPException(status_code=400, detail="app_name must be 'solo' or 'wolo'")


def _load_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        path.unlink(missing_ok=True)
        return None
    return pid


@router.get("/jobs")
def list_cron_jobs(app_name: str = "solo") -> list[dict[str, Any]]:
    """Return all scheduled cron jobs for the selected app (view-only)."""
    data_dir = _workspace_data_dir(app_name)
    return _load_jobs(data_dir / "cron_jobs.json")


@router.get("/status")
def get_scheduler_status(app_name: str = "solo") -> dict[str, Any]:
    """Return scheduler daemon status for the selected app."""
    data_dir = _workspace_data_dir(app_name)
    log_dir = data_dir.parent / "logs"
    registry_path = data_dir / "cron_jobs.json"
    pid_path = data_dir / "cron_scheduler.pid"
    history_path = data_dir / "cron_history.jsonl"

    jobs = _load_jobs(registry_path)
    enabled = [j for j in jobs if j.get("enabled", True)]
    pid = _read_pid(pid_path)

    return {
        "running": pid is not None,
        "pid": pid,
        "total_jobs": len(jobs),
        "enabled_jobs": len(enabled),
        "log_file": str(log_dir / "cron_scheduler.jsonl"),
        "history_file": str(history_path),
    }
