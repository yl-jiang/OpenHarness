"""Background task manager."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from openharness.config.paths import get_tasks_dir
from openharness.tasks.types import TaskRecord, TaskStatus, TaskType
from openharness.utils.log import get_logger
from openharness.utils.shell import create_shell_subprocess

logger = get_logger(__name__)
_TASK_RESTART_NOTICE = "[OpenHarness] Agent task restarted; prior interactive context was not preserved.\n"


def _encode_task_worker_payload(data: str) -> bytes:
    """Serialize one worker input as a single JSON line.

    Plain-text prompts may contain embedded newlines, so they cannot be written
    directly to a readline()-based worker protocol. We wrap them in a JSON
    object with a ``text`` field, while preserving already-structured payloads
    emitted by teammate backends.
    """

    stripped = data.rstrip("\n")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        framed = stripped
    elif "\n" not in stripped and "\r" not in stripped:
        framed = stripped
    else:
        framed = json.dumps({"text": stripped}, ensure_ascii=False)
    return (framed + "\n").encode("utf-8")


CompletionListener = Callable[[TaskRecord], Awaitable[None] | None]

# Known provider API key env var names to probe when ANTHROPIC_API_KEY is absent.
_OTHER_API_KEY_ENVS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "MOONSHOT_API_KEY",
    "GEMINI_API_KEY",
    "MINIMAX_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _resolve_api_key(explicit: str | None) -> str | None:
    """Return the best available API key, or None.

    Resolution order:
    1. Explicitly supplied *explicit* value.
    2. ``ANTHROPIC_API_KEY`` environment variable.
    3. Other known provider API key environment variables.
    4. Active profile credential from the config / credential store.
    """
    if explicit:
        return explicit
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        return key
    for env_var in _OTHER_API_KEY_ENVS:
        if key := os.environ.get(env_var):
            return key
    try:
        from openharness.auth.storage import load_credential
        from openharness.config import load_settings
        from openharness.config.settings import credential_storage_provider_name

        settings = load_settings()
        if settings.api_key:
            return settings.api_key
        profile_name, profile = settings.resolve_profile()
        if profile.api_key:
            return profile.api_key
        stored = load_credential(credential_storage_provider_name(profile_name, profile), "api_key")
        if stored:
            return stored
    except Exception:
        pass
    return None


class BackgroundTaskManager:
    """Manage shell and agent subprocess tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._waiters: dict[str, asyncio.Task[None]] = {}
        self._output_locks: dict[str, asyncio.Lock] = {}
        self._input_locks: dict[str, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}
        self._listeners: list[Callable[[TaskRecord, str, str], None]] = []
        self._completion_listeners: dict[str, CompletionListener] = {}

    def on_task_change(self, callback: Callable[[TaskRecord, str, str], None]) -> None:
        """Register a callback fired whenever a task status changes.

        The callback receives ``(task, old_status, new_status)``.
        """
        self._listeners.append(callback)

    def _set_task_status(self, task: TaskRecord, new_status: str) -> None:
        """Update task status and notify registered listeners."""
        old_status = task.status
        task.status = new_status
        logger.event(
            "task_manager_task_status_change",
            task_id=task.id,
            task_type=task.type,
            old_status=old_status,
            new_status=new_status,
            return_code=task.return_code,
            description=task.description,
        )
        for listener in self._listeners:
            try:
                listener(task, old_status, new_status)
            except Exception:  # noqa: BLE001 – never let a listener crash the manager
                pass

    async def create_shell_task(
        self,
        *,
        command: str | None = None,
        argv: list[str] | None = None,
        env: dict[str, str] | None = None,
        description: str,
        cwd: str | Path,
        task_type: TaskType = "local_bash",
    ) -> TaskRecord:
        """Start a background shell command."""
        if command is None and argv is None:
            raise ValueError("Shell task requires command or argv")
        task_id = _task_id(task_type)
        output_path = get_tasks_dir() / f"{task_id}.log"
        display_command = command if command is not None else shlex.join(argv or [])
        record = TaskRecord(
            id=task_id,
            type=task_type,
            status=TaskStatus.RUNNING,
            description=description,
            cwd=str(Path(cwd).resolve()),
            output_file=output_path,
            command=display_command,
            argv=list(argv) if argv is not None else None,
            env=dict(env) if env is not None else None,
            created_at=time.time(),
            started_at=time.time(),
        )
        output_path.write_text("", encoding="utf-8")
        self._tasks[task_id] = record
        self._output_locks[task_id] = asyncio.Lock()
        self._input_locks[task_id] = asyncio.Lock()
        await self._start_process(task_id)
        return record

    async def create_agent_task(
        self,
        *,
        prompt: str,
        description: str,
        cwd: str | Path,
        task_type: TaskType = "local_agent",
        model: str | None = None,
        api_key: str | None = None,
        command: str | None = None,
        env: dict[str, str] | None = None,
    ) -> TaskRecord:
        """Start a local agent task as a subprocess."""
        command_override_supplied = command is not None
        if command is None:
            effective_api_key = _resolve_api_key(api_key)
            if not effective_api_key:
                raise ValueError(
                    "Local agent tasks require an API key. Set ANTHROPIC_API_KEY (or another "
                    "provider key such as OPENAI_API_KEY) as an environment variable, configure "
                    "credentials via 'oh auth', or provide an explicit command override."
                )
            cmd = [sys.executable, "-m", "openharness", "--task-worker", "--api-key", effective_api_key]
            if model:
                cmd.extend(["--model", model])
            command = " ".join(shlex.quote(part) for part in cmd)

        record = await self.create_shell_task(
            command=command,
            description=description,
            cwd=cwd,
            task_type=task_type,
            env=env,
        )
        logger.event(
            "task_manager_create_agent_task",
            task_id=record.id,
            task_type=task_type,
            description=description,
            cwd=str(Path(cwd).resolve()),
            prompt_length=len(prompt),
            requested_model=model,
            has_command_override=command_override_supplied,
            command_head=(command.split(maxsplit=1)[0] if command else None),
            command_has_task_worker="--task-worker" in command if command else False,
        )
        updated = replace(record, prompt=prompt)
        if task_type != "local_agent":
            updated.metadata["agent_mode"] = task_type
        self._tasks[record.id] = updated
        await self.write_to_task(record.id, json.dumps({"text": prompt}))
        return updated

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Return one task record."""
        return self._tasks.get(task_id)

    def list_tasks(self, *, status: TaskStatus | None = None) -> list[TaskRecord]:
        """Return all tasks, optionally filtered by status."""
        tasks = list(self._tasks.values())
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return sorted(tasks, key=lambda item: item.created_at, reverse=True)

    async def wait_for_tasks(
        self,
        task_ids: list[str] | None = None,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> list[TaskRecord]:
        """Block until all specified tasks (or all running tasks) reach a terminal state.

        Returns the list of awaited task records after they finish.
        Raises ``asyncio.TimeoutError`` if the timeout is exceeded.
        """
        if task_ids is not None:
            for tid in task_ids:
                self._require_task(tid)
            targets = task_ids
        else:
            targets = [tid for tid, t in self._tasks.items() if t.status not in TaskStatus.terminal_states()]

        if not targets:
            return []

        async def _poll() -> list[TaskRecord]:
            while True:
                pending = [tid for tid in targets if self._tasks[tid].status not in TaskStatus.terminal_states()]
                if not pending:
                    break
                await asyncio.sleep(poll_interval)
            return [self._tasks[tid] for tid in targets]

        return await asyncio.wait_for(_poll(), timeout=timeout)

    def update_task(
        self,
        task_id: str,
        *,
        description: str | None = None,
        progress: int | None = None,
        status_note: str | None = None,
    ) -> TaskRecord:
        """Update mutable task metadata used for coordination and UI display."""
        task = self._require_task(task_id)
        if description is not None and description.strip():
            task.description = description.strip()
        if progress is not None:
            task.metadata["progress"] = str(progress)
        if status_note is not None:
            note = status_note.strip()
            if note:
                task.metadata["status_note"] = note
            else:
                task.metadata.pop("status_note", None)
        return task

    async def stop_task(self, task_id: str) -> TaskRecord:
        """Terminate a running task."""
        task = self._require_task(task_id)
        process = self._processes.get(task_id)
        if process is None:
            if task.status in TaskStatus.terminal_states():
                return task
            raise ValueError(f"Task {task_id} is not running")

        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        await _close_process_stdin(process)

        self._set_task_status(task, TaskStatus.KILLED)
        task.ended_at = time.time()
        logger.event(
            "task_manager_stop_task",
            task_id=task.id,
            task_type=task.type,
            return_code=task.return_code,
        )
        await self._notify_completion_listeners(task)
        return task

    async def write_to_task(self, task_id: str, data: str) -> None:
        """Write one line to task stdin, auto-resuming local agents when needed."""
        task = self._require_task(task_id)
        payload = _encode_task_worker_payload(data)
        async with self._input_locks[task_id]:
            process = await self._ensure_writable_process(task)
            process.stdin.write(payload)
            try:
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                if task.type not in {"local_agent", "remote_agent", "in_process_teammate"}:
                    raise ValueError(f"Task {task_id} does not accept input") from None
                process = await self._restart_agent_task(task)
                process.stdin.write(payload)
                await process.stdin.drain()

    def read_task_output(self, task_id: str, *, max_bytes: int = 12000) -> str:
        """Return the tail of a task's output file."""
        task = self._require_task(task_id)
        content = task.output_file.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_bytes:
            return content[-max_bytes:]
        return content

    def register_completion_listener(self, listener: CompletionListener) -> Callable[[], None]:
        """Register a callback fired whenever a task reaches a terminal state.

        Returns an unregister callable that removes the listener.
        """
        listener_id = uuid4().hex
        self._completion_listeners[listener_id] = listener

        def _unregister() -> None:
            self._completion_listeners.pop(listener_id, None)

        return _unregister

    async def _notify_completion_listeners(self, task: TaskRecord) -> None:
        snapshot = replace(task, metadata=dict(task.metadata))
        for listener_id, listener in list(self._completion_listeners.items()):
            try:
                maybe_awaitable = listener(snapshot)
                if maybe_awaitable is not None:
                    await maybe_awaitable
            except Exception:
                logger.exception(
                    "Task completion listener %s failed for task %s",
                    listener_id,
                    task.id,
                )

    async def _watch_process(
        self,
        task_id: str,
        process: asyncio.subprocess.Process,
        generation: int,
    ) -> None:
        reader = asyncio.create_task(self._copy_output(task_id, process))
        return_code = await process.wait()
        await reader
        await _close_process_stdin(process)

        current_generation = self._generations.get(task_id)
        if current_generation != generation:
            return

        task = self._tasks[task_id]
        task.return_code = return_code
        if task.status != TaskStatus.KILLED:
            self._set_task_status(task, TaskStatus.COMPLETED if return_code == 0 else TaskStatus.FAILED)
        task.ended_at = time.time()
        output_tail = ""
        if return_code != 0:
            output_tail = self.read_task_output(task_id, max_bytes=800).strip()
        logger.event(
            "task_manager_process_exit",
            task_id=task.id,
            task_type=task.type,
            return_code=return_code,
            status=task.status,
            duration_s=(task.ended_at - task.started_at) if task.started_at and task.ended_at else None,
            output_tail=output_tail,
        )
        self._processes.pop(task_id, None)
        self._waiters.pop(task_id, None)
        await self._notify_completion_listeners(task)

    async def _copy_output(self, task_id: str, process: asyncio.subprocess.Process) -> None:
        if process.stdout is None:
            return
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                return
            async with self._output_locks[task_id]:
                with self._tasks[task_id].output_file.open("ab") as handle:
                    handle.write(chunk)

    def _require_task(self, task_id: str) -> TaskRecord:
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"No task found with ID: {task_id}")
        return task

    async def _start_process(self, task_id: str) -> asyncio.subprocess.Process:
        task = self._require_task(task_id)
        if task.command is None and task.argv is None:
            raise ValueError(f"Task {task_id} does not have a command to run")

        generation = self._generations.get(task_id, 0) + 1
        self._generations[task_id] = generation
        command = task.command or ""
        command_head = task.argv[0] if task.argv else (command.split(maxsplit=1)[0] if command else None)
        command_has_task_worker = (
            "--task-worker" in task.argv if task.argv is not None else "--task-worker" in command
        )
        logger.event(
            "task_manager_process_start",
            task_id=task.id,
            task_type=task.type,
            generation=generation,
            cwd=task.cwd,
            command_head=command_head,
            command_has_task_worker=command_has_task_worker,
        )
        if task.argv is not None:
            process = await asyncio.create_subprocess_exec(
                *task.argv,
                cwd=task.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=task.env,
            )
        else:
            process = await create_shell_subprocess(
                task.command or "",
                cwd=task.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=task.env,
            )
        self._processes[task_id] = process
        self._waiters[task_id] = asyncio.create_task(
            self._watch_process(task_id, process, generation)
        )
        return process

    async def _ensure_writable_process(
        self,
        task: TaskRecord,
    ) -> asyncio.subprocess.Process:
        process = self._processes.get(task.id)
        if process is not None and process.stdin is not None and process.returncode is None:
            return process
        if task.type not in {"local_agent", "remote_agent", "in_process_teammate"}:
            raise ValueError(f"Task {task.id} does not accept input")
        return await self._restart_agent_task(task)

    async def _restart_agent_task(self, task: TaskRecord) -> asyncio.subprocess.Process:
        if task.command is None:
            raise ValueError(f"Task {task.id} does not have a restart command")

        waiter = self._waiters.get(task.id)
        if waiter is not None and not waiter.done():
            await waiter

        restart_count = int(task.metadata.get("restart_count", "0")) + 1
        task.metadata["restart_count"] = str(restart_count)
        task.metadata["status_note"] = "Task restarted; prior interactive context was not preserved."
        self._set_task_status(task, TaskStatus.RUNNING)
        task.started_at = time.time()
        task.ended_at = None
        task.return_code = None
        with task.output_file.open("ab") as handle:
            handle.write(_TASK_RESTART_NOTICE.encode("utf-8"))
        logger.event(
            "task_manager_restart_agent_task",
            task_id=task.id,
            task_type=task.type,
            restart_count=restart_count,
        )
        return await self._start_process(task.id)

    def close(self) -> None:
        """Best-effort cleanup for any tracked subprocesses and watcher tasks."""
        for waiter in list(self._waiters.values()):
            waiter.cancel()
        self._waiters.clear()

        for process in list(self._processes.values()):
            stdin = process.stdin
            if stdin is not None and not stdin.is_closing():
                try:
                    stdin.close()
                except RuntimeError:
                    pass
            if process.returncode is None:
                try:
                    process.kill()
                except (ProcessLookupError, RuntimeError):
                    pass
        self._processes.clear()

    async def aclose(self) -> None:
        """Asynchronously shut down tracked subprocesses and waiters."""
        processes = list(self._processes.values())
        waiters = list(self._waiters.values())

        for process in processes:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await _close_process_stdin(process)

        for process in processes:
            if process.returncode is None:
                try:
                    await process.wait()
                except ProcessLookupError:
                    pass

        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)

        self._processes.clear()
        self._waiters.clear()


_DEFAULT_MANAGER: BackgroundTaskManager | None = None
_DEFAULT_MANAGER_KEY: str | None = None


def get_task_manager() -> BackgroundTaskManager:
    """Return the singleton task manager."""
    global _DEFAULT_MANAGER, _DEFAULT_MANAGER_KEY
    current_key = str(get_tasks_dir().resolve())
    if _DEFAULT_MANAGER is None or _DEFAULT_MANAGER_KEY != current_key:
        if _DEFAULT_MANAGER is not None:
            _DEFAULT_MANAGER.close()
        _DEFAULT_MANAGER = BackgroundTaskManager()
        _DEFAULT_MANAGER_KEY = current_key
    return _DEFAULT_MANAGER


def reset_task_manager() -> None:
    """Reset the singleton task manager, closing tracked subprocesses first."""
    global _DEFAULT_MANAGER, _DEFAULT_MANAGER_KEY
    if _DEFAULT_MANAGER is not None:
        _DEFAULT_MANAGER.close()
    _DEFAULT_MANAGER = None
    _DEFAULT_MANAGER_KEY = None


async def shutdown_task_manager() -> None:
    """Async reset that fully reaps tracked subprocesses before clearing state."""
    global _DEFAULT_MANAGER, _DEFAULT_MANAGER_KEY
    if _DEFAULT_MANAGER is not None:
        await _DEFAULT_MANAGER.aclose()
    _DEFAULT_MANAGER = None
    _DEFAULT_MANAGER_KEY = None


def _task_id(task_type: TaskType) -> str:
    prefixes = {
        "local_bash": "b",
        "local_agent": "a",
        "remote_agent": "r",
        "in_process_teammate": "t",
        "dream": "d",
    }
    return f"{prefixes[task_type]}{uuid4().hex[:8]}"


async def _close_process_stdin(process: asyncio.subprocess.Process) -> None:
    stdin = process.stdin
    if stdin is None or stdin.is_closing():
        return
    stdin.close()
    try:
        await stdin.wait_closed()
    except (BrokenPipeError, ConnectionResetError):
        pass
