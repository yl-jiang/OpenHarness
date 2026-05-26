"""Tool for asking the interactive user a follow-up question."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


AskUserPrompt = Callable[[str, list[str] | None], Awaitable[str]]


class AskUserQuestionToolInput(BaseModel):
    """Arguments for asking the user a question."""

    question: str = Field(description="The question to show the user.")
    choices: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of choices for the user to select from. "
            "When provided, the user can pick one via arrow keys. "
            "They can also type a freeform answer instead."
        ),
    )


class AskUserQuestionTool(BaseTool):
    """Ask the interactive user a question and return the answer."""

    name = "ask_user_question"
    description = (
        "Ask the user a question and return their answer. "
        "Supports optional choices for structured selection."
    )
    input_model = AskUserQuestionToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to show the user.",
                    },
                    "choices": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of choices for the user to select from. "
                            "When provided, the user can pick via arrow keys or type freeform."
                        ),
                    },
                },
                "required": ["question"],
            },
        }

    def is_read_only(self, arguments: AskUserQuestionToolInput) -> bool:
        del arguments
        return True

    async def execute(
        self,
        arguments: AskUserQuestionToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        prompt = context.metadata.get("ask_user_prompt")
        if not callable(prompt):
            return ToolResult(
                output="ask_user_question is unavailable in this session",
                is_error=True,
            )
        answer = str(await prompt(arguments.question, arguments.choices)).strip()
        if not answer:
            return ToolResult(output="(no response)")
        return ToolResult(output=answer)
