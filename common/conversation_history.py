from __future__ import annotations

from openharness.engine.messages import ConversationMessage, ToolResultBlock


def _is_completed_assistant_reply(message: ConversationMessage) -> bool:
    return (
        message.role == "assistant"
        and not message.tool_uses
        and bool(message.text.strip())
    )


def _has_tool_activity(message: ConversationMessage) -> bool:
    if message.role == "assistant":
        return bool(message.tool_uses)
    return any(isinstance(block, ToolResultBlock) for block in message.content)


def _is_plain_user_prompt(message: ConversationMessage) -> bool:
    return (
        message.role == "user"
        and bool(message.text.strip())
        and not any(isinstance(block, ToolResultBlock) for block in message.content)
    )


def stabilize_conversation_history(
    messages: list[ConversationMessage],
) -> tuple[list[ConversationMessage], bool]:
    """Rollback any trailing tool loop that never reached a final assistant reply.

    Keeps plain user-only histories intact so lightweight/manual session snapshots
    still work, but drops half-finished tool turns such as:
    user text -> assistant tool_use -> user tool_result -> ...EOF
    """

    if not messages:
        return [], False

    last_completed_assistant_index: int | None = None
    for index, message in enumerate(messages):
        if _is_completed_assistant_reply(message):
            last_completed_assistant_index = index

    trailing = (
        messages[last_completed_assistant_index + 1 :]
        if last_completed_assistant_index is not None
        else messages
    )
    if not any(_has_tool_activity(message) for message in trailing):
        return list(messages), False

    if last_completed_assistant_index is None:
        return [], True
    return list(messages[: last_completed_assistant_index + 1]), True


def trim_conversation_history_to_turn_boundary(
    messages: list[ConversationMessage],
    max_messages: int,
) -> list[ConversationMessage]:
    """Keep a recent suffix without starting in the middle of a user turn."""

    if max_messages <= 0:
        return []
    if len(messages) <= max_messages:
        return list(messages)

    start = len(messages) - max_messages
    adjusted_start = start
    while adjusted_start < len(messages):
        if _is_plain_user_prompt(messages[adjusted_start]):
            return list(messages[adjusted_start:])
        adjusted_start += 1
    return list(messages[start:])
