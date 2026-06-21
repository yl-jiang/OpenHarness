"""Hook event names supported by OpenHarness."""

from __future__ import annotations

from enum import Enum


class HookEvent(str, Enum):
    """Events that can trigger hooks."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    NOTIFICATION = "notification"
    STOP = "stop"
    SUBAGENT_STOP = "subagent_stop"
    # Goal-mode lifecycle events. Fire-and-forget; hook scripts run
    # asynchronously after each driver turn via GoalMode.flush_hooks().
    GOAL_CREATED = "goal_created"
    GOAL_RESUMED = "goal_resumed"
    GOAL_PAUSED = "goal_paused"
    GOAL_BLOCKED = "goal_blocked"
    GOAL_COMPLETED = "goal_completed"
    GOAL_CANCELLED = "goal_cancelled"
