"""Auto-register feed digest cron jobs for the wolo app."""
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
from wolo.core.workspace import get_data_dir

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = Path(__file__).resolve().parents[1]


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


def ensure_feed_digest_job(
    app: str,
    *,
    workspace: str | Path | None = None,
    notify: dict[str, str] | None = None,
    schedule: str = "30 21 * * *",
    tz: str = "Asia/Shanghai",
    im_push_enabled: bool = True,
) -> None:
    """Register or update the feed digest cron job for wolo."""
    job_name = f"{app}-feed-digest"
    existing = _get_cron_job(job_name, workspace)
    if existing is not None:
        python = sys.executable
        script = str(_APP_ROOT / "gateway" / "feed_digest_runner.py")
        expected_command = f"{python} {script} --app {app}"
        if workspace:
            expected_command += f" --workspace {workspace}"
        if not im_push_enabled:
            expected_command += " --no-push"

        changed = False
        if notify and not existing.get("notify"):
            existing["notify"] = notify
            changed = True
        if existing.get("schedule") != schedule:
            existing["schedule"] = schedule
            changed = True
        if existing.get("timezone") != tz:
            existing["timezone"] = tz
            changed = True
        if existing.get("command") != expected_command:
            existing["command"] = expected_command
            changed = True
        if changed:
            _upsert_cron_job(existing, workspace)
            logger.info("Updated wolo feed digest cron job %s", job_name)
        else:
            logger.debug("wolo feed digest cron job already exists: %s", job_name)
        return

    python = sys.executable
    script = str(_APP_ROOT / "gateway" / "feed_digest_runner.py")
    command = f"{python} {script} --app {app}"
    if workspace:
        command += f" --workspace {workspace}"
    if not im_push_enabled:
        command += " --no-push"

    job: dict[str, Any] = {
        "name": job_name,
        "schedule": schedule,
        "timezone": tz,
        "command": command,
        "cwd": str(_REPO_ROOT),
        "enabled": True,
        "metadata": {"app": app, "im_push_enabled": im_push_enabled},
    }
    if notify and im_push_enabled:
        job["notify"] = notify

    _upsert_cron_job(job, workspace)
    logger.info(
        "Registered wolo feed digest cron job: %s (schedule=%s tz=%s)",
        job_name,
        schedule,
        tz,
    )
