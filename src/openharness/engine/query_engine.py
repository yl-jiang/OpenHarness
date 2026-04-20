"""High-level conversation engine."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from openharness.api.client import SupportsStreamingMessages
from openharness.engine.cost_tracker import CostTracker
from openharness.coordinator.coordinator_mode import get_coordinator_user_context
from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock
from openharness.engine.query import AskUserPrompt, MaxTurnsExceeded, PermissionPrompt, QueryContext, remember_user_goal, run_query
from openharness.engine.stream_events import AssistantTurnComplete, StatusEvent, StreamEvent, StreamFinished
from openharness.hooks import HookEvent, HookExecutor
from openharness.permissions.checker import PermissionChecker
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
        self._messages: list[ConversationMessage] = []
        self._cost_tracker = CostTracker()

    @property
    def messages(self) -> list[ConversationMessage]:
        """Return the current conversation history."""
        return list(self._messages)

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

    def clear(self) -> None:
        """Clear the in-memory conversation history."""
        self._messages.clear()
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
        return text == _INTERNAL_AUTO_CONTINUE_PROMPT or text.startswith("# Coordinator User Context\n\n")

    @classmethod
    def _public_messages(cls, messages: list[ConversationMessage]) -> list[ConversationMessage]:
        """Filter out internal-only messages that should not be exposed to render_event handlers."""
        return [message for message in messages if not cls._is_internal_message(message)]

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
                    if pending_turn_complete is not None and not isinstance(event, AssistantTurnComplete):
                        if _is_meaningful(pending_turn_complete):
                            progress_in_this_run = True
                        self._messages = self._public_messages(query_messages)
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
            return

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        """Replace the in-memory conversation history."""
        self._messages = list(messages)

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
        if user_message.text.strip():
            remember_user_goal(self._tool_metadata, user_message.text)
        self._messages.append(user_message)
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
        )
        query_messages = list(self._messages)
        coordinator_context = self._build_coordinator_context_message()
        if coordinator_context is not None:
            query_messages.append(coordinator_context)
        async for event in self._stream_query_with_guards(context=context, query_messages=query_messages):
            yield event
        logger.event(
            "submit_message_end",
            session_id=self._tool_metadata.get("session_id"),
            message_count_after=len(self._messages),
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
        )
        query_messages = list(self._messages)
        async for event in self._stream_query_with_guards(context=context, query_messages=query_messages):
            yield event
        logger.event(
            "continue_pending_end",
            session_id=self._tool_metadata.get("session_id"),
            message_count_after=len(self._messages),
        )
