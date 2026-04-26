"""Subprocess-based TeammateExecutor implementation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openharness.swarm.spawn_utils import (
    build_inherited_cli_flags,
    build_inherited_env_vars,
    get_teammate_command,
)
from openharness.swarm.types import (
    BackendType,
    SpawnResult,
    TeammateMessage,
    TeammateSpawnConfig,
)
from openharness.tasks.manager import get_task_manager
from openharness.utils.log import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class SubprocessBackend:
    """TeammateExecutor that runs each teammate as a separate subprocess.

    Uses the existing :class:`~openharness.tasks.manager.BackgroundTaskManager`
    to create and manage the child processes, communicating via stdin/stdout.
    """

    type: BackendType = "subprocess"

    # Maps agent_id -> task_id for tracking live agents
    _agent_tasks: dict[str, str]

    def __init__(self) -> None:
        self._agent_tasks = {}

    def is_available(self) -> bool:
        """Subprocess backend is always available."""
        return True

    async def spawn(self, config: TeammateSpawnConfig) -> SpawnResult:
        """Spawn a new teammate as a subprocess via the task manager.

        Builds the appropriate CLI command and creates a ``local_agent`` task
        that accepts the initial prompt via stdin.
        """
        agent_id = f"{config.name}@{config.team}"

        flags = build_inherited_cli_flags(
            model=config.model,
            api_format=config.api_format,
            base_url=config.base_url,
            system_prompt=config.system_prompt,
            system_prompt_mode=config.system_prompt_mode,
            plan_mode_required=config.plan_mode_required,
            disallowed_tools=config.disallowed_tools,
            allowed_tools=config.allowed_tools,
        )
        extra_env = build_inherited_env_vars(
            model=config.model,
            api_format=config.api_format,
            base_url=config.base_url,
            provider=config.provider,
        )
        trace_session_id = config.session_id or config.parent_session_id or None
        logger.event(
            "subprocess_backend_spawn_start",
            session_id=trace_session_id,
            agent_id=agent_id,
            team=config.team,
            agent_name=config.name,
            cwd=config.cwd,
            requested_model=config.model,
            inherited_api_format=config.api_format,
            inherited_base_url=config.base_url,
            inherited_provider=config.provider,
            prompt_length=len(config.prompt),
            permission_count=len(config.permissions),
            plan_mode_required=config.plan_mode_required,
            env_keys=sorted(extra_env.keys()),
            flag_count=len(flags),
        )

        command = config.command
        if command is None:
            # Build environment export prefix for shell invocation
            env_prefix = " ".join(f"{k}={v!r}" for k, v in extra_env.items())

            teammate_cmd = get_teammate_command()
            if teammate_cmd.endswith("python") or teammate_cmd.endswith("python3") or "/python" in teammate_cmd:
                cmd_parts = [teammate_cmd, "-m", "openharness", "--task-worker"] + flags
            else:
                cmd_parts = [teammate_cmd, "--task-worker"] + flags
            command = f"{env_prefix} {' '.join(cmd_parts)}" if env_prefix else " ".join(cmd_parts)
            logger.event(
                "subprocess_backend_command_built",
                session_id=trace_session_id,
                agent_id=agent_id,
                command_head=cmd_parts[0] if cmd_parts else "",
                command_length=len(command),
                command_has_task_worker="--task-worker" in command,
            )

        manager = get_task_manager()
        try:
            record = await manager.create_agent_task(
                prompt=config.prompt,
                description=f"Teammate: {agent_id}",
                cwd=config.cwd,
                task_type=config.task_type,
                model=config.model,
                command=command,
            )
        except Exception as exc:
            logger.exception(
                "Failed to spawn teammate %s",
                agent_id,
                session_id=trace_session_id,
                agent_id=agent_id,
            )
            logger.event(
                "subprocess_backend_spawn_result",
                session_id=trace_session_id,
                agent_id=agent_id,
                success=False,
                backend_type=self.type,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return SpawnResult(
                task_id="",
                agent_id=agent_id,
                backend_type=self.type,
                success=False,
                error=str(exc),
            )

        self._agent_tasks[agent_id] = record.id
        logger.debug("Spawned teammate %s as task %s", agent_id, record.id, agent_id=agent_id, task_id=record.id)
        logger.event(
            "subprocess_backend_spawn_result",
            session_id=trace_session_id,
            agent_id=agent_id,
            success=True,
            backend_type=self.type,
            task_id=record.id,
            task_type=record.type,
        )
        return SpawnResult(
            task_id=record.id,
            agent_id=agent_id,
            backend_type=self.type,
        )

    async def send_message(self, agent_id: str, message: TeammateMessage) -> None:
        """Send a message to a running teammate via its stdin pipe.

        The message is serialised as a single JSON line so the teammate can
        distinguish structured messages from plain prompts.
        """
        task_id = self._agent_tasks.get(agent_id)
        if task_id is None:
            raise ValueError(f"No active subprocess for agent {agent_id!r}")

        payload = {
            "text": message.text,
            "from": message.from_agent,
            "timestamp": message.timestamp,
        }
        if message.color:
            payload["color"] = message.color
        if message.summary:
            payload["summary"] = message.summary

        manager = get_task_manager()
        await manager.write_to_task(task_id, json.dumps(payload))
        logger.debug("Sent message to %s (task %s)", agent_id, task_id, agent_id=agent_id, task_id=task_id)

    async def shutdown(self, agent_id: str, *, force: bool = False) -> bool:
        """Terminate a subprocess teammate.

        Args:
            agent_id: The agent to terminate.
            force: Ignored for subprocess backend; always sends SIGTERM then
                   SIGKILL after a brief wait (handled by the task manager).

        Returns:
            True if the task was found and terminated.
        """
        task_id = self._agent_tasks.get(agent_id)
        if task_id is None:
            logger.warning("shutdown() called for unknown agent %s", agent_id, agent_id=agent_id)
            return False

        manager = get_task_manager()
        try:
            await manager.stop_task(task_id)
        except ValueError as exc:
            logger.debug("stop_task for %s: %s", task_id, exc, agent_id=agent_id, task_id=task_id)
            # Task may have already finished — still clean up mapping
        finally:
            self._agent_tasks.pop(agent_id, None)

        logger.debug("Shut down teammate %s (task %s)", agent_id, task_id, agent_id=agent_id, task_id=task_id)
        return True

    def get_task_id(self, agent_id: str) -> str | None:
        """Return the task manager task ID for a given agent, if known."""
        return self._agent_tasks.get(agent_id)
