"""Tool for asking the interactive user a follow-up question."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


AskUserPrompt = Callable[[str], Awaitable[str]]


class AskUserQuestionToolInput(BaseModel):
    """Arguments for asking the user a question."""

    question: str = Field(
        description=(
            "The exact question to show the user. Use this tool instead of asking in normal "
            "assistant text whenever you need clarification, confirmation, or the user to "
            "choose between options."
        )
    )


class AskUserQuestionTool(BaseTool):
    """Ask the interactive user a question and return the answer."""

    name = "ask_user_question"
    description = (
        "Ask the interactive user a follow-up question and return the answer. Use this tool "
        "instead of plain assistant text whenever you need clarification, confirmation, or "
        "the user to make a choice."
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
                        "description": (
                            "The exact question to show the user. Use this tool instead of "
                            "asking in normal assistant text whenever you need clarification, "
                            "confirmation, or the user to choose between options."
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
        answer = str(await prompt(arguments.question)).strip()
        if not answer:
            return ToolResult(output="(no response)")
        return ToolResult(output=answer)
