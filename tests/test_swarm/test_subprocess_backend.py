from __future__ import annotations

from dataclasses import dataclass

import pytest

from openharness.swarm.subprocess_backend import SubprocessBackend
from openharness.swarm.types import TeammateSpawnConfig


@dataclass
class _FakeTaskRecord:
    id: str
    type: str = "local_agent"


class _FakeTaskManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_agent_task(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeTaskRecord(id="task-123")


@pytest.mark.asyncio
async def test_subprocess_backend_forwards_replace_system_prompt(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_create_agent_task(self, **kwargs):
        captured.update(kwargs)
        return _FakeTaskRecord(id="task-123")

    monkeypatch.setattr(
        "openharness.tasks.manager.BackgroundTaskManager.create_agent_task",
        fake_create_agent_task,
    )
    monkeypatch.setattr("openharness.swarm.subprocess_backend.get_teammate_command", lambda: "/usr/bin/python3")
    monkeypatch.setattr("openharness.swarm.subprocess_backend.build_inherited_env_vars", lambda **_: {})

    backend = SubprocessBackend()
    result = await backend.spawn(
        TeammateSpawnConfig(
            name="Plan",
            team="default",
            prompt="design the feature",
            cwd=str(tmp_path),
            parent_session_id="parent-1",
            model="inherit",
            system_prompt="PLAN_PROMPT",
            system_prompt_mode="replace",
        )
    )

    assert result.success is True
    command = str(captured["command"])
    assert "--task-worker" in command
    assert "--system-prompt PLAN_PROMPT" in command


@pytest.mark.asyncio
async def test_subprocess_backend_forwards_append_system_prompt(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_create_agent_task(self, **kwargs):
        captured.update(kwargs)
        return _FakeTaskRecord(id="task-123")

    monkeypatch.setattr(
        "openharness.tasks.manager.BackgroundTaskManager.create_agent_task",
        fake_create_agent_task,
    )
    monkeypatch.setattr("openharness.swarm.subprocess_backend.get_teammate_command", lambda: "/usr/bin/python3")
    monkeypatch.setattr("openharness.swarm.subprocess_backend.build_inherited_env_vars", lambda **_: {})

    backend = SubprocessBackend()
    result = await backend.spawn(
        TeammateSpawnConfig(
            name="verification",
            team="default",
            prompt="verify the implementation",
            cwd=str(tmp_path),
            parent_session_id="parent-1",
            model="inherit",
            system_prompt="VERIFY_PROMPT",
            system_prompt_mode="append",
        )
    )

    assert result.success is True
    command = str(captured["command"])
    assert "--append-system-prompt VERIFY_PROMPT" in command
