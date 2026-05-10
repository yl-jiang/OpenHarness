"""Events yielded by the query engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage


class CompactProgressPhase(str, Enum):
    """Shared phase names for conversation compaction progress."""

    HOOKS_START = "hooks_start"
    CONTEXT_COLLAPSE_START = "context_collapse_start"
    CONTEXT_COLLAPSE_END = "context_collapse_end"
    SESSION_MEMORY_START = "session_memory_start"
    SESSION_MEMORY_END = "session_memory_end"
    COMPACT_START = "compact_start"
    COMPACT_RETRY = "compact_retry"
    COMPACT_END = "compact_end"
    COMPACT_FAILED = "compact_failed"

    @classmethod
    def start_phases(cls) -> frozenset["CompactProgressPhase"]:
        return frozenset(
            {
                cls.CONTEXT_COLLAPSE_START,
                cls.SESSION_MEMORY_START,
                cls.COMPACT_START,
            }
        )


@dataclass(frozen=True)
class AssistantTextDelta:
    """Incremental assistant text."""

    text: str


@dataclass(frozen=True)
class AssistantTurnComplete:
    """Completed assistant turn."""

    message: ConversationMessage
    usage: UsageSnapshot


@dataclass(frozen=True)
class ToolExecutionStarted:
    """The engine is about to execute a tool."""

    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionCompleted:
    """A tool has finished executing."""

    tool_name: str
    output: str
    is_error: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ErrorEvent:
    """An error that should be surfaced to the user."""

    message: str
    recoverable: bool = True


@dataclass(frozen=True)
class StatusEvent:
    """A transient system status message shown to the user."""

    message: str


@dataclass(frozen=True)
class StreamFinished:
    """Terminal event yielded by the engine when streaming ends for a non-normal reason.

    Emitted when auto-continue budget is exhausted or max_turns is exceeded.
    Normal completion (model returns text and stops) does NOT emit this event.
    Callers should default to ``reason="completed"`` when this event is absent.
    """

    reason: Literal[
        "auto_continue_exhausted",
        "max_turns_exceeded",
    ]
    detail: str | None = None


@dataclass(frozen=True)
class CompactProgressEvent:
    """Structured progress event for conversation compaction."""

    phase: CompactProgressPhase
    trigger: Literal["auto", "manual", "reactive"]
    message: str | None = None
    attempt: int | None = None
    checkpoint: str | None = None
    metadata: dict[str, Any] | None = None


StreamEvent = (
    AssistantTextDelta
    | AssistantTurnComplete
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | ErrorEvent
    | StatusEvent
    | CompactProgressEvent
    | StreamFinished
)
