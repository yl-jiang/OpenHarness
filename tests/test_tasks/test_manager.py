"""Tests for background task management."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openharness.tasks.manager import BackgroundTaskManager


@pytest.mark.asyncio
async def test_create_shell_task_and_read_output(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_shell_task(
        command="printf 'hello task'",
        description="hello",
        cwd=tmp_path,
    )

    await asyncio.wait_for(manager._waiters[task.id], timeout=5)  # type: ignore[attr-defined]
    updated = manager.get_task(task.id)
    assert updated is not None
    assert updated.status == "completed"
    assert "hello task" in manager.read_task_output(task.id)


@pytest.mark.asyncio
async def test_create_agent_task_with_command_override_and_write(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_agent_task(
        prompt="first",
        description="agent",
        cwd=tmp_path,
        command="while read line; do echo \"got:$line\"; break; done",
    )

    await asyncio.wait_for(manager._waiters[task.id], timeout=5)  # type: ignore[attr-defined]
    assert "got:first" in manager.read_task_output(task.id)


@pytest.mark.asyncio
async def test_write_to_stopped_agent_task_restarts_process(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_agent_task(
        prompt="ready",
        description="agent",
        cwd=tmp_path,
        command="while read line; do echo \"got:$line\"; break; done",
    )
    await asyncio.wait_for(manager._waiters[task.id], timeout=5)  # type: ignore[attr-defined]

    await manager.write_to_task(task.id, "follow-up")
    await asyncio.wait_for(manager._waiters[task.id], timeout=5)  # type: ignore[attr-defined]

    output = manager.read_task_output(task.id)
    assert "got:ready" in output
    assert "got:follow-up" in output
    updated = manager.get_task(task.id)
    assert updated is not None
    assert updated.metadata["restart_count"] == "1"


@pytest.mark.asyncio
async def test_stop_task(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    task = await manager.create_shell_task(
        command="sleep 30",
        description="sleeper",
        cwd=tmp_path,
    )
    await manager.stop_task(task.id)
    updated = manager.get_task(task.id)
    assert updated is not None
    assert updated.status == "killed"


@pytest.mark.asyncio
async def test_task_manager_on_task_change_fires_callback_when_task_stopped(
    tmp_path: Path, monkeypatch
):
    """on_task_change callback must fire with (task, old_status, new_status)
    when a task transitions from 'running' to 'killed' via stop_task()."""
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    manager = BackgroundTaskManager()

    changes: list[tuple[str, str, str]] = []  # (task_id, old_status, new_status)

    def _on_change(task, old_status: str, new_status: str) -> None:
        changes.append((task.id, old_status, new_status))

    manager.on_task_change(_on_change)

    task = await manager.create_shell_task(
        command="sleep 30",
        description="sleeper",
        cwd=tmp_path,
    )
    await manager.stop_task(task.id)

    assert any(
        task_id == task.id and old == "running" and new == "killed"
        for task_id, old, new in changes
    ), f"Expected 'running' -> 'killed' transition for {task.id}; got {changes}"


@pytest.mark.asyncio
async def test_create_agent_task_writes_trace_records_for_failed_lifecycle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    trace_path = tmp_path / "task-manager-trace.jsonl"
    monkeypatch.setenv("OPENHARNESS_TRACE_FILE", str(trace_path))
    manager = BackgroundTaskManager()

    task = await manager.create_agent_task(
        prompt="ready",
        description="trace-agent",
        cwd=tmp_path,
        command="while read line; do echo \"AUTH_FAIL:$line\"; echo \"401 unauthorized\" >&2; exit 7; done",
    )

    await asyncio.wait_for(manager._waiters[task.id], timeout=5)  # type: ignore[attr-defined]

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(
        record["event"] == "task_manager_create_agent_task"
        and record.get("task_id") == task.id
        and record.get("task_type") == "local_agent"
        for record in records
    )
    assert any(
        record["event"] == "task_manager_process_start"
        and record.get("task_id") == task.id
        for record in records
    )
    assert any(
        record["event"] == "task_manager_process_exit"
        and record.get("task_id") == task.id
        and record.get("return_code") == 7
        and record.get("status") == "failed"
        and "AUTH_FAIL:ready" in str(record.get("output_tail", ""))
        and "401 unauthorized" in str(record.get("output_tail", ""))
        for record in records
    )
