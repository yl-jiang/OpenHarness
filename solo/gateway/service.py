"""Gateway service lifecycle for the standalone solo app."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes

from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.manager import ChannelManager
from openharness.utils.log import get_logger

from solo.config import build_channel_manager_config, load_config
from solo.gateway.bridge import SoloGatewayBridge
from solo.gateway.heartbeat import SoloHeartbeatService
from solo.core.models import SoloState
from solo.core.workspace import (
    get_logs_dir,
    get_pid_path,
    get_state_path,
    get_workspace_root,
    initialize_workspace,
)

logger = get_logger(__name__)
_REPO_ROOT = Path(__file__).resolve().parents[2]


class SoloGatewayService:
    """Foreground/background service wrapper for solo channels."""

    def __init__(
        self,
        cwd: str | Path | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        self._cwd = str(Path(cwd or Path.cwd()).resolve())
        self._workspace = workspace
        os.chdir(self._cwd)
        root = initialize_workspace(self._workspace)
        os.environ["SOLO_WORKSPACE"] = str(root)
        self._config = load_config(root)
        self._bus = MessageBus()
        self._manager = ChannelManager(build_channel_manager_config(self._config), self._bus)
        self._bridge = SoloGatewayBridge(
            bus=self._bus,
            workspace=root,
            provider_profile=self._config.provider_profile,
        )
        self._heartbeat = SoloHeartbeatService(
            bus=self._bus,
            workspace=root,
            provider_profile=self._config.provider_profile,
            enabled_channels=self._config.enabled_channels,
            interval_s=self._config.heartbeat.interval_s,
            enabled=self._config.heartbeat.enabled,
            keep_recent_messages=self._config.heartbeat.keep_recent_messages,
            quiet_hours_start=self._config.heartbeat.quiet_hours_start,
            quiet_hours_end=self._config.heartbeat.quiet_hours_end,
            timezone_name=self._config.heartbeat.timezone,
            max_daily_pushes=self._config.heartbeat.max_daily_pushes,
        )

    @property
    def pid_file(self) -> Path:
        return get_pid_path(self._workspace)

    @property
    def log_file(self) -> Path:
        return get_logs_dir(self._workspace) / "gateway.log"

    @property
    def state_file(self) -> Path:
        return get_state_path(self._workspace)

    def write_state(self, *, running: bool, last_error: str | None = None) -> None:
        state = SoloState(
            running=running,
            pid=os.getpid() if running else None,
            provider_profile=self._config.provider_profile,
            enabled_channels=self._config.enabled_channels,
            last_error=last_error,
        )
        self.state_file.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")

    async def run_foreground(self) -> int:
        existing_pid = _check_pid_file(self.pid_file)
        if existing_pid is not None:
            logger.error(
                "solo gateway already running pid=%d workspace=%s",
                existing_pid,
                self._workspace,
            )
            print(f"Error: solo gateway is already running (pid {existing_pid})")
            return 1

        self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        self.write_state(running=True)
        channels = self._config.enabled_channels or []
        _print_gateway_banner(
            pid=os.getpid(),
            workspace=self._workspace or get_workspace_root(),
            profile=self._config.provider_profile,
            channels=channels,
            log_file=self.log_file,
        )
        logger.info(
            "solo gateway starting pid=%d channels=%s profile=%s workspace=%s",
            os.getpid(),
            channels,
            self._config.provider_profile,
            self._workspace,
        )

        # One-shot migration: retire the legacy todo-reminder cron job.
        # The heartbeat watchdog now owns the todo/due-today signal, so keeping
        # the cron would just produce permanent notify failures that poison
        # the heartbeat fingerprint.
        _migrate_legacy_todo_cron(self._workspace)
        # Auto-register feed digest cron job
        _register_feed_digest_cron(self._workspace, self._config)

        bridge_task = asyncio.create_task(self._bridge.run(), name="solo-gateway-bridge")
        manager_task = asyncio.create_task(self._manager.start_all(), name="solo-gateway-channels")
        await self._heartbeat.start()
        stop_event = asyncio.Event()

        def _stop(*_: object) -> None:
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _stop)

        # Yield once so channel tasks can start and emit their first logs before "ready"
        await asyncio.sleep(0)
        logger.info("solo gateway ready — waiting for messages (Ctrl-C to stop)")

        try:
            await stop_event.wait()
        finally:
            logger.info("solo gateway shutting down pid=%d", os.getpid())
            self._bridge.stop()
            bridge_task.cancel()
            manager_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bridge_task
            with contextlib.suppress(asyncio.CancelledError):
                await manager_task
            await self._manager.stop_all()
            await self._heartbeat.stop()
            self.write_state(running=False)
            self.pid_file.unlink(missing_ok=True)
            logger.info("solo gateway stopped pid=%d", os.getpid())
        return 0


def start_gateway_process(cwd: str | Path | None = None, workspace: str | Path | None = None) -> int:
    service = SoloGatewayService(cwd, workspace)
    service.log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info("start_gateway_process log_file=%s workspace=%s", service.log_file, service._workspace)
    env = os.environ.copy()
    pythonpath_entries = [str(_REPO_ROOT)]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    with service.log_file.open("a", encoding="utf-8") as log_file:
        popen_kwargs: dict[str, object] = {
            "cwd": service._cwd,
            "stdout": log_file,
            "stderr": log_file,
            "env": env,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
            popen_kwargs["stdin"] = subprocess.DEVNULL
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "solo",
                "gateway",
                "run",
                "--cwd",
                service._cwd,
                "--workspace",
                str(get_workspace_root(workspace)),
            ],
            **popen_kwargs,
        )
    logger.info("start_gateway_process launched pid=%d", process.pid)
    return process.pid


def stop_gateway_process(cwd: str | Path | None = None, workspace: str | Path | None = None) -> bool:
    service = SoloGatewayService(cwd, workspace)
    pids: list[int] = []
    if service.pid_file.exists():
        with contextlib.suppress(ValueError):
            pids.append(int(service.pid_file.read_text(encoding="utf-8").strip()))
    pids.extend(_iter_workspace_gateway_pids(workspace))
    unique = [pid for index, pid in enumerate(pids) if pid not in pids[:index] and _pid_is_running(pid)]
    if not unique:
        service.pid_file.unlink(missing_ok=True)
        logger.info("stop_gateway_process no running gateway found")
        return False
    logger.info("stop_gateway_process sending SIGTERM to pids=%s", unique)
    if sys.platform == "win32":
        for pid in unique:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        for pid in unique:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
    service.pid_file.unlink(missing_ok=True)
    service.write_state(running=False)

    # The cron scheduler daemon is forked independently and survives a gateway
    # stop. Stop it too so a subsequent start does not inherit a daemon
    # running stale bytecode.
    try:
        from solo.gateway.cron_scheduler import stop_daemon as _stop_cron_daemon
        _stop_cron_daemon(workspace)
    except Exception as exc:
        logger.warning("Failed to stop cron scheduler daemon: %s", exc)

    return True


def gateway_status(cwd: str | Path | None = None, workspace: str | Path | None = None) -> SoloState:
    service = SoloGatewayService(cwd, workspace)
    live_pid: int | None = None
    if service.pid_file.exists():
        with contextlib.suppress(ValueError):
            pid = int(service.pid_file.read_text(encoding="utf-8").strip())
            if _pid_is_running(pid):
                live_pid = pid
    if live_pid is None:
        live_pids = _iter_workspace_gateway_pids(workspace)
        if live_pids:
            live_pid = live_pids[0]
            service.pid_file.write_text(str(live_pid), encoding="utf-8")
        else:
            service.pid_file.unlink(missing_ok=True)
    last_error: str | None = None
    if service.state_file.exists():
        with contextlib.suppress(Exception):
            last_error = SoloState.model_validate_json(
                service.state_file.read_text(encoding="utf-8")
            ).last_error
    return SoloState(
        running=live_pid is not None,
        pid=live_pid,
        provider_profile=service._config.provider_profile,
        enabled_channels=service._config.enabled_channels,
        last_error=last_error,
    )


def _pid_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == 259
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _check_pid_file(pid_file: Path) -> int | None:
    """Check whether *pid_file* points to a live process.

    Returns the existing PID if the process is still running,
    otherwise removes the stale file and returns ``None``.
    """
    if not pid_file.exists():
        return None
    with contextlib.suppress(ValueError):
        existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
        if _pid_is_running(existing_pid):
            return existing_pid
    pid_file.unlink(missing_ok=True)
    return None


def _iter_workspace_gateway_pids(workspace: str | Path | None = None) -> list[int]:
    get_workspace_root(workspace)  # ensure workspace exists & is initialized
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return []
    current_pid = os.getpid()
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_text, args = line.split(None, 1)
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == current_pid:
            continue
        if "solo" not in args or "gateway run" not in args:
            continue
        if _pid_is_running(pid):
            pids.append(pid)
    return pids


def _print_gateway_banner(
    *,
    pid: int,
    workspace: object,
    profile: str,
    channels: list[str],
    log_file: Path,
) -> None:
    """Print a human-readable startup banner to stdout."""
    channel_str = ", ".join(channels) if channels else "(none)"
    print(
        f"\n"
        f"  ╔══════════════════════════════════════════╗\n"
        f"  ║          solo gateway starting           ║\n"
        f"  ╚══════════════════════════════════════════╝\n"
        f"  pid       : {pid}\n"
        f"  workspace : {workspace}\n"
        f"  profile   : {profile}\n"
        f"  channels  : {channel_str}\n"
        f"  log file  : {log_file}\n",
        flush=True,
    )


def _resolve_feishu_notify_target(
    config: object,
    workspace: str | Path | None,
) -> dict[str, str] | None:
    """Resolve feishu notification target from config or conversation history."""
    channel_configs = getattr(config, "channel_configs", {}) or {}
    feishu_config = channel_configs.get("feishu", {})
    user_open_id = (
        feishu_config.get("owner_open_id")
        or feishu_config.get("user_open_id")
    )

    # Fall back to the most recent feishu conversation partner
    if not user_open_id:
        try:
            from solo.core.session import list_conversations

            root = get_workspace_root(workspace)
            for item in list_conversations(root, limit=20):
                key = str(item.get("session_key") or "")
                if ":" not in key:
                    continue
                channel, chat_id = key.split(":", 1)
                if channel == "feishu" and chat_id:
                    user_open_id = chat_id
                    break
        except Exception:
            pass

    if not user_open_id:
        return None
    return {
        "type": "feishu_dm",
        "user_open_id": str(user_open_id),
        "workspace": str(get_workspace_root(workspace)),
    }


def _register_feed_digest_cron(
    workspace: str | Path | None,
    config: object,
) -> None:
    """Best-effort registration of the solo feed digest cron job."""
    fd_config = getattr(config, "feed_digest", None)
    if fd_config is None or not getattr(fd_config, "enabled", False):
        logger.debug("Solo feed digest disabled; skipping cron registration")
        return
    try:
        from solo.gateway.feed_digest_cron import ensure_feed_digest_job

        notify = (
            _resolve_feishu_notify_target(config, workspace)
            if getattr(fd_config, "im_push_enabled", True)
            else None
        )
        ensure_feed_digest_job(
            "solo",
            workspace=workspace,
            notify=notify,
            schedule=fd_config.schedule,
            tz=fd_config.timezone,
            im_push_enabled=getattr(fd_config, "im_push_enabled", True),
        )
    except Exception as exc:
        logger.warning("Failed to register solo feed digest cron job: %s", exc)


def _migrate_legacy_todo_cron(workspace: str | Path | None) -> None:
    """Retire the legacy `solo-todo-reminder` cron job and stale one-shot reminders.

    The heartbeat watchdog now serves the overdue / due-today todo signal with
    proper quiet-hours / daily-cap / per-signal-ack safeguards. Keeping the old
    cron would only produce permanent notify failures (broken feishu config in
    older deployments) that pollute cron_history and poison the heartbeat
    signal fingerprint.

    Idempotent: safe to invoke on every gateway startup.
    """
    try:
        from solo.gateway.todo_cron import delete_cron_job, list_one_shot_jobs
        from datetime import datetime, timezone

        if delete_cron_job("solo-todo-reminder", workspace):
            logger.info("Migrated: removed legacy solo-todo-reminder cron job")

        now_utc = datetime.now(timezone.utc)
        for job in list_one_shot_jobs(workspace):
            payload = job.get("payload") or {}
            if str(payload.get("kind") or "") != "reminder":
                continue
            next_run_str = str(job.get("next_run") or "")
            try:
                next_run = datetime.fromisoformat(next_run_str)
            except (TypeError, ValueError):
                continue
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=timezone.utc)
            if next_run < now_utc:
                delete_cron_job(str(job.get("name") or ""), workspace)
                logger.info(
                    "Migrated: removed past-due one-shot reminder %s (was due %s)",
                    job.get("name"),
                    next_run_str,
                )
    except Exception as exc:
        logger.warning("Legacy todo-cron migration failed: %s", exc)
