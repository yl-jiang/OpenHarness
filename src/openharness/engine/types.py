"""Type definitions and enums for the query engine."""

from enum import Enum


class TaskFocusStateKey(str, Enum):
    """Field keys for the task_focus_state sub-dict inside tool metadata.

    Single source of truth for the task_focus_state schema.
    Use ``default_task_focus_state()`` to obtain a fresh default instance.
    """

    GOAL = "goal"
    RECENT_GOALS = "recent_goals"
    ACTIVE_ARTIFACTS = "active_artifacts"
    VERIFIED_STATE = "verified_state"
    NEXT_STEP = "next_step"


def default_task_focus_state() -> dict[str, object]:
    """Return a fresh default task_focus_state dict."""
    return {
        TaskFocusStateKey.GOAL: "",
        TaskFocusStateKey.RECENT_GOALS: [],
        TaskFocusStateKey.ACTIVE_ARTIFACTS: [],
        TaskFocusStateKey.VERIFIED_STATE: [],
        TaskFocusStateKey.NEXT_STEP: "",
    }


class ToolMetadataKey(str, Enum):
    """Keys for tool metadata storage in the query context.

    These keys are used to track various state and activity during query execution:
    - PERMISSION_MODE: Current permission mode setting
    - READ_FILE_STATE: History of file read operations
    - INVOKED_SKILLS: List of recently invoked skills
    - ASYNC_AGENT_STATE: State of async agent spawning activities
    - ASYNC_AGENT_TASKS: Details of spawned async agent tasks
    - RECENT_WORK_LOG: Recent work log entries
    - RECENT_VERIFIED_WORK: Verified work completed in recent interactions
    - TASK_FOCUS_STATE: Current task focus and related state
    - COMPACT_CHECKPOINTS: Conversation compaction checkpoints
    - COMPACT_LAST: Last compaction state
    - SELF_EVOLUTION_STATE: Background memory/skill review counters
    - SELF_EVOLUTION_CONTROLLER: Session-only self-evolution controller
    - CURRENT_*: Session-only runtime model/provider context
    """

    PERMISSION_MODE = "permission_mode"
    READ_FILE_STATE = "read_file_state"
    INVOKED_SKILLS = "invoked_skills"
    ASYNC_AGENT_STATE = "async_agent_state"
    ASYNC_AGENT_TASKS = "async_agent_tasks"
    RECENT_WORK_LOG = "recent_work_log"
    RECENT_VERIFIED_WORK = "recent_verified_work"
    TASK_FOCUS_STATE = "task_focus_state"
    COMPACT_CHECKPOINTS = "compact_checkpoints"
    COMPACT_LAST = "compact_last"
    SELF_EVOLUTION_STATE = "self_evolution_state"
    SELF_EVOLUTION_CONTROLLER = "self_evolution_controller"
    # Session-only runtime context: not persisted across sessions.
    CURRENT_MODEL = "current_model"
    CURRENT_PROVIDER = "current_provider"
    CURRENT_API_FORMAT = "current_api_format"
    CURRENT_BASE_URL = "current_base_url"
    CURRENT_ACTIVE_PROFILE = "current_active_profile"
    # Session-only cache: not persisted across sessions.
    FILE_READ_CACHE = "file_read_cache"
    TOOL_CALL_HISTORY = "tool_call_history"
    TOOL_NAME_REPAIR_NOTICES = "tool_name_repair_notices"
    VISION_MODEL_CONFIG = "vision_model_config"

    @classmethod
    def all_persisted_keys(cls) -> tuple["ToolMetadataKey", ...]:
        """Return all keys that should be persisted across sessions."""
        return (
            cls.PERMISSION_MODE,
            cls.READ_FILE_STATE,
            cls.INVOKED_SKILLS,
            cls.ASYNC_AGENT_STATE,
            cls.ASYNC_AGENT_TASKS,
            cls.RECENT_WORK_LOG,
            cls.RECENT_VERIFIED_WORK,
            cls.TASK_FOCUS_STATE,
            cls.COMPACT_CHECKPOINTS,
            cls.COMPACT_LAST,
            cls.SELF_EVOLUTION_STATE,
        )
