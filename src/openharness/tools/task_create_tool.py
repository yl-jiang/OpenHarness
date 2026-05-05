"""Tool for creating background tasks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.tasks.manager import get_task_manager
from openharness.tools.agent_tool import spawn_background_agent
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskCreateToolInput(BaseModel):
    """Arguments for task creation."""

    type: str = Field(default="local_bash", description="Task type: local_bash or local_agent")
    description: str = Field(description="Short task description")
    command: str | None = Field(default=None, description="Shell command for local_bash")
    prompt: str | None = Field(default=None, description="Prompt for local_agent")
    model: str | None = Field(default=None)


class TaskCreateTool(BaseTool):
    """Create a background task."""

    name = "task_create"
    description = (
        "Create low-level background tasks. Prefer local_bash for shell commands. "
        "Use agent for managed subagents; local_agent remains a compatibility path."
    )
    input_model = TaskCreateToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["local_bash", "local_agent"],
                        "description": (
                            "Task type. Prefer local_bash. "
                            "local_agent is a low-level compatibility path; use agent for managed subagents."
                        ),
                        "default": "local_bash",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short task description",
                    },
                    "command": {
                        "type": "string",
                        "description": (
                            "Shell command for local_bash tasks, or an explicit command override "
                            "for local_agent compatibility tasks."
                        ),
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Initial prompt for local_agent compatibility tasks",
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Model override for local_agent compatibility tasks "
                            "(e.g. 'claude-3-5-sonnet')"
                        ),
                    },
                },
                "required": ["description"],
            },
        }

    async def execute(self, arguments: TaskCreateToolInput, context: ToolExecutionContext) -> ToolResult:
        manager = get_task_manager()
        if arguments.type == "local_bash":
            if not arguments.command:
                return ToolResult(output="command is required for local_bash tasks", is_error=True)
            task = await manager.create_shell_task(
                command=arguments.command,
                description=arguments.description,
                cwd=context.cwd,
            )
        elif arguments.type == "local_agent":
            if not arguments.prompt:
                return ToolResult(output="prompt is required for local_agent tasks", is_error=True)
            try:
                result = await spawn_background_agent(
                    context=context,
                    prompt=arguments.prompt,
                    command=arguments.command,
                    mode="local_agent",
                    model=arguments.model,
                )
            except Exception as exc:
                return ToolResult(output=str(exc), is_error=True)
            task = manager.get_task(result.task_id)
            if task is None:
                return ToolResult(output=f"Spawned agent task {result.task_id} was not registered", is_error=True)
            task.description = arguments.description
            task.metadata["spawn_entrypoint"] = "task_create"
            task.metadata["spawn_api"] = "compatibility"
        else:
            return ToolResult(output=f"unsupported task type: {arguments.type}", is_error=True)

        return ToolResult(output=f"Created task {task.id} ({task.type})")
