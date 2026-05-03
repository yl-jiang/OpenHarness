"""Tool for spawning local agent tasks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.coordinator.agent_definitions import get_agent_definition
from openharness.coordinator.coordinator_mode import get_team_registry
from openharness.engine.types import ToolMetadataKey
from openharness.hooks import HookEvent
from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateSpawnConfig
from openharness.tasks import get_task_manager
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.utils.log import get_logger

logger = get_logger(__name__)


def _context_value(context: ToolExecutionContext, key: ToolMetadataKey | str) -> str | None:
    if not isinstance(context.metadata, dict):
        return None
    resolved_key = key.value if isinstance(key, ToolMetadataKey) else key
    value = context.metadata.get(resolved_key)
    return value if isinstance(value, str) and value.strip() else None


def _resolve_spawn_model(
    *,
    agent_default_model: str | None,
    current_model: str | None,
) -> str | None:
    if agent_default_model is None or agent_default_model == "inherit":
        return current_model
    return agent_default_model


class AgentToolInput(BaseModel):
    """Arguments for local agent spawning."""

    description: str = Field(description="Short description of the delegated work")
    prompt: str = Field(description="Full prompt for the local agent")
    subagent_type: str | None = Field(
        default=None,
        description="Agent type for definition lookup (e.g. 'general-purpose', 'Explore', 'worker')",
    )
    command: str | None = Field(default=None, description="Override spawn command")
    team: str | None = Field(default=None, description="Optional team to attach the agent to")
    mode: str = Field(
        default="local_agent",
        description="Agent mode: local_agent, remote_agent, or in_process_teammate",
    )


class AgentTool(BaseTool):
    """Spawn a local agent subprocess."""

    name = "agent"
    description = (
        "Spawn a local background agent task to delegate complex multi-step work. "
        "Use subagent_type to control tool access: "
        "'research' for read-only investigation, 'worker' for full read/write access, "
        "'verification' for test/build verification. "
        "Returns the agent_id and task_id immediately; poll with task_get/task_output."
    )
    input_model = AgentToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Short description of the delegated work",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Full prompt for the local agent",
                    },
                    "subagent_type": {
                        "type": "string",
                        "description": (
                            "Agent type controlling tools and system prompt. "
                            "Key types: "
                            "'research' — read-only investigation, cannot modify files, use for the Research phase; "
                            "'worker' — full tool access (read + write + run), use for the Implementation phase; "
                            "'verification' — read-only, runs tests/builds, produces PASS/FAIL verdict; "
                            "'general-purpose' — broad tasks not fitting the above. "
                            "Defaults to 'worker' if omitted."
                        ),
                    },
                    "command": {
                        "type": "string",
                        "description": "Override spawn command",
                    },
                    "team": {
                        "type": "string",
                        "description": "Optional team to attach the agent to",
                    },
                    "mode": {
                        "type": "string",
                        "description": (
                            "Execution mode: "
                            "'local_agent' spawns a subprocess agent (default); "
                            "'remote_agent' targets a remote worker; "
                            "'in_process_teammate' runs in the same process."
                        ),
                        "default": "local_agent",
                    },
                },
                "required": ["description", "prompt"],
            },
        }

    async def execute(self, arguments: AgentToolInput, context: ToolExecutionContext) -> ToolResult:
        session_id = _context_value(context, "session_id")
        logger.event(
            "agent_tool_execute_start",
            session_id=session_id,
            description=arguments.description,
            mode=arguments.mode,
            subagent_type=arguments.subagent_type,
            team=arguments.team or "default",
            prompt_length=len(arguments.prompt),
            cwd=str(context.cwd),
        )
        if arguments.mode not in {"local_agent", "remote_agent", "in_process_teammate"}:
            logger.event(
                "agent_tool_invalid_mode",
                session_id=session_id,
                requested_mode=arguments.mode,
            )
            return ToolResult(
                output="Invalid mode. Use local_agent, remote_agent, or in_process_teammate.",
                is_error=True,
            )

        # Look up agent definition if subagent_type is specified
        agent_def = None
        if arguments.subagent_type:
            agent_def = get_agent_definition(arguments.subagent_type)

        # Resolve team and agent name for the swarm backend
        team = arguments.team or "default"
        agent_name = arguments.subagent_type or "agent"

        resolved_model = _resolve_spawn_model(
            agent_default_model=agent_def.model if agent_def else None,
            current_model=_context_value(context, ToolMetadataKey.CURRENT_MODEL),
        )

        # Use subprocess backend so spawned agents are registered in
        # BackgroundTaskManager and are pollable by the task tools.
        # in_process tasks return asyncio-internal IDs that task tools
        # cannot query, and subprocess is always available on all platforms.
        registry = get_backend_registry()
        executor = registry.get_executor("subprocess")

        config = TeammateSpawnConfig(
            name=agent_name,
            team=team,
            prompt=arguments.prompt,
            cwd=str(context.cwd),
            parent_session_id="main",
            model=resolved_model,
            api_format=_context_value(context, ToolMetadataKey.CURRENT_API_FORMAT),
            base_url=_context_value(context, ToolMetadataKey.CURRENT_BASE_URL),
            provider=_context_value(context, ToolMetadataKey.CURRENT_PROVIDER),
            command=arguments.command,
            system_prompt=agent_def.system_prompt if agent_def else None,
            permissions=agent_def.permissions if agent_def else [],
            disallowed_tools=agent_def.disallowed_tools if agent_def else [],
            allowed_tools=agent_def.tools if agent_def else None,
            session_id=session_id,
            task_type=arguments.mode,
        )

        try:
            result = await executor.spawn(config)
        except Exception as exc:
            logger.exception(
                "Failed to spawn agent",
                agent_name=agent_name,
                team=team,
                session_id=session_id,
            )
            logger.event(
                "agent_tool_spawn_failed",
                session_id=session_id,
                agent_name=agent_name,
                team=team,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return ToolResult(output=str(exc), is_error=True)

        if not result.success:
            logger.event(
                "agent_tool_spawn_result",
                session_id=session_id,
                agent_name=agent_name,
                team=team,
                success=False,
                backend_type=result.backend_type,
                task_id=result.task_id,
                error=result.error,
            )
            return ToolResult(output=result.error or "Failed to spawn agent", is_error=True)

        if arguments.team:
            registry = get_team_registry()
            try:
                registry.add_agent(arguments.team, result.task_id)
            except ValueError:
                registry.create_team(arguments.team)
                registry.add_agent(arguments.team, result.task_id)

        if context.hook_executor is not None:
            manager = get_task_manager()
            unregister = None

            async def _emit_subagent_stop(task_record) -> None:
                nonlocal unregister
                if task_record.id != result.task_id:
                    return
                if unregister is not None:
                    unregister()
                    unregister = None
                await context.hook_executor.execute(
                    HookEvent.SUBAGENT_STOP,
                    {
                        "event": HookEvent.SUBAGENT_STOP.value,
                        "agent_id": result.agent_id,
                        "task_id": result.task_id,
                        "backend_type": result.backend_type,
                        "status": task_record.status,
                        "return_code": task_record.return_code,
                        "description": arguments.description,
                        "subagent_type": arguments.subagent_type or "agent",
                        "team": team,
                        "mode": arguments.mode,
                    },
                )

            unregister = manager.register_completion_listener(_emit_subagent_stop)
            task_record = manager.get_task(result.task_id)
            if task_record is not None and task_record.status in {"completed", "failed", "killed"}:
                await _emit_subagent_stop(task_record)

        logger.event(
            "agent_tool_spawn_result",
            session_id=session_id,
            agent_name=agent_name,
            team=team,
            success=True,
            backend_type=result.backend_type,
            task_id=result.task_id,
            agent_id=result.agent_id,
        )
        return ToolResult(
            output=(
                f"Spawned agent {result.agent_id} "
                f"(task_id={result.task_id}, backend={result.backend_type})"
            )
        )
