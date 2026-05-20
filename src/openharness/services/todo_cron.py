"""Auto-register todo reminder cron jobs for solo/wolo apps."""

from __future__ import annotations

import sys
from pathlib import Path

from openharness.services.cron import get_cron_job, upsert_cron_job
from openharness.utils.log import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def ensure_todo_reminder_job(
    app: str,
    *,
    workspace: str | Path | None = None,
    notify: dict[str, str] | None = None,
    schedule: str = "0 9 * * *",
    timezone: str = "Asia/Shanghai",
) -> None:
    """Register a cron job that checks todos and sends reminders.

    Idempotent: if the job already exists, it won't be recreated.

    Args:
        app: "solo" or "wolo"
        workspace: workspace path for store access
        notify: optional notification config (e.g. {"type": "feishu_dm", "user_open_id": "..."})
        schedule: cron expression (default: daily at 9:00 AM)
        timezone: IANA timezone for the schedule
    """
    job_name = f"{app}-todo-reminder"
    existing = get_cron_job(job_name)
    if existing is not None:
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

    upsert_cron_job(job)
    logger.info("Registered todo reminder cron job: %s (schedule=%s tz=%s)", job_name, schedule, timezone)
