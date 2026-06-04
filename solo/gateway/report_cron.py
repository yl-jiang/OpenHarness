"""Auto-register weekly/monthly report cron jobs for the solo app."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openharness.services.cron import next_run_time, validate_cron_expression
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text
from openharness.utils.log import get_logger
from solo.core.workspace import get_data_dir

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_TIMEZONE = "Asia/Shanghai"
_DEFAULT_TIMEOUT_SECONDS = 900
_WEEKLY_REPORT_SCHEDULE = "0 21 * * 0"
_MONTHLY_REPORT_SCHEDULE = "0 21 28-31 * *"


def _cron_registry_path(workspace: str | Path | None) -> Path:
    data_dir = get_data_dir(workspace)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "cron_jobs.json"


def _load(workspace: str | Path | None) -> list[dict[str, Any]]:
    path = _cron_registry_path(workspace)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _save(workspace: str | Path | None, jobs: list[dict[str, Any]]) -> None:
    atomic_write_text(
        _cron_registry_path(workspace),
        json.dumps(jobs, indent=2) + "\n",
    )


def _get_cron_job(job_name: str, workspace: str | Path | None) -> dict[str, Any] | None:
    for job in _load(workspace):
        if job.get("name") == job_name:
            return job
    return None


def _upsert_cron_job(job: dict[str, Any], workspace: str | Path | None) -> None:
    job.setdefault("enabled", True)
    job.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    schedule = job.get("schedule", "")
    if validate_cron_expression(schedule):
        job["next_run"] = next_run_time(
            schedule,
            tz=job.get("timezone") or job.get("tz"),
        ).isoformat()
    lock = _cron_registry_path(workspace).with_suffix(".json.lock")
    with exclusive_file_lock(lock):
        jobs = [item for item in _load(workspace) if item.get("name") != job.get("name")]
        jobs.append(job)
        jobs.sort(key=lambda item: str(item.get("name", "")))
        _save(workspace, jobs)


def _build_command(
    app: str,
    *,
    report_type: str,
    workspace: str | Path | None,
    job_name: str,
    timezone_name: str,
) -> str:
    python = sys.executable
    script = str(_APP_ROOT / "gateway" / "report_runner.py")
    command = (
        f"{python} {script} --app {app} --report-type {report_type} "
        f"--job-name {job_name} --timezone {timezone_name}"
    )
    if workspace:
        command += f" --workspace {workspace}"
    return command


def _ensure_report_job(
    app: str,
    *,
    report_type: str,
    workspace: str | Path | None = None,
    schedule: str,
    timezone_name: str,
    timeout_s: int,
) -> None:
    job_name = f"{app}-{report_type}-report"
    expected_command = _build_command(
        app,
        report_type=report_type,
        workspace=workspace,
        job_name=job_name,
        timezone_name=timezone_name,
    )
    expected_cwd = str(_REPO_ROOT)
    expected_timeout = max(1, int(timeout_s))
    expected_metadata = {"app": app, "report_type": report_type}
    existing = _get_cron_job(job_name, workspace)
    if existing is not None:
        updated = False
        if str(existing.get("command") or "").strip() != expected_command:
            existing["command"] = expected_command
            updated = True
        if str(existing.get("cwd") or "").strip() != expected_cwd:
            existing["cwd"] = expected_cwd
            updated = True
        if str(existing.get("schedule") or "").strip() != str(schedule).strip():
            existing["schedule"] = schedule
            updated = True
        if str(existing.get("timezone") or "").strip() != timezone_name:
            existing["timezone"] = timezone_name
            updated = True
        if int(existing.get("timeout_s") or 0) != expected_timeout:
            existing["timeout_s"] = expected_timeout
            updated = True
        if not bool(existing.get("enabled", True)):
            existing["enabled"] = True
            updated = True
        if existing.get("metadata") != expected_metadata:
            existing["metadata"] = expected_metadata
            updated = True
        if "notify" in existing:
            existing.pop("notify", None)
            updated = True
        if updated:
            _upsert_cron_job(existing, workspace)
            logger.info("Reconciled solo report cron job %s", job_name)
        else:
            logger.debug("solo report cron job already exists: %s", job_name)
        return

    job: dict[str, Any] = {
        "name": job_name,
        "schedule": schedule,
        "timezone": timezone_name,
        "command": expected_command,
        "cwd": expected_cwd,
        "enabled": True,
        "timeout_s": expected_timeout,
        "metadata": expected_metadata,
    }
    _upsert_cron_job(job, workspace)
    logger.info(
        "Registered solo report cron job: %s (schedule=%s tz=%s)",
        job_name,
        schedule,
        timezone_name,
    )


def ensure_report_jobs(
    app: str,
    *,
    workspace: str | Path | None = None,
    timezone_name: str = _DEFAULT_TIMEZONE,
    weekly_schedule: str = _WEEKLY_REPORT_SCHEDULE,
    monthly_schedule: str = _MONTHLY_REPORT_SCHEDULE,
    timeout_s: int = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Register or update recurring weekly/monthly report cron jobs."""
    _ensure_report_job(
        app,
        report_type="weekly",
        workspace=workspace,
        schedule=weekly_schedule,
        timezone_name=timezone_name,
        timeout_s=timeout_s,
    )
    _ensure_report_job(
        app,
        report_type="monthly",
        workspace=workspace,
        schedule=monthly_schedule,
        timezone_name=timezone_name,
        timeout_s=timeout_s,
    )
