"""Tool for maintaining a project TODO file."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, List, Any
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


def _resolve_project_root(start: Path) -> Path:
    """Resolve the active project root from the current working directory."""
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    fallback = current
    while True:
        if (current / ".git").exists() or (current / "pyproject.toml").exists():
            return current
        if current.parent == current:
            return fallback
        current = current.parent


class TodoStore:
    """
    In-memory todo list. One instance per AIAgent (one per session).

    Items are ordered -- list position is priority. Each item has:
      - id: unique string identifier (agent-chosen)
      - content: task description
      - status: pending | in_progress | completed | cancelled
    """

    def __init__(self, project_root: Path | None = None):
        self._items: List[Dict[str, str]] = []
        self._project_root = _resolve_project_root(project_root or Path.cwd())

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, str]]:
        """
        Write todos. Returns the full current list after writing.

        Args:
            todos: list of {id, content, status} dicts
            merge: if False, replace the entire list. If True, update
                   existing items by id and append new ones.
        """
        if not merge:
            # Replace mode: new list entirely
            self._items = [self._validate(t) for t in self._dedupe_by_id(todos)]
        else:
            # Merge mode: update existing items by id, append new ones
            existing = {item["id"]: item for item in self._items}
            for t in self._dedupe_by_id(todos):
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue  # Can't merge without an id

                if item_id in existing:
                    # Update only the fields the LLM actually provided
                    if "content" in t and t["content"]:
                        existing[item_id]["content"] = str(t["content"]).strip()
                    if "status" in t and t["status"]:
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                else:
                    # New item -- validate fully and append to end
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)
            # Rebuild _items preserving order for existing items
            seen = set()
            rebuilt = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = rebuilt
        
        # self._persist_to_file()
        return self.read()

    def read(self) -> List[Dict[str, str]]:
        """Return a copy of the current list."""
        return [item.copy() for item in self._items]

    def has_items(self) -> bool:
        """Check if there are any items in the list."""
        return bool(self._items)

    def format_for_injection(self) -> Optional[str]:
        """
        Render the todo list for post-compression injection.

        Returns a human-readable string to append to the compressed
        message history, or None if the list is empty.
        """
        if not self._items:
            return None

        # Status markers for compact display
        markers = {
            "completed": "[x]",
            "in_progress": "[>]",
            "pending": "[ ]",
            "cancelled": "[~]",
        }

        # Only inject pending/in_progress items — completed/cancelled ones
        # cause the model to re-do finished work after compression.
        active_items = [
            item for item in self._items
            if item["status"] in ("pending", "in_progress")
        ]
        if not active_items:
            return None

        lines = ["[Your active task list was preserved across context compression]"]
        for item in active_items:
            marker = markers.get(item["status"], "[?]")
            lines.append(f"- {marker} {item['id']}. {item['content']} ({item['status']})")

        return "\n".join(lines)

    @staticmethod
    def _validate(item: Dict[str, Any]) -> Dict[str, str]:
        """
        Validate and normalize a todo item.

        Ensures required fields exist and status is valid.
        Returns a clean dict with only {id, content, status}.
        """
        item_id = str(item.get("id", "")).strip()
        if not item_id:
            item_id = "?"

        content = str(item.get("content", "")).strip()
        if not content:
            content = "(no description)"

        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"

        return {"id": item_id, "content": content, "status": status}

    def _persist_to_file(self) -> None:
        """Write the current todo list to the active project TODO.md."""
        md_path = self._project_root / "TODO.md"

        status_order = {"in_progress": 0, "pending": 1, "completed": 2, "cancelled": 3}
        status_headers = {
            "in_progress": "## 🔵 In Progress",
            "pending": "## ⬜ Pending",
            "completed": "## ✅ Completed",
            "cancelled": "## ❌ Cancelled",
        }

        lines = ["# TODO", ""]

        # Group items by status
        grouped: Dict[str, List[Dict[str, str]]] = {}
        for item in self._items:
            grouped.setdefault(item["status"], []).append(item)

        # Emit sections in a fixed priority order
        for status in sorted(grouped.keys(), key=lambda s: status_order.get(s, 99)):
            lines.append(status_headers.get(status, f"## {status.title()}"))
            lines.append("")
            for item in grouped[status]:
                lines.append(f"- {item['content']}  `({item['id']})`")
            lines.append("")

        md_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse duplicate ids, keeping the last occurrence in its position."""
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]


class TodoItem(BaseModel):
    id: str = Field(description="Unique item identifier")
    content: str = Field(description="Task description")
    status: Literal["pending", "in_progress", "completed", "cancelled"] = Field(description="Current status")


class TodoToolInput(BaseModel):
    todos: Optional[list[TodoItem]] = Field(
        default=None,
        description="Task items to write. Omit to read current list.",
    )
    merge: bool = Field(
        default=False,
        description=(
            "true: update existing items by id, add new ones. "
            "false (default): replace the entire list."
        ),
    )


TODO_TOOL_DESCRIPTION = (
    "Manage your task list for the current session. Use for complex tasks "
    "with 3+ steps or when the user provides multiple tasks. "
    "Call with no parameters to read the current list.\n\n"
    "Writing:\n"
    "- Provide 'todos' array to create/update items\n"
    "- merge=false (default): replace the entire list with a fresh plan\n"
    "- merge=true: update existing items by id, add any new ones\n\n"
    "Each item: {id: string, content: string, "
    "status: pending|in_progress|completed|cancelled}\n"
    "List order is priority. Only ONE item in_progress at a time.\n"
    "Mark items completed immediately when done. If something fails, "
    "cancel it and add a revised item.\n\n"
    "Always returns the full current list."
)

class TodoTool(BaseTool):
    """Add or update an item in a TODO markdown file."""

    name = "todo"
    description = TODO_TOOL_DESCRIPTION
    input_model = TodoToolInput

    def to_api_schema(self) -> dict[str, Any]:
        """Return a hand-crafted, LLM-friendly schema for todo.

        Pydantic v2 encodes ``Optional[list[TodoItem]]`` as
        ``anyOf: [{type: "array", items: {$ref: ...}}, {type: "null"}]``.
        Many OpenAI-compatible models (e.g. MiniMax, GLM) interpret this as
        "send null by default", which makes every call a read-only no-op.

        This override emits a clean schema:
        - ``todos`` is an optional array property (absent = read mode)
        - No null type, no $ref, no $defs — just a flat, unambiguous object
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": (
                            "Task items to write. "
                            "Omit this field entirely to read the current list."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Unique item identifier",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Task description",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": [
                                        "pending",
                                        "in_progress",
                                        "completed",
                                        "cancelled",
                                    ],
                                    "description": "Current status",
                                },
                            },
                            "required": ["id", "content", "status"],
                        },
                    },
                    "merge": {
                        "type": "boolean",
                        "description": (
                            "true: update existing items by id, add new ones. "
                            "false (default): replace the entire list."
                        ),
                        "default": False,
                    },
                },
            },
        }

    def is_read_only(self, arguments: TodoToolInput) -> bool:
        return arguments.todos is None

    async def execute(
        self,
        arguments: TodoToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Single entry point for the todo tool. Reads or writes depending on params.

        Args:
            arguments: if provided, write these items. If None, read current list.
            merge: if True, update by id. If False (default), replace entire list.
            store: the TodoStore instance from the AIAgent.

        Returns:
            JSON string with the full current list and summary metadata.
        """
        store = context.metadata.get("todo_store")
        if not isinstance(store, TodoStore):
            store = TodoStore(context.cwd)
            context.metadata["todo_store"] = store

        if arguments.todos is not None:
            items = store.write(
                [item.model_dump(mode="json") for item in arguments.todos],
                arguments.merge,
            )
        else:
            items = store.read()

        # Build summary counts
        pending = sum(1 for i in items if i["status"] == "pending")
        in_progress = sum(1 for i in items if i["status"] == "in_progress")
        completed = sum(1 for i in items if i["status"] == "completed")
        cancelled = sum(1 for i in items if i["status"] == "cancelled")

        return ToolResult(
            output=json.dumps(
                {
                    "todos": items,
                    "summary": {
                        "total": len(items),
                        "pending": pending,
                        "in_progress": in_progress,
                        "completed": completed,
                        "cancelled": cancelled,
                    },
                },
                ensure_ascii=False,
            )
        )
