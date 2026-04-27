"""Hermes-style self-evolution through background memory and skill review."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openharness.api.client import SupportsStreamingMessages
from openharness.engine.messages import ConversationMessage
from openharness.engine.types import ToolMetadataKey
from openharness.permissions.checker import PermissionChecker
from openharness.tools.base import ToolRegistry
from openharness.utils.log import get_logger

logger = get_logger(__name__)

_MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves: persona, preferences, "
    "personal details, or recurring expectations worth remembering?\n"
    "2. Has the user expressed expectations about how the assistant should behave, "
    "their work style, or ways they want future sessions to operate?\n\n"
    "If something stands out, save it using the memory tool. "
    "If nothing is worth saving, just say 'Nothing to save.' and stop."
)

_SKILL_REVIEW_PROMPT = (
    "Review the conversation above and consider saving or updating a skill if appropriate.\n\n"
    "Focus on: was a non-trivial approach used to complete a task that required trial "
    "and error, changing course due to experiential findings, or meeting a user "
    "preference for a different method or outcome?\n\n"
    "If a relevant skill already exists, update it with what you learned. "
    "Otherwise, create a new skill if the approach is reusable.\n"
    "If nothing is worth saving, just say 'Nothing to save.' and stop."
)

_COMBINED_REVIEW_PROMPT = (
    "Review the conversation above and consider two things:\n\n"
    "**Memory**: Has the user revealed things about themselves: persona, preferences, "
    "personal details, or recurring expectations? Has the user expressed expectations "
    "about how the assistant should behave, their work style, or ways future sessions "
    "should operate? If so, save using the memory tool.\n\n"
    "**Skills**: Was a non-trivial approach used to complete a task that required trial "
    "and error, changing course due to experiential findings, or meeting a user "
    "preference for a different method or outcome? If a relevant skill already exists, "
    "update it. Otherwise, create a new one if the approach is reusable.\n\n"
    "Only act if there's something genuinely worth saving. "
    "If nothing stands out, just say 'Nothing to save.' and stop."
)


@dataclass(frozen=True)
class SelfEvolutionConfig:
    """Configuration for background self-evolution review."""

    enabled: bool = True
    memory_review_interval: int = 10
    skill_review_interval: int = 10
    max_review_turns: int = 4


@dataclass(frozen=True)
class SelfEvolutionReviewRequest:
    """One background review request."""

    messages_snapshot: list[ConversationMessage]
    review_memory: bool
    review_skills: bool
    prompt: str
    latest_user_prompt: str = ""


class SelfEvolutionRunner(Protocol):
    """Spawns a review without blocking the foreground response."""

    def spawn_review(self, request: SelfEvolutionReviewRequest) -> None:
        """Start a best-effort review."""


def build_self_evolution_review_prompt(*, review_memory: bool, review_skills: bool) -> str:
    """Return the appropriate review prompt."""
    if review_memory and review_skills:
        return _COMBINED_REVIEW_PROMPT
    if review_memory:
        return _MEMORY_REVIEW_PROMPT
    return _SKILL_REVIEW_PROMPT


class SelfEvolutionController:
    """Track review triggers and hand off review work to a runner."""

    def __init__(self, config: SelfEvolutionConfig, runner: SelfEvolutionRunner) -> None:
        self._config = config
        self._runner = runner

    def begin_user_turn(
        self,
        metadata: dict[str, object],
        *,
        memory_tool_available: bool,
        skill_tool_available: bool,
    ) -> None:
        """Record a new user turn and queue periodic memory review if due."""
        state = _state(metadata)
        state["memory_tool_available"] = bool(memory_tool_available)
        state["skill_tool_available"] = bool(skill_tool_available)
        if not self._config.enabled or not memory_tool_available:
            return
        interval = self._config.memory_review_interval
        if interval <= 0:
            return
        turns = int(state.get("turns_since_memory_review") or 0) + 1
        if turns >= interval:
            state["turns_since_memory_review"] = 0
            state["pending_memory_review"] = True
        else:
            state["turns_since_memory_review"] = turns

    def observe_assistant_turn(
        self,
        metadata: dict[str, object],
        message: ConversationMessage,
    ) -> None:
        """Record assistant tool work and queue skill review if due."""
        state = _state(metadata)
        if not self._config.enabled or not state.get("skill_tool_available"):
            return
        tool_uses = message.tool_uses
        if not tool_uses:
            return
        if any(_is_skill_write(tool.name, tool.input) for tool in tool_uses):
            state["tool_iters_since_skill_review"] = 0
            state["pending_skill_review"] = False
            return
        interval = self._config.skill_review_interval
        if interval <= 0:
            return
        iterations = int(state.get("tool_iters_since_skill_review") or 0) + 1
        if iterations >= interval:
            state["tool_iters_since_skill_review"] = 0
            state["pending_skill_review"] = True
        else:
            state["tool_iters_since_skill_review"] = iterations

    def maybe_spawn_review(
        self,
        metadata: dict[str, object],
        messages_snapshot: list[ConversationMessage],
        *,
        latest_user_prompt: str = "",
    ) -> None:
        """Spawn a review if either memory or skill triggers are pending."""
        state = _state(metadata)
        if not self._config.enabled:
            return
        review_memory = bool(state.pop("pending_memory_review", False))
        review_skills = bool(state.pop("pending_skill_review", False))
        if not review_memory and not review_skills:
            return
        request = SelfEvolutionReviewRequest(
            messages_snapshot=list(messages_snapshot),
            review_memory=review_memory,
            review_skills=review_skills,
            prompt=build_self_evolution_review_prompt(
                review_memory=review_memory,
                review_skills=review_skills,
            ),
            latest_user_prompt=latest_user_prompt,
        )
        self._runner.spawn_review(request)


class BackgroundSelfEvolutionRunner:
    """Run self-evolution in an asyncio task using the existing query engine."""

    def __init__(
        self,
        *,
        api_client: SupportsStreamingMessages,
        tool_registry: ToolRegistry,
        permission_checker: PermissionChecker,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        max_tokens: int,
        config: SelfEvolutionConfig,
        tool_metadata: dict[str, object],
    ) -> None:
        self._api_client = api_client
        self._tool_registry = tool_registry
        self._permission_checker = permission_checker
        self._cwd = Path(cwd)
        self._model = model
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._config = config
        self._tool_metadata = tool_metadata

    def spawn_review(self, request: SelfEvolutionReviewRequest) -> None:
        """Schedule the background review on the current event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("Skipping self-evolution review: no running event loop.")
            return
        loop.create_task(self._run_review(request))

    async def _run_review(self, request: SelfEvolutionReviewRequest) -> None:
        try:
            from openharness.engine.query_engine import QueryEngine

            metadata = dict(self._tool_metadata)
            metadata.pop(ToolMetadataKey.SELF_EVOLUTION_CONTROLLER.value, None)
            engine = QueryEngine(
                api_client=self._api_client,
                tool_registry=self._tool_registry,
                permission_checker=self._permission_checker,
                cwd=self._cwd,
                model=self._model,
                system_prompt=self._system_prompt,
                max_tokens=self._max_tokens,
                max_turns=max(1, self._config.max_review_turns),
                tool_metadata=metadata,
            )
            engine.load_messages(request.messages_snapshot)
            async for _event in engine.submit_message(request.prompt):
                pass
        except Exception as exc:
            logger.debug("Self-evolution review failed: %s", exc)


def _state(metadata: dict[str, object]) -> dict[str, object]:
    raw = metadata.get(ToolMetadataKey.SELF_EVOLUTION_STATE.value)
    if isinstance(raw, dict):
        return raw
    state: dict[str, object] = {
        "turns_since_memory_review": 0,
        "tool_iters_since_skill_review": 0,
        "pending_memory_review": False,
        "pending_skill_review": False,
    }
    metadata[ToolMetadataKey.SELF_EVOLUTION_STATE.value] = state
    return state


def _is_skill_write(tool_name: str, tool_input: object) -> bool:
    if tool_name != "skill_manager" or not isinstance(tool_input, dict):
        return False
    return str(tool_input.get("action") or "") in {"write", "patch", "delete"}
