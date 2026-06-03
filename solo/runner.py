"""Self-log query runner backed by the OpenHarness QueryEngine."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from common.constants import HIDDEN_ARGS
from openharness.api.client import SupportsStreamingMessages
from openharness.config import load_settings
from openharness.config.settings import PermissionSettings
from openharness.engine.messages import ConversationMessage, ImageBlock, TextBlock, sanitize_conversation_messages
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete, ReasoningDelta, ToolExecutionCompleted, ToolExecutionStarted
from openharness.engine.types import ToolMetadataKey
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.api.recording_client import wrap_with_model_call_recorder
from openharness.skills import load_skill_registry
from openharness.tools.base import ToolRegistry
from openharness.ui.runtime import _resolve_api_client_from_settings, _resolve_vision_config
from openharness.utils.log import get_logger

from solo.core.memory import load_memory_prompt
from solo.core.session import load_conversation, save_conversation
from solo.core.store import SoloStore
from solo.prompts import (
    SIMILAR_RECORDS_HEADER,
    SKILLS_PROMPT_HEADER,
    TOOL_ROUTER_PROMPT,
    build_time_context,
)
from solo.strings import ARG_LABELS, FALLBACK_MESSAGE, PASSTHROUGH_TOOLS, TOOL_LABELS
from solo.tools import SoloToolRegistry, build_oh_registry
from solo.core.workspace import get_memory_dir, get_sessions_dir, get_skills_dir, get_soul_path, get_user_path, get_workspace_root

logger = get_logger(__name__)

_MAX_TURNS = 10
_SESSION_MAX_MESSAGES = 80

# Arguments that are noise for end-users and should never be shown.
_HIDDEN_ARGS = HIDDEN_ARGS

_MAX_HINT_ARGS = 3
_MAX_ARG_LEN = 60


def _stringify_arg(value: Any) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
    elif isinstance(value, (list, tuple)):
        text = "、".join(_stringify_arg(v) for v in value if v not in (None, ""))
    elif isinstance(value, dict):
        text = "、".join(f"{k}:{_stringify_arg(v)}" for k, v in value.items())
    else:
        text = str(value)
    text = text.strip()
    if len(text) > _MAX_ARG_LEN:
        text = text[: _MAX_ARG_LEN - 1] + "…"
    return text


def _format_tool_hint(tool_name: str, tool_input: dict[str, Any] | None) -> str:
    """Render a human-friendly tool-call hint with key arguments.

    Shows the friendly action label plus the most relevant arguments so the
    Feishu user sees *what* is being executed, not just the tool name.
    """
    suffix = tool_name.split("_", 1)[1] if "_" in tool_name else tool_name
    header = TOOL_LABELS.get(suffix, f"🛠️ {tool_name}")

    lines: list[str] = []
    for key, value in (tool_input or {}).items():
        if key in _HIDDEN_ARGS or value in (None, "", [], {}):
            continue
        text = _stringify_arg(value)
        if not text:
            continue
        label = ARG_LABELS.get(key, key)
        lines.append(f"  · {label}：{text}")
        if len(lines) >= _MAX_HINT_ARGS:
            break

    if lines:
        return header + "\n" + "\n".join(lines)
    return header


def _read_file(path: Path) -> str | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    return content or None


def _is_image_file(path: str) -> bool:
    """Check if a file path refers to an image based on MIME type."""
    mime, _ = mimetypes.guess_type(path)
    return bool(mime and mime.startswith("image/"))


def _build_similar_records_context(store: SoloStore, user_text: str, *, max_results: int = 5) -> str:
    """Search historical records for BM25-similar entries and return a compact context block.

    Kept out of the system prompt (like time context) so the static prompt
    benefits from KV-Cache sharing across turns.
    """
    if not user_text.strip():
        return ""
    try:
        records = store.search_records(query=user_text, limit=max_results)
    except Exception:
        logger.debug("_build_similar_records_context search failed, skipping")
        return ""
    if not records:
        logger.debug("_build_similar_records_context no similar records found")
        return ""
    lines = list(SIMILAR_RECORDS_HEADER)
    for record in records:
        summary = record.summary or record.corrected_content[:60]
        tag_part = f" #{record.tags}" if record.tags else ""
        lines.append(f"- [{record.date}] {summary} [{record.emotion}]{tag_part}")
    lines.append("")
    logger.debug("_build_similar_records_context found %d similar records", len(records))
    return "\n".join(lines)


def _build_user_message(text: str, media: list[str] | None) -> str | ConversationMessage:
    """Build a user message, optionally embedding image blocks from media paths.

    Returns a plain string if no image media is present (preserving existing behavior),
    or a ConversationMessage with TextBlock + ImageBlock content when images are provided.
    """
    if not media:
        return text

    image_paths = [p for p in media if _is_image_file(p)]
    if not image_paths:
        return text

    content: list[TextBlock | ImageBlock] = [TextBlock(text=text)]
    for img_path in image_paths:
        try:
            content.append(ImageBlock.from_path(img_path))
        except Exception:
            logger.warning("Failed to encode image attachment: %s", img_path)
    return ConversationMessage.from_user_content(content)


def _build_system_prompt(workspace: Path) -> str:
    """Build the system prompt by combining routing rules with persona files and memory."""
    sections = [TOOL_ROUTER_PROMPT.strip()]
    skills_prompt = _build_skills_prompt(workspace)
    if skills_prompt:
        sections.append(skills_prompt)

    soul = _read_file(get_soul_path(workspace))
    if soul:
        sections.append(soul)

    user = _read_file(get_user_path(workspace))
    if user:
        sections.extend(["# User Profile", user])

    memory = load_memory_prompt(workspace)
    if memory:
        sections.append(memory)

    return "\n\n".join(sections)


def _build_skills_prompt(workspace: Path) -> str | None:
    registry = load_skill_registry(None, extra_skill_dirs=[get_skills_dir(workspace)])
    skills = [skill for skill in registry.list_skills() if not skill.disable_model_invocation]
    if not skills:
        return None
    lines = list(SKILLS_PROMPT_HEADER)
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def _autodream_context(workspace: Path) -> dict[str, str]:
    return {
        "memory_dir": str(get_memory_dir(workspace)),
        "session_dir": str(get_sessions_dir(workspace)),
        "app_label": "solo personal memory",
        "runner_module": "ohmo",
    }


class SoloQueryRunner:
    """Run the solo agent loop using OpenHarness QueryEngine with persistent conversation history."""

    def __init__(
        self,
        store: SoloStore,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        self._store = store
        base_client = api_client or _resolve_api_client_from_settings(settings)
        self._client = wrap_with_model_call_recorder(base_client, self._store.record_llm_call)

    async def stream_run(
        self,
        user_text: str,
        session_key: str = "",
        *,
        media: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
        progress_callback: Callable[[str], Any] | None = None,
        allow_tools: bool = True,
        include_time_context: bool = True,
        include_similar_context: bool = True,
        use_session_history: bool = True,
        persist_session: bool = True,
        system_prompt_override: str | None = None,
    ):
        """Async generator yielding ``(kind, text)`` tuples during execution.

        Yields:
            ``("progress", text)`` — transient status/thinking hint
            ``("tool_hint", text)`` — tool-use notification
            ``("final", text)``    — the final reply (always last)
        """
        if allow_tools:
            registry = SoloToolRegistry(
                self._store,
                source_context=source_context,
                progress_callback=progress_callback,
            )
            oh_registry = build_oh_registry(registry)
        else:
            oh_registry = ToolRegistry()

        workspace = get_workspace_root(self._store.workspace)
        skill_dirs = (str(get_skills_dir(workspace)),)
        prior_messages, session_id = ([], None)
        if session_key and use_session_history:
            prior_messages, session_id = load_conversation(workspace, session_key)
        # Limit session history to prevent topic drift and silent empty model
        # stops in long-running gateway chats. Older facts remain searchable.
        if len(prior_messages) > _SESSION_MAX_MESSAGES:
            logger.info(
                "session window trimmed session_key=%s total=%d kept=%d",
                session_key, len(prior_messages), _SESSION_MAX_MESSAGES,
            )
            prior_messages = prior_messages[-_SESSION_MAX_MESSAGES:]
        if not session_id:
            session_id = uuid4().hex[:12]

        engine = QueryEngine(
            api_client=self._client,
            tool_registry=oh_registry,
            permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
            cwd=Path.cwd(),
            model=self._settings.model,
            system_prompt=system_prompt_override or _build_system_prompt(workspace),
            max_tokens=self._settings.max_tokens,
            max_turns=_MAX_TURNS,
            settings=self._settings,
            tool_metadata={
                "session_id": session_id,
                "extra_skill_dirs": skill_dirs,
                "user_skills_dir": str(get_skills_dir(workspace)),
                "skill_registry_cwd": None,
                ToolMetadataKey.VISION_MODEL_CONFIG.value: _resolve_vision_config(self._settings),
                "autodream_context": _autodream_context(workspace),
            },
        )
        engine.tool_metadata["system_prompt_refresher"] = lambda: engine.set_system_prompt(
            system_prompt_override or _build_system_prompt(workspace)
        )
        if prior_messages:
            engine.load_messages(sanitize_conversation_messages(prior_messages))

        # Prefix the user message with volatile context so the *system prompt*
        # remains static and can be fully KV-Cache shared across turns.
        prefix = ""
        if include_time_context:
            prefix += build_time_context()
        if include_similar_context:
            prefix += _build_similar_records_context(self._store, user_text)
        user_message = _build_user_message(prefix + user_text, media)

        yield ("progress", "🤔 正在思考...")
        last_text = ""
        tool_outputs: list[str] = []
        tool_errors: list[str] = []
        # Tools whose output should be sent verbatim (not summarized by LLM).
        _PASSTHROUGH_TOOLS = PASSTHROUGH_TOOLS
        passthrough_output: str = ""
        engine_error: str = ""
        try:
            async for event in engine.submit_message(user_message):
                if isinstance(event, ReasoningDelta):
                    yield ("reasoning", event.text)
                elif isinstance(event, AssistantTextDelta):
                    yield ("delta", event.text)
                elif isinstance(event, ToolExecutionStarted):
                    yield ("tool_hint", _format_tool_hint(event.tool_name, event.tool_input))
                elif isinstance(event, AssistantTurnComplete):
                    candidate = event.message.text.strip()
                    if candidate and not event.message.tool_uses:
                        last_text = candidate
                elif isinstance(event, ToolExecutionCompleted):
                    if event.is_error:
                        tool_errors.append(f"{event.tool_name}: {event.output.strip()[:200]}")
                    elif event.output.strip():
                        tool_outputs.append(event.output.strip())
                        if event.tool_name in _PASSTHROUGH_TOOLS:
                            passthrough_output = event.output.strip()
        except Exception as exc:
            engine_error = f"{type(exc).__name__}: {exc}"
            logger.exception("SoloQueryRunner engine error session_key=%r text=%r", session_key, user_text[:80])

        if session_key and persist_session:
            save_conversation(workspace, session_key, engine.messages, session_id=session_id)

        # For passthrough tools (report/visualize), send the full tool output
        # directly instead of the LLM's potentially abbreviated summary.
        if passthrough_output:
            final = passthrough_output
        else:
            # Prefer the model's final text for human tone after a successful
            # record/import flow; tool output remains the fallback for silent
            # final turns.
            final = last_text or "\n".join(tool_outputs) or FALLBACK_MESSAGE
            if final.startswith(FALLBACK_MESSAGE):
                logger.warning(
                    "solo fallback triggered — last_text=%r tool_outputs=%s "
                    "tool_errors=%s engine_error=%s session_key=%s text_preview=%r",
                    last_text,
                    [o[:80] for o in tool_outputs],
                    tool_errors,
                    engine_error,
                    session_key,
                    user_text[:120],
                )
        yield ("final", final)

    async def run(
        self,
        user_text: str,
        session_key: str = "",
        *,
        media: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
        allow_tools: bool = True,
        include_time_context: bool = True,
        include_similar_context: bool = True,
        use_session_history: bool = True,
        persist_session: bool = True,
        system_prompt_override: str | None = None,
    ) -> str:
        async for kind, text in self.stream_run(
            user_text,
            session_key,
            media=media,
            source_context=source_context,
            allow_tools=allow_tools,
            include_time_context=include_time_context,
            include_similar_context=include_similar_context,
            use_session_history=use_session_history,
            persist_session=persist_session,
            system_prompt_override=system_prompt_override,
        ):
            if kind == "final":
                return text
        return FALLBACK_MESSAGE
