"""Tool adapter for curated persistent memory."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.memory.paths import get_curated_memory_dir
from openharness.memory.store import MemoryOperationResult, MemoryStore, MemoryTarget
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class MemoryToolInput(BaseModel):
    """Arguments for the memory tool."""

    action: Literal["add", "replace", "remove", "read"] = Field(
        description="Operation to perform on persistent memory."
    )
    target: MemoryTarget = Field(
        default="memory",
        description="'memory' for project/tool facts, or 'user' for user profile facts.",
    )
    content: str | None = Field(default=None, description="Entry content for add or replace.")
    old_text: str | None = Field(
        default=None,
        description="Unique substring identifying the entry for replace or remove.",
    )


class MemoryTool(BaseTool):
    """Read and update curated persistent memory."""

    name = "memory"
    description = (
        "Save durable information to persistent memory that survives across sessions. "
        "Use it for stable user preferences, project conventions, environment facts, "
        "and tool quirks. Do not save temporary task progress, raw logs, or TODO state."
    )
    input_model = MemoryToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                self.description
                + "\n\nTargets:\n"
                "- user: who the user is, preferences, communication style, workflow habits\n"
                "- memory: project conventions, environment facts, tool quirks, lessons learned\n\n"
                "Actions: add, replace, remove, read. For replace/remove, provide old_text as "
                "a short unique substring of the entry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "replace", "remove", "read"],
                        "description": "The memory operation to perform.",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["memory", "user"],
                        "default": "memory",
                        "description": "Which memory file to operate on.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Entry content for add or replacement content for replace.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Unique substring used to identify an entry for replace/remove.",
                    },
                },
                "required": ["action"],
            },
        }

    def is_read_only(self, arguments: BaseModel) -> bool:
        return isinstance(arguments, MemoryToolInput) and arguments.action == "read"

    async def execute(self, arguments: MemoryToolInput, context: ToolExecutionContext) -> ToolResult:
        store = MemoryStore(get_curated_memory_dir(context.cwd))
        store.load_from_disk()

        if arguments.action == "read":
            result = store.read(arguments.target)
        elif arguments.action == "add":
            if not arguments.content:
                return ToolResult(output="content is required for add.", is_error=True)
            result = store.add(arguments.target, arguments.content)
        elif arguments.action == "replace":
            if not arguments.old_text:
                return ToolResult(output="old_text is required for replace.", is_error=True)
            if not arguments.content:
                return ToolResult(output="content is required for replace.", is_error=True)
            result = store.replace(arguments.target, arguments.old_text, arguments.content)
        elif arguments.action == "remove":
            if not arguments.old_text:
                return ToolResult(output="old_text is required for remove.", is_error=True)
            result = store.remove(arguments.target, arguments.old_text)
        else:
            return ToolResult(output=f"Unknown memory action: {arguments.action}", is_error=True)

        self._notify_memory_write(arguments, context, result)
        return ToolResult(output=json.dumps(result.to_dict(), ensure_ascii=False), is_error=not result.success)

    def _notify_memory_write(
        self,
        arguments: MemoryToolInput,
        context: ToolExecutionContext,
        result: MemoryOperationResult,
    ) -> None:
        if not result.success or arguments.action == "read":
            return
        manager = context.metadata.get("memory_provider_manager")
        if manager is None or not hasattr(manager, "on_memory_write"):
            return
        content = arguments.content or arguments.old_text or ""
        manager.on_memory_write(arguments.action, arguments.target, content)
