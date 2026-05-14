"""High-level conversation engine."""

from __future__ import annotations

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
    StatusEvent,
    StreamEvent,
    StreamFinished,
)
from openharness.engine.types import ToolMetadataKey
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
        schedule_auto_dream(
            cwd=self._cwd,
            settings=self._settings,
            model=self._model,
            current_session_id=str(self._tool_metadata.get("session_id") or ""),
            **kwargs,
        )

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
        try:
            async for event in self._stream_query_with_guards(context=context, query_messages=query_messages):
                yield event
        finally:
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
            skill_tool_available=self._tool_registry.get("skill_manager") is not None,
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
        logger.event(
            "continue_pending_end",
            session_id=self._tool_metadata.get("session_id"),
            message_count_after=len(self._messages),
        )
