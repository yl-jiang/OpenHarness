"""Tool for spawning local agent tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.config.settings import is_claude_family_provider
from openharness.coordinator.agent_definitions import get_agent_definition
from openharness.coordinator.coordinator_mode import get_team_registry
from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateSpawnConfig
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.utils.log import get_logger

logger = get_logger(__name__)

_CLAUDE_ONLY_MODELS = frozenset({
    "haiku",
    "sonnet",
    "opus",
    "sonnet[1m]",
    "opus[1m]",
    "opusplan",
})

def _context_value(context: ToolExecutionContext, key: str) -> str | None:
    if not isinstance(context.metadata, dict):
        return None
    value = context.metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _is_claude_only_model(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized in _CLAUDE_ONLY_MODELS or normalized.startswith("claude-")


def _resolve_spawn_model(
    *,
    agent_default_model: str | None,
    current_model: str | None,
    current_provider: str | None,
) -> str | None:
    if agent_default_model is None or agent_default_model == "inherit":
        return current_model
    if current_provider and not is_claude_family_provider(current_provider) and _is_claude_only_model(agent_default_model):
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
    description = "Spawn a local background agent task."
    input_model = AgentToolInput

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
            current_model=_context_value(context, "current_model"),
            current_provider=_context_value(context, "current_provider"),
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
            api_format=_context_value(context, "current_api_format"),
            base_url=_context_value(context, "current_base_url"),
            provider=_context_value(context, "current_provider"),
            command=arguments.command,
            system_prompt=agent_def.system_prompt if agent_def else None,
            permissions=agent_def.permissions if agent_def else [],
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
