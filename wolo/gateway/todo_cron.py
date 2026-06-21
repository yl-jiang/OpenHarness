"""Auto-register todo reminder cron jobs for the wolo app."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.services.cron import next_run_time, validate_cron_expression
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text
from openharness.utils.log import get_logger
from wolo.core.workspace import get_data_dir

logger = get_logger(__name__)


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


def list_one_shot_jobs(workspace: str | Path | None = None) -> list[dict[str, Any]]:
    """Return all pending one-shot jobs (reminder + agent_task) sorted by next_run."""
    return sorted(
        [j for j in _load(workspace) if j.get("kind") == "one_shot" and j.get("enabled", True)],
        key=lambda j: str(j.get("next_run") or ""),
    )


def schedule_one_shot_reminder(
    app: str,
    *,
    workspace: str | Path | None = None,
    remind_at: datetime,
    message: str,
    notify: dict[str, str],
    session_key: str = "",
) -> dict[str, Any]:
    """Persist a one-shot reminder job for the app-local scheduler."""

    reminder_text = str(message).strip()
    if not reminder_text:
        raise ValueError("message is required for reminder jobs")

    due_at = remind_at if remind_at.tzinfo is not None else remind_at.replace(tzinfo=timezone.utc)
    payload: dict[str, Any] = {
        "kind": "reminder",
        "message": reminder_text,
        "notification_text": f"⏰ 提醒：{reminder_text}",
    }
    if session_key:
        payload["session_key"] = session_key
    job = {
        "name": f"{app}-reminder-{uuid4().hex[:12]}",
        "kind": "one_shot",
        "enabled": True,
        "next_run": due_at.astimezone(timezone.utc).isoformat(),
        "notify": notify,
        "payload": payload,
    }
    _upsert_cron_job(job, workspace)
    logger.info("Registered one-shot reminder job: %s next_run=%s", job["name"], job["next_run"])
    return job


def schedule_one_shot_agent_task(
    app: str,
    *,
    workspace: str | Path | None = None,
    run_at: datetime,
    prompt: str,
    notify: dict[str, str],
) -> dict[str, Any]:
    """Persist a one-shot agent-task job for the app-local scheduler.

    At `run_at` the scheduler will invoke the app's agent with `prompt`,
    then DM the resulting output to the user.
    """

    task_prompt = str(prompt).strip()
    if not task_prompt:
        raise ValueError("prompt is required for agent_task jobs")

    due_at = run_at if run_at.tzinfo is not None else run_at.replace(tzinfo=timezone.utc)
    job = {
        "name": f"{app}-task-{uuid4().hex[:12]}",
        "kind": "one_shot",
        "enabled": True,
        "next_run": due_at.astimezone(timezone.utc).isoformat(),
        "notify": notify,
        "payload": {
            "kind": "agent_task",
            "message": task_prompt,
        },
    }
    _upsert_cron_job(job, workspace)
    logger.info("Registered one-shot agent_task job: %s next_run=%s", job["name"], job["next_run"])
    return job
