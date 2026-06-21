"""High-level conversation engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from openharness.api.client import SupportsStreamingMessages
from openharness.config.settings import Settings
from openharness.engine.cost_tracker import CostTracker
from openharness.coordinator.coordinator_mode import get_coordinator_user_context
from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, sanitize_conversation_messages
from openharness.engine.query import (
    AskUserPrompt,
    INTERNAL_DONE_REMINDER_PREFIX,
    INTERNAL_TOOL_NAME_REPAIR_PROMPT_PREFIX,
    INTERNAL_TRUNCATION_RECOVERY_PREFIX,
    MaxTurnsExceeded,
    PermissionPrompt,
    QueryContext,
    remember_user_goal,
    run_query,
)
from openharness.engine.stream_events import (
    AssistantTurnComplete,
    CompactProgressPhase,
    GoalChange,
    GoalChangeStats,
    GoalUpdatedEvent,
    StatusEvent,
    StreamEvent,
    StreamFinished,
)
from openharness.engine.types import ToolMetadataKey
from openharness.goal.injection import (
    GOAL_CONTINUATION_PROMPT,
    build_completion_summary_prompt,
    build_goal_reminder,
)
from openharness.goal.state import GOAL_MODE_KEY, GoalMode
from openharness.hooks import HookEvent, HookExecutor
from openharness.permissions.approvals import ApprovalCoordinator, ApprovalRequest, PromptFn
from openharness.permissions.checker import PermissionChecker
from openharness.services.autodream.service import schedule_auto_dream
from openharness.tools.base import ToolRegistry
from openharness.utils.log import get_logger

_INTERNAL_AUTO_CONTINUE_PROMPT = (
    "<openharness-internal:auto-continue>\n"
    "The previous assistant turn ended without a user-visible result after tool work. "
    "Continue working on the current request. Do not stop silently; either use more tools, "
    "provide a concise final result, or explain clearly what blocks completion."
)
_AUTO_CONTINUE_STATUS_MESSAGE = (
    "Assistant stopped after tool work without a visible result; continuing automatically."
)
# How many *consecutive* silent stops without any meaningful progress are
# allowed before giving up.  If the model makes tool calls or produces visible
# text between two silent stops, that counts as progress and the consecutive
# counter resets to zero.
_MAX_CONSECUTIVE_SILENT_STOPS = 1
# Hard absolute ceiling across the whole guard loop, regardless of progress,
# to prevent runaway loops on a pathological model.
_MAX_AUTO_CONTINUE_ABSOLUTE = 5

logger = get_logger(__name__)


@dataclass(frozen=True)
class QueryTurnCheckpoint:
    """Rollback snapshot for one in-flight user turn."""

    messages: list[ConversationMessage]
    export_messages: list[ConversationMessage]
    system_prompt: str
    model: str
    max_turns: int | None
    tool_metadata: dict[str, object]


def _clone_message_list(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    return [message.model_copy(deep=True) for message in messages]


def _clone_turn_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clone_turn_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone_turn_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone_turn_value(item) for item in value)
    if isinstance(value, set):
        return {_clone_turn_value(item) for item in value}
    model_copy = getattr(value, "model_copy", None)
    if callable(model_copy):
        try:
            return model_copy(deep=True)
        except TypeError:
            pass
    return value


class QueryEngine:
    """Owns conversation history and the tool-aware model loop."""

    def __init__(
        self,
        *,
        api_client: SupportsStreamingMessages,
        tool_registry: ToolRegistry,
        permission_checker: PermissionChecker,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        max_tokens: int = 4096,
        context_window_tokens: int | None = None,
        auto_compact_threshold_tokens: int | None = None,
        max_turns: int | None = 8,
        permission_prompt: PermissionPrompt | None = None,
        ask_user_prompt: AskUserPrompt | None = None,
        hook_executor: HookExecutor | None = None,
        tool_metadata: dict[str, object] | None = None,
        require_explicit_done: bool = False,
        settings: Settings | None = None,
        approval_coordinator: ApprovalCoordinator | None = None,
    ) -> None:
        self._api_client = api_client
        self._tool_registry = tool_registry
        self._permission_checker = permission_checker
        self._cwd = Path(cwd).resolve()
        self._model = model
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._context_window_tokens = context_window_tokens
        self._auto_compact_threshold_tokens = auto_compact_threshold_tokens
        self._max_turns = max_turns
        self._permission_prompt = permission_prompt
        self._ask_user_prompt = ask_user_prompt
        self._hook_executor = hook_executor
        self._tool_metadata = tool_metadata or {}
        self._require_explicit_done = require_explicit_done
        self._settings = settings
        self._messages: list[ConversationMessage] = []
        self._export_messages: list[ConversationMessage] = []
        self._cost_tracker = CostTracker()
        # Cached pre-goal permission mode; populated by _drive_goal right
        # before clear_after_complete() so _maybe_restore_permission can read
        # it after the goal record is gone. None means "nothing cached".
        self._cached_original_permission: str | None = None

        if approval_coordinator is not None:
            self._approval_coordinator: ApprovalCoordinator = approval_coordinator
        else:
            self._approval_coordinator = self._make_default_coordinator(
                permission_checker, permission_prompt, hook_executor
            )

    @staticmethod
    def _make_default_coordinator(
        checker: PermissionChecker,
        permission_prompt: PermissionPrompt | None,
        hook_executor: HookExecutor | None,
    ) -> ApprovalCoordinator:
        async def _notify_hook(request: ApprovalRequest) -> None:
            if hook_executor is not None and request.kind == "tool":
                await hook_executor.execute(
                    HookEvent.NOTIFICATION,
                    {
                        "event": HookEvent.NOTIFICATION.value,
                        "notification_type": "permission_prompt",
                        "tool_name": request.tool_name,
                        "reason": request.reason,
                    },
                )

        prompt_fn: PromptFn | None = None
        if permission_prompt is not None:
            _perm = permission_prompt

            async def _legacy_prompt(request: ApprovalRequest) -> str:
                return await _perm(request.tool_name, request.reason)  # type: ignore[arg-type]

            prompt_fn = _legacy_prompt

        return ApprovalCoordinator(
            checker,
            prompt_fn=prompt_fn,
            notify_fn=_notify_hook,
        )

    @property
    def messages(self) -> list[ConversationMessage]:
        """Return the current conversation history."""
        return list(self._messages)

    @property
    def export_messages(self) -> list[ConversationMessage]:
        """Return the full exportable session history, preserved across compaction."""
        return list(self._export_messages or self._messages)

    @property
    def max_turns(self) -> int | None:
        """Return the maximum number of agentic turns per user input, if capped."""
        return self._max_turns

    @property
    def api_client(self) -> SupportsStreamingMessages:
        """Return the active API client."""
        return self._api_client

    @property
    def model(self) -> str:
        """Return the active model identifier."""
        return self._model

    @property
    def system_prompt(self) -> str:
        """Return the active system prompt."""
        return self._system_prompt

    @property
    def tool_metadata(self) -> dict[str, object]:
        """Return the mutable tool metadata/carry-over state."""
        return self._tool_metadata

    @property
    def total_usage(self):
        """Return the total usage across all turns."""
        return self._cost_tracker.total

    def _turn_private_metadata_keys(self) -> set[str]:
        """Return ephemeral per-turn metadata stored under private ``_...`` keys."""

        return {key for key in self._tool_metadata if key.startswith("_")}

    def _turn_checkpoint_metadata_keys(self) -> set[str]:
        """Return all metadata keys that should roll back with the current turn."""

        explicit_turn_keys = {key.value for key in ToolMetadataKey.turn_checkpoint_keys()}
        private_turn_keys = self._turn_private_metadata_keys()
        return explicit_turn_keys | private_turn_keys

    def capture_turn_checkpoint(self) -> QueryTurnCheckpoint:
        """Capture the rollback state for the current user turn.

        Cancellation should discard turn-local conversational state without
        disturbing long-lived runtime handles stored in ``tool_metadata``.
        """

        metadata_keys = self._turn_checkpoint_metadata_keys()
        return QueryTurnCheckpoint(
            messages=_clone_message_list(self._messages),
            export_messages=_clone_message_list(self._export_messages),
            system_prompt=self._system_prompt,
            model=self._model,
            max_turns=self._max_turns,
            tool_metadata={
                key: _clone_turn_value(self._tool_metadata[key])
                for key in metadata_keys
                if key in self._tool_metadata
            },
        )

    def restore_turn_checkpoint(self, checkpoint: QueryTurnCheckpoint) -> None:
        """Restore a previously captured turn checkpoint."""

        self._messages = _clone_message_list(checkpoint.messages)
        self._export_messages = _clone_message_list(checkpoint.export_messages)
        self._system_prompt = checkpoint.system_prompt
        self._model = checkpoint.model
        self._max_turns = checkpoint.max_turns

        turn_scoped_keys = self._turn_checkpoint_metadata_keys()
        for key in turn_scoped_keys:
            self._tool_metadata.pop(key, None)
        for key, value in checkpoint.tool_metadata.items():
            self._tool_metadata[key] = _clone_turn_value(value)

    def clear(self) -> None:
        """Clear the in-memory conversation history."""
        self._messages.clear()
        self._export_messages.clear()
        self._cost_tracker = CostTracker()

    def set_system_prompt(self, prompt: str) -> None:
        """Update the active system prompt for future turns."""
        self._system_prompt = prompt

    def set_model(self, model: str) -> None:
        """Update the active model for future turns."""
        self._model = model

    def set_api_client(self, api_client: SupportsStreamingMessages) -> None:
        """Update the active API client for future turns."""
        self._api_client = api_client

    def set_max_turns(self, max_turns: int | None) -> None:
        """Update the maximum number of agentic turns per user input."""
        self._max_turns = None if max_turns is None else max(1, int(max_turns))

    def set_permission_checker(self, checker: PermissionChecker) -> None:
        """Update the active permission checker for future turns."""
        self._permission_checker = checker
        self._approval_coordinator.set_checker(checker)

    @property
    def approval_coordinator(self) -> ApprovalCoordinator:
        """Expose the approval coordinator for out-of-loop tool authorization."""
        return self._approval_coordinator

    async def authorize_tool(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
    ):
        """Run an approval check for a tool invocation outside the main loop.

        Returns a :class:`PermissionDecision`.  Callers must respect the
        ``allowed`` flag; the coordinator already prompts the user when
        ``requires_confirmation`` is set.
        """
        return await self._approval_coordinator.authorize_tool(
            tool_name,
            is_read_only=is_read_only,
            file_path=file_path,
            command=command,
        )

    def inject_user_message(self, text: str) -> None:
        """Append a user message without triggering a model turn.

        Used by user-initiated shell commands (``!cmd``) so the resulting
        output is visible to the next model turn and persists to session
        export history.

        Consecutive injections are merged into the trailing user message to
        preserve the provider-required user/assistant alternation.
        """
        self._messages = sanitize_conversation_messages(self._messages)
        if self._messages and self._messages[-1].role == "user":
            last = self._messages[-1]
            merged_content = list(last.content) + [TextBlock(text="\n\n" + text)]
            self._messages[-1] = ConversationMessage(role="user", content=merged_content)
        else:
            self._messages.append(ConversationMessage.from_user_text(text))
        self.capture_export_checkpoint(self._messages)

    def set_require_explicit_done(self, value: bool) -> None:
        """Update whether the agent loop requires an explicit done() call to terminate."""
        self._require_explicit_done = value

    def _build_coordinator_context_message(self) -> ConversationMessage | None:
        """Build a synthetic user message carrying coordinator runtime context."""
        context = get_coordinator_user_context()
        worker_tools_context = context.get("workerToolsContext")
        if not worker_tools_context:
            return None
        return ConversationMessage(
            role="user",
            content=[TextBlock(text=f"# Coordinator User Context\n\n{worker_tools_context}")],
        )

    @staticmethod
    def _is_internal_message(message: ConversationMessage) -> bool:
        if message.role != "user":
            return False
        text = message.text
        return (
            text == _INTERNAL_AUTO_CONTINUE_PROMPT
            or text.startswith(INTERNAL_TOOL_NAME_REPAIR_PROMPT_PREFIX)
            or text.startswith(INTERNAL_DONE_REMINDER_PREFIX)
            or text.startswith(INTERNAL_TRUNCATION_RECOVERY_PREFIX)
            or text.startswith("# Coordinator User Context\n\n")
        )

    @classmethod
    def _public_messages(cls, messages: list[ConversationMessage]) -> list[ConversationMessage]:
        """Filter out internal-only messages that should not be exposed to render_event handlers."""
        return [message for message in messages if not cls._is_internal_message(message)]

    def capture_export_checkpoint(self, messages: list[ConversationMessage] | None = None) -> None:
        """Merge the current public history into the persistent export history."""
        current = self._public_messages(messages if messages is not None else self._messages)
        if not current:
            return
        if not self._export_messages:
            self._export_messages = list(current)
            return
        if current == self._export_messages:
            return
        if len(current) >= len(self._export_messages) and current[: len(self._export_messages)] == self._export_messages:
            self._export_messages = list(current)
            return

        max_overlap = min(len(self._export_messages), len(current))
        for overlap in range(max_overlap, 0, -1):
            suffix = self._export_messages[-overlap:]
            for start in range(0, len(current) - overlap + 1):
                if current[start : start + overlap] == suffix:
                    self._export_messages.extend(current[start + overlap :])
                    return

        self._export_messages = list(current)

    @staticmethod
    def _should_auto_continue_after_silent_stop(messages: list[ConversationMessage]) -> bool:
        if len(messages) < 2:
            return False
        last = messages[-1]
        if last.role != "assistant" or last.tool_uses or last.text.strip():
            return False
        previous = messages[-2]
        if previous.role != "user":
            return False
        if not any(isinstance(block, ToolResultBlock) for block in previous.content):
            return False
        for message in reversed(messages[:-2]):
            if message.role != "assistant":
                continue
            return bool(message.tool_uses)
        return False

    async def _stream_query_with_guards(
        self,
        *,
        context: QueryContext,
        query_messages: list[ConversationMessage],
    ) -> AsyncIterator[StreamEvent]:
        # Counts consecutive silent stops that occurred *without* meaningful
        # progress between them.  Resets whenever the model does tool work
        # or outputs visible text between two silent stops.
        consecutive_silent_stops = 0
        # Hard absolute ceiling across the entire guard loop.
        total_auto_continues = 0

        while True:
            pending_turn_complete: AssistantTurnComplete | None = None
            # Track whether a meaningful (non-empty) AssistantTurnComplete was
            # yielded during this run_query call, *before* the final pending one.
            # A tool-use turn is always meaningful; it means the model is making
            # progress even if the final stop in this loop iteration is silent.
            progress_in_this_run = False

            def _is_meaningful(turn: AssistantTurnComplete) -> bool:
                return bool(turn.message.tool_uses or turn.message.text.strip())

            try:
                async for event, usage in run_query(context, query_messages):  # this loop only pass throughs events from run_query
                    if usage is not None:
                        self._cost_tracker.add(usage)
                    if getattr(event, "phase", None) in CompactProgressPhase.start_phases():
                        self.capture_export_checkpoint(query_messages)
                    if pending_turn_complete is not None and not isinstance(event, AssistantTurnComplete):
                        if _is_meaningful(pending_turn_complete):
                            progress_in_this_run = True
                        self._messages = self._public_messages(query_messages)
                        self.capture_export_checkpoint(self._messages)
                        yield pending_turn_complete   # yield the pending AssistantTurnComplete before yielding the next event, which is not an AssistantTurnComplete
                        pending_turn_complete = None
                    if isinstance(event, AssistantTurnComplete):
                        logger.event(
                            "assistant_turn_complete",
                            session_id=self._tool_metadata.get("session_id"),
                            text_length=len(event.message.text),
                            tool_use_count=len(event.message.tool_uses),
                            message_count=len(query_messages),
                        )
                        self._notify_self_evolution_assistant_turn(event.message)
                        if pending_turn_complete is not None:
                            if _is_meaningful(pending_turn_complete):
                                progress_in_this_run = True
                            self._messages = self._public_messages(query_messages)
                            yield pending_turn_complete
                        pending_turn_complete = event  # store the normal AssistantTurnComplete from `run_query`
                        continue
                    yield event  # normal non-AssistantTurnComplete event, just yield it right away
            except MaxTurnsExceeded as exc:
                logger.event(
                    "max_turns_exceeded",
                    session_id=self._tool_metadata.get("session_id"),
                    max_turns=exc.max_turns,
                    message_count=len(query_messages),
                )
                self._messages = self._public_messages(query_messages)
                self.capture_export_checkpoint(self._messages)
                # Surface the max-turns message as a status event so any render_event
                # handler (TUI and headless alike) can display it.
                yield StatusEvent(message=f"Stopped after {exc.max_turns} turns (max_turns limit reached).")
                yield StreamFinished(
                    reason="max_turns_exceeded",
                    detail=f"Stopped after {exc.max_turns} turns (max_turns limit reached)",
                )
                return

            if pending_turn_complete is not None:
                matched_silent_stop = self._should_auto_continue_after_silent_stop(query_messages)
                logger.event(
                    "silent_stop_check",
                    session_id=self._tool_metadata.get("session_id"),
                    matched=matched_silent_stop,
                    consecutive_silent_stops=consecutive_silent_stops,
                    total_auto_continues=total_auto_continues,
                    progress_in_this_run=progress_in_this_run,
                    last_text_length=len(pending_turn_complete.message.text),
                    last_tool_use_count=len(pending_turn_complete.message.tool_uses),
                    message_count=len(query_messages),
                )
                if matched_silent_stop:
                    # If the model made meaningful progress in this run (earlier
                    # tool-use turns), treat this as the first consecutive silent
                    # stop and reset the counter.
                    if progress_in_this_run:
                        consecutive_silent_stops = 0
                    consecutive_silent_stops += 1

                    can_continue = (
                        consecutive_silent_stops <= _MAX_CONSECUTIVE_SILENT_STOPS
                        and total_auto_continues < _MAX_AUTO_CONTINUE_ABSOLUTE
                    )
                    if can_continue:
                        total_auto_continues += 1
                        query_messages.pop()
                        query_messages.append(ConversationMessage.from_user_text(_INTERNAL_AUTO_CONTINUE_PROMPT))
                        self._messages = self._public_messages(query_messages)
                        self.capture_export_checkpoint(self._messages)
                        logger.event(
                            "auto_continue_triggered",
                            session_id=self._tool_metadata.get("session_id"),
                            attempt=total_auto_continues,
                            consecutive_silent_stops=consecutive_silent_stops,
                            message_count=len(query_messages),
                        )
                        yield StatusEvent(message=_AUTO_CONTINUE_STATUS_MESSAGE)
                        continue

                self._messages = self._public_messages(query_messages)
                self.capture_export_checkpoint(self._messages)
                yield pending_turn_complete
                if matched_silent_stop and not can_continue:
                    logger.event(
                        "auto_continue_exhausted",
                        session_id=self._tool_metadata.get("session_id"),
                        consecutive_silent_stops=consecutive_silent_stops,
                        total_auto_continues=total_auto_continues,
                        message_count=len(query_messages),
                    )
                    yield StreamFinished(reason="auto_continue_exhausted")
            else:
                self._messages = self._public_messages(query_messages)
                self.capture_export_checkpoint(self._messages)
            return

    def load_messages(
        self,
        messages: list[ConversationMessage],
        *,
        preserve_export_history: bool = False,
    ) -> None:
        """Replace the in-memory conversation history."""
        self._messages = list(messages)
        if preserve_export_history:
            if not self._export_messages:
                self._export_messages = list(self._public_messages(messages))
            return
        self._export_messages = list(messages)

    def _schedule_auto_dream(self) -> None:
        """Fire-and-forget background memory consolidation after a user turn."""
        if self._settings is None:
            return
        context = self._tool_metadata.get("autodream_context")
        kwargs = dict(context) if isinstance(context, dict) else {}
        resolution = self._tool_metadata.get(ToolMetadataKey.UTILITY_CLIENT_RESOLUTION.value)
        utility_model = resolution.model if resolution else None
        schedule_auto_dream(
            cwd=self._cwd,
            settings=self._settings,
            model=utility_model or self._model,
            current_session_id=str(self._tool_metadata.get("session_id") or ""),
            **kwargs,
        )

    def _prepare_session_memory(self) -> None:
        """Expose file-backed session memory to compaction when enabled."""

        if self._settings is None or not self._settings.memory.session_memory_enabled:
            return
        if not self._settings.memory.enabled:
            return
        from openharness.services.session_memory import prepare_session_memory_metadata

        prepare_session_memory_metadata(
            self._cwd,
            self._tool_metadata,
            session_id=str(self._tool_metadata.get("session_id") or "default"),
        )

    async def _update_session_memory(self) -> None:
        """Persist a session checkpoint after a user turn."""

        if self._settings is None or not self._settings.memory.session_memory_enabled:
            return
        if not self._settings.memory.enabled:
            return
        from openharness.services.session_memory import update_session_memory_file

        update_session_memory_file(
            self._cwd,
            list(self._messages),
            tool_metadata=self._tool_metadata,
            session_id=str(self._tool_metadata.get("session_id") or "default"),
        )

    async def _extract_durable_memories(self) -> None:
        """Run the optional durable memory extraction pass."""

        if self._settings is None or not self._settings.memory.auto_extract_enabled:
            return
        if not self._settings.memory.enabled:
            return
        from openharness.services.memory_extract import extract_memories_from_turn

        resolution = self._tool_metadata.get(ToolMetadataKey.UTILITY_CLIENT_RESOLUTION.value)
        try:
            result = await extract_memories_from_turn(
                cwd=self._cwd,
                api_client=resolution.api_client if resolution else self._api_client,
                model=resolution.model if resolution else self._model,
                messages=list(self._messages),
                max_records=self._settings.memory.auto_extract_max_records,
            )
        except Exception as exc:
            logger.warning("Durable memory extraction failed: %s", exc, exc_info=True)
            self._tool_metadata["memory_extract_last_error"] = str(exc)
            return
        self._tool_metadata["memory_extract_last"] = {
            "skipped": result.skipped,
            "reason": result.reason,
            "written_paths": [str(path) for path in result.written_paths],
        }

    def _schedule_extract_memories(self) -> None:
        """Fire-and-forget durable memory extraction after a user turn."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._extract_durable_memories())

    def has_pending_continuation(self) -> bool:
        """Return True when the conversation ends with tool results awaiting a follow-up model turn."""
        if not self._messages:
            return False
        last = self._messages[-1]
        if last.role != "user":
            return False
        if not any(isinstance(block, ToolResultBlock) for block in last.content):
            return False
        for msg in reversed(self._messages[:-1]):
            if msg.role != "assistant":
                continue
            return bool(msg.tool_uses)
        return False

    async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[StreamEvent]:
        """Append a user message and execute the query loop."""
        user_message = (
            prompt
            if isinstance(prompt, ConversationMessage)
            else ConversationMessage.from_user_text(prompt)
        )
        logger.event(
            "submit_message_start",
            session_id=self._tool_metadata.get("session_id"),
            prompt_length=len(user_message.text),
            message_count_before=len(self._messages),
        )
        if user_message.text.strip() and not self._tool_metadata.pop("_suppress_next_user_goal", False):
            remember_user_goal(self._tool_metadata, user_message.text)
        self._prepare_session_memory()
        self._messages = sanitize_conversation_messages(self._messages)
        self._begin_self_evolution_user_turn()
        self._messages.append(user_message)
        self.capture_export_checkpoint(self._messages)
        if self._hook_executor is not None:
            await self._hook_executor.execute(
                HookEvent.USER_PROMPT_SUBMIT,
                {
                    "event": HookEvent.USER_PROMPT_SUBMIT.value,
                    "prompt": user_message.text,
                },
            )
        context = QueryContext(
            api_client=self._api_client,
            tool_registry=self._tool_registry,
            permission_checker=self._permission_checker,
            cwd=self._cwd,
            model=self._model,
            system_prompt=self._system_prompt,
            max_tokens=self._max_tokens,
            context_window_tokens=self._context_window_tokens,
            auto_compact_threshold_tokens=self._auto_compact_threshold_tokens,
            max_turns=self._max_turns,
            permission_prompt=self._permission_prompt,
            ask_user_prompt=self._ask_user_prompt,
            hook_executor=self._hook_executor,
            tool_metadata=self._tool_metadata,
            require_explicit_done=self._require_explicit_done,
            approval_coordinator=self._approval_coordinator,
        )
        query_messages = list(self._messages)
        coordinator_context = self._build_coordinator_context_message()
        if coordinator_context is not None:
            query_messages.append(coordinator_context)
        # Route to the goal driver when a durable goal is active. The goal
        # branch reuses the same QueryContext (hooks + memory are finalized by
        # the surrounding try/finally in submit_message, not inside _drive_goal).
        goal_mode = self._tool_metadata.get(GOAL_MODE_KEY)
        active_goal = goal_mode.get_active_goal() if isinstance(goal_mode, GoalMode) else None
        try:
            if active_goal is not None:
                async for event in self._drive_goal(
                    context=context,
                    query_messages=query_messages,
                    initial_snapshot=active_goal,
                ):
                    yield event
            else:
                async for event in self._stream_query_with_guards(context=context, query_messages=query_messages):
                    yield event
                async for event in self._maybe_handoff_new_goal(context, query_messages):
                    yield event
        finally:
            await self._update_session_memory()
            self._schedule_extract_memories()
            self._schedule_auto_dream()
        self._maybe_spawn_self_evolution_review(
            user_message.text,
            messages_snapshot=self._public_messages(query_messages),
        )
        logger.event(
            "submit_message_end",
            session_id=self._tool_metadata.get("session_id"),
            message_count_after=len(self._messages),
        )

    async def _maybe_handoff_new_goal(
        self,
        context: QueryContext,
        query_messages: list[ConversationMessage],
    ) -> AsyncIterator[StreamEvent]:
        """Check for a goal created mid-turn and route to the driver.

        ``post_tool_stage`` stops the turn when ``create_goal`` is called.
        After ``_stream_query_with_guards`` returns, this method picks up
        the newly active goal and delegates to ``_drive_goal``.
        """
        goal_mode = self._tool_metadata.get(GOAL_MODE_KEY)
        if not isinstance(goal_mode, GoalMode):
            return
        new_goal = goal_mode.get_active_goal()
        if new_goal is None:
            return
        async for event in self._drive_goal(
            context=context,
            query_messages=query_messages,
            initial_snapshot=new_goal,
        ):
            yield event

    async def _drive_goal(
        self,
        *,
        context: QueryContext,
        query_messages: list[ConversationMessage],
        initial_snapshot,
    ) -> AsyncIterator[StreamEvent]:
        """Multi-turn driver for an active goal.

        Called from ``submit_message`` when an active goal exists. Each
        iteration:

        1. Pre-checks budget (blocks immediately if exceeded).
        2. Increments the turn counter.
        3. Injects a goal reminder (+ continuation prompt on turns > 1).
        4. Runs one turn via ``_stream_query_with_guards``, tracking token use.
        5. Post-checks status changes made by ``UpdateGoal``:
           - ``complete``/``blocked``: inject summary/blocked prompt, run a
             final turn for the model to write the response, clear state.
           - ``paused``/cancel: exit without further turns.
        6. Post-checks budget again.
        7. If still ``active``, loop (injecting a continuation prompt on the
           next iteration). The driver does NOT auto-pause — the model must
           explicitly call UpdateGoal to stop.

        ``CancelledError`` (Ctrl+C, task cancel) pauses the goal and re-raises.
        """
        goal_mode = self._tool_metadata.get(GOAL_MODE_KEY)
        if not isinstance(goal_mode, GoalMode):
            # Defensive: caller routed here but GoalMode is gone. Fall through
            # to a single regular turn so the user's message still gets answered.
            async for event in self._stream_query_with_guards(
                context=context, query_messages=query_messages
            ):
                yield event
            return

        # Announce the start of goal-driven work.
        yield GoalUpdatedEvent(
            snapshot=goal_mode.get_goal(),
            change=GoalChange(
                kind="lifecycle",
                status="active",
                actor=initial_snapshot.last_actor or "user",
            ),
        )

        hard_cap_iterations = 200  # defensive safety net across the entire queue
        total_iterations = 0
        is_first_turn_of_current_goal = True

        while True:
            # 1. Budget pre-check.
            goal = goal_mode.get_goal()
            if goal is None:
                return
            if goal.status != "active":
                return
            if goal.budget.over_budget:
                snapshot = goal_mode.mark_blocked(
                    reason="A configured budget was reached", actor="runtime"
                )
                yield GoalUpdatedEvent(
                    snapshot=snapshot,
                    change=GoalChange(
                        kind="lifecycle",
                        status="blocked",
                        reason="budget reached",
                        actor="runtime",
                    ),
                )
                # Blocked by budget: never auto-advance (user should inspect).
                self._maybe_restore_permission()
                return

            # 2. Count this turn.
            goal_mode.increment_turn()
            total_iterations += 1
            if total_iterations >= hard_cap_iterations:
                # Hit the hard cap: block rather than loop forever.
                snapshot = goal_mode.mark_blocked(
                    reason="Driver iteration cap reached", actor="runtime"
                )
                yield GoalUpdatedEvent(
                    snapshot=snapshot,
                    change=GoalChange(
                        kind="lifecycle",
                        status="blocked",
                        reason="driver iteration cap",
                        actor="runtime",
                    ),
                )
                self._maybe_restore_permission()
                return

            # 3. Inject reminder + continuation. On the first turn of the
            # current goal the user's original input is already the trailing
            # user message (added by submit_message); we only prepend a
            # reminder. On subsequent turns we inject a fresh user message
            # carrying both.
            reminder = build_goal_reminder(goal_mode.get_goal())
            if is_first_turn_of_current_goal:
                if reminder:
                    self.inject_user_message(reminder)
                    query_messages = list(self._messages)
            else:
                if reminder:
                    combined = f"{reminder}\n\n{GOAL_CONTINUATION_PROMPT}"
                else:
                    combined = GOAL_CONTINUATION_PROMPT
                continuation = ConversationMessage.from_user_text(combined)
                self._messages.append(continuation)
                self.capture_export_checkpoint(self._messages)
                query_messages = list(self._messages)

            # 4. Run one turn. Track token use via AssistantTurnComplete.
            turn_was_cancelled = False
            try:
                async for event in self._stream_query_with_guards(
                    context=context, query_messages=query_messages
                ):
                    yield event
                    if isinstance(event, AssistantTurnComplete):
                        try:
                            total_tokens = int(event.usage.total_tokens or 0)
                        except (TypeError, AttributeError):
                            total_tokens = 0
                        if total_tokens > 0:
                            goal_mode.record_token_usage(total_tokens)
                        # Sync the local view with whatever run_query mutated.
                        query_messages = list(self._messages)
                        # Emit a stats-refresh event immediately so the frontend
                        # sees updated turns/tokens during long auto_continue
                        # sequences. Without this, the refresh at step 7 only
                        # fires after _stream_query_with_guards returns, which
                        # may be many sub-turns later.
                        yield GoalUpdatedEvent(snapshot=goal_mode.get_goal(), change=None)
            except asyncio.CancelledError:
                # Ctrl+C / task cancel: pause (don't lose state), then re-raise.
                turn_was_cancelled = True
                goal_mode.pause_goal(
                    reason="Paused after interruption", actor="runtime"
                )
                yield GoalUpdatedEvent(
                    snapshot=goal_mode.get_goal(),
                    change=GoalChange(
                        kind="lifecycle",
                        status="paused",
                        reason="Paused after interruption",
                        actor="runtime",
                    ),
                )
                raise

            # Fire-and-forget hook flush: any GOAL_* events enqueued during
            # this turn (by tool calls or by the cancel branch above) are
            # dispatched now. Failures are logged but do not abort the driver.
            await goal_mode.flush_hooks()

            is_first_turn_of_current_goal = False

            # 5. React to status changes.
            goal = goal_mode.get_goal()
            if goal is None:
                # Cancelled while we were running.
                self._maybe_restore_permission()
                return

            if goal.status == "complete":
                # Inject a summary prompt and run one last turn for the model
                # to compose the completion reply. The record still carries
                # status="complete" — clear it after the summary turn.
                summary_prompt = build_completion_summary_prompt(goal)
                summary_message = ConversationMessage.from_user_text(summary_prompt)
                self._messages.append(summary_message)
                self.capture_export_checkpoint(self._messages)
                summary_messages = list(self._messages)
                async for event in self._stream_query_with_guards(
                    context=context, query_messages=summary_messages
                ):
                    yield event
                yield GoalUpdatedEvent(
                    snapshot=goal,
                    change=GoalChange(
                        kind="completion",
                        status="complete",
                        reason=goal.terminal_reason,
                        actor=goal.last_actor,
                        stats=GoalChangeStats(
                            turns_used=goal.turns_used,
                            tokens_used=goal.tokens_used,
                            wall_clock_ms=goal.wall_clock_ms,
                        ),
                    ),
                )
                # Snapshot the original permission mode BEFORE clear_after_complete
                # wipes the state.
                self._cached_original_permission = goal_mode.original_permission_mode()
                goal_mode.clear_after_complete()
                yield GoalUpdatedEvent(snapshot=None, change=None)

                # Advance queue (kimi-code alignment): complete always promotes.
                promoted = self._maybe_promote_queued_goal(goal_mode)
                if promoted is not None:
                    # Discard the pending permission-restore signal: we are
                    # continuing within the same submit_message.
                    self._tool_metadata.pop("_pending_permission_restore", None)
                    self._cached_original_permission = None
                    is_first_turn_of_current_goal = True
                    continue
                self._maybe_restore_permission()
                return

            if goal.status == "blocked":
                yield GoalUpdatedEvent(
                    snapshot=goal,
                    change=GoalChange(
                        kind="lifecycle",
                        status="blocked",
                        reason=goal.terminal_reason,
                        actor=goal.last_actor,
                    ),
                )
                # Default: blocked does NOT auto-advance — user should inspect.
                # Opt-in via settings.goal.auto_advance_on_blocked.
                settings = self._settings
                auto_advance = bool(
                    settings
                    and getattr(getattr(settings, "goal", None), "auto_advance_on_blocked", False)
                )
                if auto_advance:
                    promoted = self._maybe_promote_queued_goal(goal_mode)
                    if promoted is not None:
                        self._tool_metadata.pop("_pending_permission_restore", None)
                        self._cached_original_permission = None
                        is_first_turn_of_current_goal = True
                        continue
                self._maybe_restore_permission()
                return

            if goal.status != "active" or turn_was_cancelled:
                # Paused or cancelled — exit the driver.
                yield GoalUpdatedEvent(
                    snapshot=goal,
                    change=GoalChange(
                        kind="lifecycle",
                        status=goal.status,
                        reason=goal.terminal_reason,
                        actor=goal.last_actor,
                    ),
                )
                self._maybe_restore_permission()
                return

            # 6. Budget post-check.
            if goal.budget.over_budget:
                snapshot = goal_mode.mark_blocked(
                    reason="A configured budget was reached", actor="runtime"
                )
                yield GoalUpdatedEvent(
                    snapshot=snapshot,
                    change=GoalChange(
                        kind="lifecycle",
                        status="blocked",
                        reason="budget reached",
                        actor="runtime",
                    ),
                )
                self._maybe_restore_permission()
                return

            # 7. Still active — emit a stats-refresh event so the frontend
            # sees updated turns/tokens/elapsed before the next continuation.
            yield GoalUpdatedEvent(snapshot=goal_mode.get_goal(), change=None)

    def _maybe_promote_queued_goal(self, goal_mode: GoalMode):
        """Pop the next queued goal and create it. Returns the new snapshot or None.

        The runtime ``GoalQueueStore`` handle lives at ``"goal_queue"``; the
        serialized persistence data lives at ``GOAL_QUEUE_KEY``
        (``"goal_queue_state"``). We read the runtime handle here.
        """
        queue = self._tool_metadata.get("goal_queue")
        if queue is None:
            # Lazy-construct an empty queue store so duck-typing inside
            # start_next_from_queue has a consistent surface.
            from openharness.goal.queue import GoalQueueStore

            queue = GoalQueueStore(self._tool_metadata)
            self._tool_metadata["goal_queue"] = queue
        return goal_mode.start_next_from_queue(queue)

    def _maybe_restore_permission(self) -> None:
        """Signal runtime to restore the pre-goal permission mode, if opted in.

        The signal lives in ``tool_metadata["_pending_permission_restore"]``
        (underscore prefix → turn-private, auto-rolled-back on cancel). The
        actual permission switch happens in ``runtime.py`` after
        ``submit_message`` returns, so it doesn't interact with the driver's
        in-flight turn.
        """
        settings = self._settings
        if settings is None:
            return
        goal_cfg = getattr(settings, "goal", None)
        if goal_cfg is None or not getattr(
            goal_cfg, "restore_permission_after_goal", False
        ):
            return
        # Prefer the cached value (captured right before clear_after_complete
        # wiped the goal record). Fall back to the live GoalMode state for
        # the other exit points (blocked / paused / cancelled) where the
        # goal record is still around.
        original = self._cached_original_permission
        self._cached_original_permission = None
        if original is None:
            goal_mode = self._tool_metadata.get(GOAL_MODE_KEY)
            if isinstance(goal_mode, GoalMode):
                original = goal_mode.original_permission_mode()
        if not original:
            return
        from openharness.permissions import PermissionMode

        if original == PermissionMode.FULL_AUTO.value:
            # No point restoring FULL_AUTO — the user is already there.
            return
        self._tool_metadata["_pending_permission_restore"] = original

    def _self_evolution_controller(self):
        controller = self._tool_metadata.get(ToolMetadataKey.SELF_EVOLUTION_CONTROLLER.value)
        if controller is None:
            return None
        required = ("begin_user_turn", "observe_assistant_turn", "maybe_spawn_review")
        if not all(hasattr(controller, name) for name in required):
            logger.debug("Ignoring invalid self-evolution controller: %r", controller)
            return None
        return controller

    def _begin_self_evolution_user_turn(self) -> None:
        controller = self._self_evolution_controller()
        if controller is None:
            return
        controller.begin_user_turn(
            self._tool_metadata,
            memory_tool_available=self._tool_registry.get("memory") is not None,
            skill_tool_available=self._tool_registry.get("skill_load") is not None,
        )

    def _notify_self_evolution_assistant_turn(self, message: ConversationMessage) -> None:
        controller = self._self_evolution_controller()
        if controller is None:
            return
        controller.observe_assistant_turn(self._tool_metadata, message)

    def _maybe_spawn_self_evolution_review(
        self,
        latest_user_prompt: str,
        *,
        messages_snapshot: list[ConversationMessage] | None = None,
    ) -> None:
        controller = self._self_evolution_controller()
        if controller is None:
            return
        controller.maybe_spawn_review(
            self._tool_metadata,
            list(messages_snapshot if messages_snapshot is not None else self._messages),
            latest_user_prompt=latest_user_prompt,
        )

    async def continue_pending(self, *, max_turns: int | None = None) -> AsyncIterator[StreamEvent]:
        """Continue an interrupted tool loop without appending a new user message."""
        self._prepare_session_memory()
        self._messages = sanitize_conversation_messages(self._messages)
        logger.event(
            "continue_pending_start",
            session_id=self._tool_metadata.get("session_id"),
            max_turns=max_turns if max_turns is not None else self._max_turns,
            message_count_before=len(self._messages),
        )
        context = QueryContext(
            api_client=self._api_client,
            tool_registry=self._tool_registry,
            permission_checker=self._permission_checker,
            cwd=self._cwd,
            model=self._model,
            system_prompt=self._system_prompt,
            max_tokens=self._max_tokens,
            context_window_tokens=self._context_window_tokens,
            auto_compact_threshold_tokens=self._auto_compact_threshold_tokens,
            max_turns=max_turns if max_turns is not None else self._max_turns,
            permission_prompt=self._permission_prompt,
            ask_user_prompt=self._ask_user_prompt,
            hook_executor=self._hook_executor,
            tool_metadata=self._tool_metadata,
            require_explicit_done=self._require_explicit_done,
            approval_coordinator=self._approval_coordinator,
        )
        query_messages = list(self._messages)
        async for event in self._stream_query_with_guards(context=context, query_messages=query_messages):
            yield event
        await self._update_session_memory()
        self._schedule_extract_memories()
        logger.event(
            "continue_pending_end",
            session_id=self._tool_metadata.get("session_id"),
            message_count_after=len(self._messages),
        )
