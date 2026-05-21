"""Auto-register todo reminder cron jobs for the solo app."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.services.cron import next_run_time, validate_cron_expression
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text
from openharness.utils.log import get_logger
from solo.workspace import get_data_dir

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
    except json.JSONDecodeError:
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
        jobs = [j for j in _load(workspace) if j.get("name") != job.get("name")]
        jobs.append(job)
        jobs.sort(key=lambda item: str(item.get("name", "")))
        _save(workspace, jobs)


def delete_cron_job(name: str, workspace: str | Path | None = None) -> bool:
    """Delete one cron job by name from the app-local registry."""

    lock = _cron_registry_path(workspace).with_suffix(".json.lock")
    with exclusive_file_lock(lock):
        jobs = _load(workspace)
        filtered = [job for job in jobs if job.get("name") != name]
        if len(filtered) == len(jobs):
            return False
        _save(workspace, filtered)
    return True


def schedule_one_shot_reminder(
    app: str,
    *,
    workspace: str | Path | None = None,
    remind_at: datetime,
    message: str,
    notify: dict[str, str],
) -> dict[str, Any]:
    """Persist a one-shot reminder job for the app-local scheduler."""

    reminder_text = str(message).strip()
    if not reminder_text:
        raise ValueError("message is required for reminder jobs")

    due_at = remind_at if remind_at.tzinfo is not None else remind_at.replace(tzinfo=timezone.utc)
    job = {
        "name": f"{app}-reminder-{uuid4().hex[:12]}",
        "kind": "one_shot",
        "enabled": True,
        "next_run": due_at.astimezone(timezone.utc).isoformat(),
        "notify": notify,
        "payload": {
            "kind": "reminder",
            "message": reminder_text,
            "notification_text": f"⏰ 提醒：{reminder_text}",
        },
    }
    _upsert_cron_job(job, workspace)
    logger.info("Registered one-shot reminder job: %s next_run=%s", job["name"], job["next_run"])
    return job


def ensure_todo_reminder_job(
    app: str,
    *,
    workspace: str | Path | None = None,
    notify: dict[str, str] | None = None,
    schedule: str = "0 9 * * *",
    timezone: str = "Asia/Shanghai",
) -> None:
    """Register or update the cron job that checks todos and sends reminders.

    If the job already exists but is missing a notify config and one is now
    available, the job will be updated with the new notify target.

    Args:
        app: app name (e.g. "solo")
        workspace: workspace path for store access
        notify: optional notification config (e.g. {"type": "feishu_dm", "user_open_id": "..."})
        schedule: cron expression (default: daily at 9:00 AM)
        timezone: IANA timezone for the schedule
    """
    job_name = f"{app}-todo-reminder"
    existing = _get_cron_job(job_name, workspace)
    if existing is not None:
        # Update notify target if we now have one and the existing job doesn't
        if notify and not existing.get("notify"):
            existing["notify"] = notify
            _upsert_cron_job(existing, workspace)
            logger.info("Updated todo reminder cron job %s with notify target", job_name)
        else:
            logger.debug("todo reminder cron job already exists: %s", job_name)
        return

    python = sys.executable
    script = str(_REPO_ROOT / "scripts" / "todo_reminder.py")
    command = f"{python} {script} --app {app}"
    if workspace:
        command += f" --workspace {workspace}"

    job: dict[str, object] = {
        "name": job_name,
        "schedule": schedule,
        "timezone": timezone,
        "command": command,
        "cwd": str(_REPO_ROOT),
        "enabled": True,
    }
    if notify:
        job["notify"] = notify

    _upsert_cron_job(job, workspace)
    logger.info("Registered todo reminder cron job: %s (schedule=%s tz=%s)", job_name, schedule, timezone)
