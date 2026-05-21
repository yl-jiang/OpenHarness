"""Background cron scheduler daemon for the wolo app.

Reads jobs from the wolo workspace cron registry (not openharness's shared one).
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openharness.sandbox import SandboxUnavailableError
from openharness.services.cron import next_run_time, validate_cron_expression
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text
from openharness.utils.log import configure_logging, get_logger
from openharness.utils.shell import create_shell_subprocess

logger = get_logger(__name__)

NOTIFICATION_OUTPUT_LIMIT = 3500
TICK_INTERVAL_SECONDS = 30

# Set once before fork so the child process inherits the workspace path.
_WORKSPACE: str | Path | None = None


# ---------------------------------------------------------------------------
# Path helpers (all workspace-aware)
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    from wolo.workspace import get_data_dir
    return get_data_dir(_WORKSPACE)


def _logs_dir() -> Path:
    from wolo.workspace import get_logs_dir
    return get_logs_dir(_WORKSPACE)


def _cron_registry_path() -> Path:
    return _data_dir() / "cron_jobs.json"


def _get_pid_path() -> Path:
    return _data_dir() / "cron_scheduler.pid"


def _get_history_path() -> Path:
    return _data_dir() / "cron_history.jsonl"


# ---------------------------------------------------------------------------
# Cron registry I/O
# ---------------------------------------------------------------------------

def _load_jobs() -> list[dict[str, Any]]:
    path = _cron_registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _save_jobs(jobs: list[dict[str, Any]]) -> None:
    path = _cron_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(jobs, indent=2) + "\n")


def _mark_job_run(name: str, *, success: bool) -> None:
    now = datetime.now(timezone.utc)
    lock = _cron_registry_path().with_suffix(".json.lock")
    with exclusive_file_lock(lock):
        jobs = _load_jobs()
        for job in jobs:
            if job.get("name") == name:
                job["last_run"] = now.isoformat()
                job["last_status"] = "success" if success else "failed"
                schedule = job.get("schedule", "")
                if validate_cron_expression(schedule):
                    job["next_run"] = next_run_time(
                        schedule, now, tz=job.get("timezone") or job.get("tz")
                    ).isoformat()
                _save_jobs(jobs)
                return


def _is_one_shot_job(job: dict[str, Any]) -> bool:
    if str(job.get("kind") or "").strip().lower() == "one_shot":
        return True
    payload = job.get("payload")
    return isinstance(payload, dict) and str(payload.get("kind") or "").strip().lower() == "reminder"


def _parse_next_run(job: dict[str, Any]) -> datetime | None:
    next_run_str = job.get("next_run")
    if not next_run_str:
        return None
    try:
        next_run = datetime.fromisoformat(str(next_run_str))
    except (TypeError, ValueError):
        return None
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=timezone.utc)
    return next_run


def _inline_output_for_job(job: dict[str, Any]) -> str | None:
    payload = job.get("payload")
    if not isinstance(payload, dict) or str(payload.get("kind") or "").strip().lower() != "reminder":
        return None
    reminder_text = str(payload.get("notification_text") or payload.get("message") or "").strip()
    if not reminder_text:
        raise ValueError("reminder cron job is missing payload.message")
    return reminder_text if reminder_text.startswith("⏰") else f"⏰ 提醒：{reminder_text}"


def _finalize_job_run(job: dict[str, Any], *, success: bool) -> None:
    if _is_one_shot_job(job):
        from wolo.gateway.todo_cron import delete_cron_job

        delete_cron_job(str(job.get("name") or ""), _WORKSPACE)
        return
    _mark_job_run(str(job.get("name") or ""), success=success)


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def _append_history(entry: dict[str, Any]) -> None:
    path = _get_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# PID file helpers
# ---------------------------------------------------------------------------

def _read_pid() -> int | None:
    path = _get_pid_path()
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        logger.debug("Removed stale scheduler PID file (pid=%d)", pid)
        path.unlink(missing_ok=True)
        return None
    return pid


def _write_pid() -> None:
    path = _get_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()) + "\n", encoding="utf-8")


def _remove_pid() -> None:
    _get_pid_path().unlink(missing_ok=True)


def is_scheduler_running() -> bool:
    return _read_pid() is not None


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _format_notification(job: dict[str, Any], entry: dict[str, Any]) -> str:
    status = entry.get("status", "?")
    rc = entry.get("returncode", "?")
    lines = [
        f"Cron job finished: {job.get('name', '?')}",
        f"Status: {status} (rc={rc})",
        f"Started: {entry.get('started_at', '?')}",
        f"Ended: {entry.get('ended_at', '?')}",
    ]
    stdout = str(entry.get("stdout") or "").strip()
    stderr = str(entry.get("stderr") or "").strip()
    if stdout:
        lines.extend(["", "Output:", stdout[-NOTIFICATION_OUTPUT_LIMIT:]])
    if stderr:
        lines.extend(["", "Stderr:", stderr[-NOTIFICATION_OUTPUT_LIMIT:]])
    if not stdout and not stderr:
        lines.extend(["", "(no output)"])
    return "\n".join(lines)


async def _agent_reformat(raw_output: str, job_name: str) -> str:
    """Use the wolo agent to rewrite raw cron output into a friendly notification."""
    try:
        from wolo.runner import WoloQueryRunner
        from wolo.store import WoloStore

        store = WoloStore(_WORKSPACE)
        runner = WoloQueryRunner(store)
        prompt = (
            f"以下是定时任务「{job_name}」的执行结果，请用简洁友好的中文重新整理成一条通知消息发给用户。"
            f"保留关键信息（待办标题、截止日期、优先级等），去掉技术细节。"
            f"如果没有需要提醒的待办，就说一句「暂无需要提醒的待办事项」。\n\n"
            f"---\n{raw_output}\n---"
        )
        return await runner.run(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent reformat failed for job %r, using raw output: %s", job_name, exc)
        return raw_output


async def _send_feishu_dm(*, user_open_id: str, content: str, workspace: str | Path | None = None) -> None:
    """Send a Feishu DM using this app's own feishu channel config."""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    from wolo.config import load_config

    config = load_config(workspace)
    feishu_config: dict[str, Any] = config.channel_configs.get("feishu", {})
    app_id = str(feishu_config.get("app_id") or "").strip()
    app_secret = str(feishu_config.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise ValueError("Feishu app_id/app_secret not configured in wolo config")

    def _send_sync() -> None:
        client = lark.Client.builder().app_id(app_id).app_secret(app_secret).log_level(lark.LogLevel.INFO).build()
        # Split long messages into chunks
        remaining = content.strip()
        while remaining:
            chunk = remaining[:1800]
            if len(remaining) > 1800:
                split_at = remaining.rfind("\n", 0, 1800)
                if split_at > 900:
                    chunk = remaining[:split_at]
            remaining = remaining[len(chunk):].strip()
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(user_open_id)
                    .msg_type("text")
                    .content(json.dumps({"text": chunk}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            response = client.im.v1.message.create(request)
            if not response.success():
                raise ValueError(
                    f"Feishu DM failed: code={response.code}, msg={response.msg}"
                )

    await asyncio.to_thread(_send_sync)
    logger.info("Sent Feishu DM to open_id=%s via wolo config", user_open_id)


async def _notify_job_result(job: dict[str, Any], entry: dict[str, Any]) -> None:
    notify = job.get("notify")
    payload = job.get("payload")
    if not isinstance(notify, dict) and isinstance(payload, dict) and payload.get("deliver"):
        notify = {"type": payload.get("channel"), "to": payload.get("to")}
    if not isinstance(notify, dict):
        return

    # On success with output, let the agent reformat into a user-friendly message
    is_success = entry.get("status") == "success"
    raw_stdout = str(entry.get("stdout") or "").strip()
    payload_kind = str(payload.get("kind") or "").strip().lower() if isinstance(payload, dict) else ""
    if payload_kind == "reminder" and raw_stdout:
        content = raw_stdout
    elif is_success and raw_stdout:
        content = await _agent_reformat(raw_stdout, str(job.get("name", "?")))
    else:
        content = _format_notification(job, entry)

    notify_type = str(notify.get("type") or "").strip().lower()
    try:
        if notify_type in {"feishu_dm", "feishu"}:
            user_open_id = str(
                notify.get("user_open_id") or notify.get("open_id") or notify.get("to") or ""
            ).strip()
            if not user_open_id:
                raise ValueError("missing notify.user_open_id")
            workspace = notify.get("workspace")
            await _send_feishu_dm(
                user_open_id=user_open_id,
                content=content,
                workspace=str(workspace) if workspace else None,
            )
        elif notify_type:
            raise ValueError(f"unsupported notify.type: {notify_type}")
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to notify cron job %r result: %s", job.get("name"), exc)
        entry["notification_status"] = "failed"
        entry["notification_error"] = str(exc)
    else:
        entry["notification_status"] = "sent"


def _command_for_job(job: dict[str, Any]) -> str:
    command = job.get("command")
    if command:
        return str(command)

    payload = job.get("payload")
    if not isinstance(payload, dict) or payload.get("kind", "agent_turn") != "agent_turn":
        raise ValueError("cron job has no command or agent_turn payload")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("agent_turn cron job is missing payload.message")

    cwd = str(job.get("cwd") or ".")
    parts = ["ohmo"]
    profile = payload.get("profile") or job.get("provider_profile")
    if profile is None:
        from ohmo.gateway.config import load_gateway_config
        profile = load_gateway_config().provider_profile
    if profile:
        parts.extend(["--profile", str(profile)])
    parts.extend(["--cwd", cwd, "--print", message])
    return " ".join(shlex.quote(part) for part in parts)


async def execute_job(job: dict[str, Any]) -> dict[str, Any]:
    name = job["name"]
    cwd = Path(job.get("cwd") or ".").expanduser()
    started_at = datetime.now(timezone.utc)
    try:
        inline_output = _inline_output_for_job(job)
    except Exception as exc:  # noqa: BLE001
        entry = {
            "name": name,
            "command": "(reminder)",
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": -1,
            "status": "error",
            "stdout": "",
            "stderr": str(exc),
        }
        _finalize_job_run(job, success=False)
        await _notify_job_result(job, entry)
        _append_history(entry)
        return entry
    if inline_output is not None:
        entry = {
            "name": name,
            "command": "(reminder)",
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": 0,
            "status": "success",
            "stdout": inline_output,
            "stderr": "",
        }
        _finalize_job_run(job, success=True)
        await _notify_job_result(job, entry)
        _append_history(entry)
        logger.info("Job %r finished inline reminder dispatch", name)
        return entry
    try:
        command = _command_for_job(job)
    except Exception as exc:  # noqa: BLE001
        entry = {
            "name": name, "command": "",
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": -1, "status": "error", "stdout": "", "stderr": str(exc),
        }
        _finalize_job_run(job, success=False)
        await _notify_job_result(job, entry)
        _append_history(entry)
        return entry

    logger.info("Executing cron job %r: %s", name, command)
    try:
        process = await create_shell_subprocess(
            command, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
    except asyncio.TimeoutError:
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        entry = {
            "name": name, "command": command,
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": -1, "status": "timeout", "stdout": "", "stderr": "Job timed out after 300s",
        }
        _finalize_job_run(job, success=False)
        await _notify_job_result(job, entry)
        _append_history(entry)
        return entry
    except SandboxUnavailableError as exc:
        entry = {
            "name": name, "command": command,
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": -1, "status": "error", "stdout": "", "stderr": str(exc),
        }
        _finalize_job_run(job, success=False)
        await _notify_job_result(job, entry)
        _append_history(entry)
        return entry
    except Exception as exc:  # noqa: BLE001
        entry = {
            "name": name, "command": command,
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": -1, "status": "error", "stdout": "", "stderr": str(exc),
        }
        _finalize_job_run(job, success=False)
        await _notify_job_result(job, entry)
        _append_history(entry)
        return entry

    success = process.returncode == 0
    entry = {
        "name": name, "command": command,
        "started_at": started_at.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "returncode": process.returncode,
        "status": "success" if success else "failed",
        "stdout": (stdout.decode("utf-8", errors="replace")[-2000:] if stdout else ""),
        "stderr": (stderr.decode("utf-8", errors="replace")[-2000:] if stderr else ""),
    }
    _finalize_job_run(job, success=success)
    await _notify_job_result(job, entry)
    _append_history(entry)
    logger.info("Job %r finished: %s (rc=%s)", name, entry["status"], process.returncode)
    return entry


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------

def _jobs_due(jobs: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    due = []
    for job in jobs:
        if not job.get("enabled", True):
            continue
        next_run = _parse_next_run(job)
        if next_run is None:
            continue
        if not _is_one_shot_job(job):
            schedule = job.get("schedule", "")
            if not validate_cron_expression(schedule):
                continue
        if next_run <= now:
            due.append(job)
    return due


async def run_scheduler_loop(*, once: bool = False) -> None:
    shutdown = asyncio.Event()

    def _on_signal() -> None:
        logger.info("Received shutdown signal")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_signal)

    _write_pid()
    logger.info("Wolo cron scheduler started (pid=%d, tick=%ds)", os.getpid(), TICK_INTERVAL_SECONDS)

    try:
        while not shutdown.is_set():
            now = datetime.now(timezone.utc)
            jobs = _load_jobs()
            due = _jobs_due(jobs, now)
            if due:
                logger.info("Tick: %d job(s) due", len(due))
                results = await asyncio.gather(
                    *(execute_job(job) for job in due), return_exceptions=True
                )
                for result in results:
                    if isinstance(result, BaseException):
                        logger.error("Unexpected error executing cron job: %s", result)
            if once:
                break
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=TICK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        _remove_pid()
        logger.info("Wolo cron scheduler stopped")


# ---------------------------------------------------------------------------
# Daemon entry point
# ---------------------------------------------------------------------------

def _run_daemon() -> None:
    log_file = _logs_dir() / "cron_scheduler.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    configure_logging(level="INFO", log_file=log_file)
    asyncio.run(run_scheduler_loop())


def start_daemon(workspace: str | Path | None = None) -> int:
    """Fork and start the scheduler daemon. Returns the child PID."""
    global _WORKSPACE
    _WORKSPACE = workspace

    existing = _read_pid()
    if existing is not None:
        raise RuntimeError(f"Wolo cron scheduler already running (pid={existing})")

    pid = os.fork()
    if pid > 0:
        time.sleep(0.3)
        return pid

    # Child — detach
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)

    _run_daemon()
    sys.exit(0)
