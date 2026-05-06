"""Shared runtime assembly for headless and Textual UIs."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from openharness.api.client import AnthropicApiClient, SupportsStreamingMessages
from openharness.api.codex_client import CodexApiClient
from openharness.api.copilot_client import CopilotClient
from openharness.api.openai_client import OpenAICompatibleClient
from openharness.api.provider import auth_status, detect_provider
from openharness.bridge import get_bridge_manager
from openharness.commands import CommandContext, CommandResult, MemoryCommandBackend, create_default_command_registry
from openharness.commands.registry import resolve_skill_alias_command
from openharness.config import get_config_file_path, load_settings
from openharness.coordinator.coordinator_mode import is_coordinator_mode
from openharness.engine import QueryEngine
from openharness.engine.messages import (
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from openharness.engine.query import MaxTurnsExceeded
from openharness.engine.stream_events import StreamEvent
from openharness.engine.types import ToolMetadataKey, default_task_focus_state
from openharness.evolution import (
    BackgroundSelfEvolutionRunner,
    SelfEvolutionConfig,
    SelfEvolutionController,
)
from openharness.hooks import HookEvent, HookExecutionContext, HookExecutor, load_hook_registry
from openharness.hooks.hot_reload import HookReloader
from openharness.mcp.client import McpClientManager
from openharness.mcp.config import load_mcp_server_configs
from openharness.memory.lifecycle import setup_memory_provider_manager, teardown_memory_provider_manager
from openharness.memory.paths import get_curated_memory_dir
from openharness.permissions import PermissionChecker
from openharness.plugins import load_plugins
from openharness.prompts import build_runtime_system_prompt
from openharness.prompts.environment import detect_git_info
from openharness.skills.loader import apply_skill_path_rules
from openharness.state import AppState, AppStateStore
from openharness.services.session_backend import DEFAULT_SESSION_BACKEND, SessionBackend
from openharness.tools import ToolRegistry, create_default_tool_registry
from openharness.tools.todo_tool import TodoStore
from openharness.keybindings import load_keybindings
from openharness.utils.log import get_logger

PermissionPrompt = Callable[[str, str], Awaitable[bool]]
AskUserPrompt = Callable[[str], Awaitable[str]]
SystemPrinter = Callable[[str], Awaitable[None]]
StreamRenderer = Callable[[StreamEvent], Awaitable[None]]
ClearHandler = Callable[[], Awaitable[None]]

logger = get_logger(__name__)


def _runtime_session_mode_log_fields() -> dict[str, object]:
    raw_value = os.environ.get("CLAUDE_CODE_COORDINATOR_MODE")
    session_mode = "coordinator" if is_coordinator_mode() else "worker"
    return {
        "session_mode": session_mode,
        "session_mode_source": "env" if raw_value is not None else "default",
        "coordinator_env_value": raw_value or "",
    }


def _resolve_vision_config(settings) -> dict[str, str]:
    """Resolve vision fallback config from settings or environment."""
    from openharness.config.settings import VisionModelConfig

    cfg = settings.vision
    if cfg.is_configured:
        return {
            "model": cfg.model,
            "api_key": cfg.api_key,
            "base_url": cfg.base_url,
        }

    env_cfg = VisionModelConfig.from_env()
    if env_cfg.is_configured:
        return {
            "model": env_cfg.model,
            "api_key": env_cfg.api_key,
            "base_url": env_cfg.base_url,
        }

    return {}


def _sync_runtime_tool_metadata(
    tool_metadata: dict[str, object],
    *,
    settings,
    provider_name: str,
) -> None:
    active_profile_name, _ = settings.resolve_profile()
    tool_metadata[ToolMetadataKey.CURRENT_MODEL.value] = settings.model
    tool_metadata[ToolMetadataKey.CURRENT_PROVIDER.value] = provider_name
    tool_metadata[ToolMetadataKey.CURRENT_API_FORMAT.value] = settings.api_format
    tool_metadata[ToolMetadataKey.CURRENT_BASE_URL.value] = settings.base_url or ""
    tool_metadata[ToolMetadataKey.CURRENT_ACTIVE_PROFILE.value] = active_profile_name


@dataclass
class RuntimeBundle:
    """Shared runtime objects for one interactive session."""

    api_client: SupportsStreamingMessages
    cwd: str
    mcp_manager: McpClientManager
    tool_registry: ToolRegistry
    app_state: AppStateStore
    hook_executor: HookExecutor
    engine: QueryEngine
    commands: object
    external_api_client: bool
    enforce_max_turns: bool = True
    session_id: str = ""
    settings_overrides: dict[str, Any] = field(default_factory=dict)
    session_backend: SessionBackend = DEFAULT_SESSION_BACKEND
    extra_skill_dirs: tuple[str, ...] = ()
    extra_plugin_roots: tuple[str, ...] = ()
    memory_backend: MemoryCommandBackend | None = None
    include_project_memory: bool = True

    def current_settings(self):
        """Return the effective settings for this session.

        We persist most settings to disk (``~/.openharness/settings.json``), but
        CLI options like ``--model``/``--api-format`` should remain in effect for
        the lifetime of the running process. Without this overlay, issuing any
        slash command (e.g. ``/fast``) would refresh UI state from disk and
        "snap back" the model/provider to whatever is stored in the config file.
        """
        return load_settings().merge_cli_overrides(**self.settings_overrides)

    def current_plugins(self):
        """Return currently visible plugins for the working tree."""
        return load_plugins(
            self.current_settings(),
            self.cwd,
            extra_roots=self.extra_plugin_roots,
        )

    def hook_summary(self) -> str:
        """Return the current hook summary."""
        return load_hook_registry(self.current_settings(), self.current_plugins()).summary()

    def plugin_summary(self) -> str:
        """Return the current plugin summary."""
        plugins = self.current_plugins()
        if not plugins:
            return "No plugins discovered."
        lines = ["Plugins:"]
        for plugin in plugins:
            state = "enabled" if plugin.enabled else "disabled"
            lines.append(f"- {plugin.manifest.name} [{state}] {plugin.manifest.description}")
        return "\n".join(lines)

    def mcp_summary(self) -> str:
        """Return the current MCP summary."""
        statuses = self.mcp_manager.list_statuses()
        if not statuses:
            return "No MCP servers configured."
        lines = ["MCP servers:"]
        for status in statuses:
            suffix = f" - {status.detail}" if status.detail else ""
            lines.append(f"- {status.name}: {status.state}{suffix}")
            if status.tools:
                lines.append(f"  tools: {', '.join(tool.name for tool in status.tools)}")
            if status.resources:
                lines.append(f"  resources: {', '.join(resource.uri for resource in status.resources)}")
        return "\n".join(lines)


def _resolve_api_client_from_settings(settings) -> SupportsStreamingMessages:
    """Build the appropriate API client for the resolved settings."""
    active_profile_name, active_profile = settings.resolve_profile()
    resolved_model = settings.model or active_profile.resolved_model

    def _safe_resolve_auth():
        try:
            resolved = settings.resolve_auth()
            logger.event(
                "runtime_auth_resolution_succeeded",
                active_profile=active_profile_name,
                provider=active_profile.provider,
                api_format=active_profile.api_format,
                model=resolved_model,
                auth_kind=resolved.auth_kind,
                auth_source=resolved.source,
            )
            return resolved
        except (ValueError, Exception) as exc:
            logger.event(
                "runtime_auth_resolution_failed",
                active_profile=active_profile_name,
                provider=active_profile.provider,
                api_format=active_profile.api_format,
                model=resolved_model,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            print(
                "Error: No API key configured.\n"
                "  Run `oh auth login` to set up authentication, or set the\n"
                "  ANTHROPIC_API_KEY (or OPENAI_API_KEY) environment variable.",
                file=sys.stderr,
            )
            raise SystemExit(1)

    if settings.api_format == "copilot":
        from openharness.api.copilot_client import COPILOT_DEFAULT_MODEL

        copilot_model = (
            COPILOT_DEFAULT_MODEL
            if settings.model in {"claude-sonnet-4.5", "claude-sonnet-4.6", "claude-sonnet-4.5", "auto"}
            else settings.model
        )
        return CopilotClient(model=copilot_model)
    if settings.provider == "openai_codex":
        auth = _safe_resolve_auth()
        return CodexApiClient(
            auth_token=auth.value,
            base_url=settings.base_url,
        )
    if settings.provider == "anthropic_claude":
        return AnthropicApiClient(
            auth_token=_safe_resolve_auth().value,
            base_url=settings.base_url,
            claude_oauth=True,
            auth_token_resolver=lambda: settings.resolve_auth().value,
        )
    if settings.api_format in ("openai", "openai_compat"):
        auth = _safe_resolve_auth()
        return OpenAICompatibleClient(
            api_key=auth.value,
            base_url=settings.base_url,
            timeout=settings.timeout,
            reasoning_effort=active_profile.reasoning_effort,
            thinking_extra_body=active_profile.thinking_extra_body,
        )
    auth = _safe_resolve_auth()
    return AnthropicApiClient(
        api_key=auth.value,
        base_url=settings.base_url,
    )


async def build_runtime(
    *,
    prompt: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    active_profile: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    permission_prompt: PermissionPrompt | None = None,
    ask_user_prompt: AskUserPrompt | None = None,
    restore_messages: list[dict] | None = None,
    restore_tool_metadata: dict[str, object] | None = None,
    enforce_max_turns: bool = True,
    session_backend: SessionBackend | None = None,
    permission_mode: str | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    disallowed_tools: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    memory_backend: MemoryCommandBackend | None = None,
    include_project_memory: bool = True,
) -> RuntimeBundle:
    """Build the shared runtime for an OpenHarness session."""
    settings_overrides: dict[str, Any] = {
        "model": model,
        "max_turns": max_turns,
        "base_url": base_url,
        "system_prompt": system_prompt,
        "api_key": api_key,
        "api_format": api_format,
        "active_profile": active_profile,
        "permission_mode": permission_mode,
    }
    settings = load_settings().merge_cli_overrides(**settings_overrides)
    cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(Path.cwd())
    normalized_skill_dirs = tuple(str(Path(path).expanduser().resolve()) for path in (extra_skill_dirs or ()))
    normalized_plugin_roots = tuple(str(Path(path).expanduser().resolve()) for path in (extra_plugin_roots or ()))
    plugins = load_plugins(settings, cwd, extra_roots=normalized_plugin_roots)
    if api_client:
        resolved_api_client = api_client
    else:
        resolved_api_client = _resolve_api_client_from_settings(settings)
    mcp_manager = McpClientManager(load_mcp_server_configs(settings, plugins))
    await mcp_manager.connect_all()
    tool_registry = create_default_tool_registry(mcp_manager)
    for plugin in plugins:
        if plugin.enabled and plugin.tools:
            for tool in plugin.tools:
                tool_registry.register(tool)
    # Apply whitelist first, then blacklist
    if allowed_tools is not None and allowed_tools != ["*"]:
        allowed_set = set(allowed_tools)
        for name in list(tool_registry._tools):
            if name not in allowed_set:
                tool_registry.unregister(name)
    for tool_name in disallowed_tools or []:
        tool_registry.unregister(tool_name)
    provider = detect_provider(settings)
    _, git_branch = detect_git_info(cwd)
    bridge_manager = get_bridge_manager()
    app_state = AppStateStore(
        AppState(
            # Show the effective runtime model (after CLI/env/profile merges),
            # not profile.last_model which may be stale.
            model=settings.model,
            permission_mode=settings.permission.mode.value,
            theme=settings.theme,
            cwd=cwd,
            git_branch=git_branch,
            provider=provider.name,
            auth_status=auth_status(settings),
            base_url=settings.base_url or "",
            vim_enabled=settings.vim_mode,
            voice_enabled=settings.voice_mode,
            voice_available=provider.voice_supported,
            voice_reason=provider.voice_reason,
            fast_mode=settings.fast_mode,
            effort=settings.effort,
            passes=settings.passes,
            mcp_connected=sum(1 for status in mcp_manager.list_statuses() if status.state == "connected"),
            mcp_failed=sum(1 for status in mcp_manager.list_statuses() if status.state == "failed"),
            bridge_sessions=len(bridge_manager.list_sessions()),
            output_style=settings.output_style,
            keybindings=load_keybindings(),
        )
    )
    hook_reloader = HookReloader(get_config_file_path())
    hook_executor = HookExecutor(
        hook_reloader.current_registry() if api_client is None else load_hook_registry(settings, plugins),
        HookExecutionContext(
            cwd=Path(cwd).resolve(),
            api_client=resolved_api_client,
            default_model=settings.model,
        ),
    )
    engine_max_turns = settings.max_turns if (enforce_max_turns or max_turns is not None) else None
    system_prompt_text = build_runtime_system_prompt(
        settings,
        cwd=cwd,
        latest_user_prompt=prompt,
        extra_skill_dirs=normalized_skill_dirs,
        extra_plugin_roots=normalized_plugin_roots,
        include_project_memory=include_project_memory,
    )
    from uuid import uuid4

    session_id = uuid4().hex[:12]
    logger.event(
        "runtime_session_mode_resolved",
        session_id=session_id,
        **_runtime_session_mode_log_fields(),
    )
    logger.event(
        "runtime_build_session_created",
        session_id=session_id,
        cwd=str(Path(cwd).resolve()),
        model=settings.model,
        provider=provider.name,
        auth_status=auth_status(settings),
        permission_mode=settings.permission.mode.value,
    )

    restored_metadata = {
        ToolMetadataKey.PERMISSION_MODE.value: settings.permission.mode.value,
        ToolMetadataKey.READ_FILE_STATE.value: [],
        ToolMetadataKey.INVOKED_SKILLS.value: [],
        ToolMetadataKey.ASYNC_AGENT_STATE.value: [],
        ToolMetadataKey.ASYNC_AGENT_TASKS.value: [],
        ToolMetadataKey.RECENT_WORK_LOG.value: [],
        ToolMetadataKey.RECENT_VERIFIED_WORK.value: [],
        ToolMetadataKey.TASK_FOCUS_STATE.value: default_task_focus_state(),
        ToolMetadataKey.COMPACT_CHECKPOINTS.value: [],
        ToolMetadataKey.SELF_EVOLUTION_STATE.value: {},
    }
    if isinstance(restore_tool_metadata, dict):
        for key, value in restore_tool_metadata.items():
            restored_metadata[key] = value
    _sync_runtime_tool_metadata(
        restored_metadata,
        settings=settings,
        provider_name=provider.name,
    )

    apply_skill_path_rules(
        settings.permission,
        cwd=cwd,
        extra_skill_dirs=normalized_skill_dirs,
        extra_plugin_roots=normalized_plugin_roots,
        settings=settings,
    )
    permission_checker = PermissionChecker(settings.permission)

    # Create and register the memory provider manager.
    memory_provider_manager = None
    if settings.memory.enabled:
        memory_provider_manager = setup_memory_provider_manager(
            curated_dir=get_curated_memory_dir(cwd),
            session_id=session_id,
        )
        restored_metadata["memory_provider_manager"] = memory_provider_manager

    if settings.self_evolution.enabled:
        evolution_config = SelfEvolutionConfig(
            enabled=settings.self_evolution.enabled,
            memory_review_interval=settings.self_evolution.memory_review_interval,
            skill_review_interval=settings.self_evolution.skill_review_interval,
            max_review_turns=settings.self_evolution.max_review_turns,
        )

        def _on_review_complete(summary: str) -> None:
            logger.event("self_evolution_review_complete", summary=summary)
            current = app_state.get().reviews_completed
            app_state.set(reviews_completed=current + 1)

        restored_metadata[ToolMetadataKey.SELF_EVOLUTION_CONTROLLER.value] = SelfEvolutionController(
            evolution_config,
            BackgroundSelfEvolutionRunner(
                api_client=resolved_api_client,
                tool_registry=tool_registry,
                permission_checker=permission_checker,
                cwd=cwd,
                model=settings.model,
                system_prompt=system_prompt_text,
                max_tokens=settings.max_tokens,
                config=evolution_config,
                tool_metadata=restored_metadata,
                on_review_complete=_on_review_complete,
            ),
        )

    todo_store = TodoStore(Path(cwd))
    engine = QueryEngine(
        api_client=resolved_api_client,
        tool_registry=tool_registry,
        permission_checker=permission_checker,
        cwd=cwd,
        model=settings.model,
        system_prompt=system_prompt_text,
        max_tokens=settings.max_tokens,
        context_window_tokens=settings.context_window_tokens or settings.memory.context_window_tokens,
        auto_compact_threshold_tokens=(
            settings.auto_compact_threshold_tokens
            or settings.memory.auto_compact_threshold_tokens
        ),
        max_turns=engine_max_turns,
        permission_prompt=permission_prompt,
        ask_user_prompt=ask_user_prompt,
        hook_executor=hook_executor,
        require_done_tool=True,
        tool_metadata={
            "mcp_manager": mcp_manager,
            "bridge_manager": bridge_manager,
            "extra_skill_dirs": normalized_skill_dirs,
            "extra_plugin_roots": normalized_plugin_roots,
            "session_id": session_id,
            "todo_store": todo_store,
            ToolMetadataKey.VISION_MODEL_CONFIG.value: _resolve_vision_config(settings),
            **restored_metadata,
        },
    )
    # Register a callback so tools (e.g. skill_manager) can refresh the
    # system prompt immediately after mutating skill files on disk.
    def _refresh_system_prompt() -> None:
        fresh_settings = load_settings().merge_cli_overrides(**settings_overrides)
        engine.set_system_prompt(
            build_runtime_system_prompt(
                fresh_settings,
                cwd=cwd,
                extra_skill_dirs=normalized_skill_dirs,
                extra_plugin_roots=normalized_plugin_roots,
                include_project_memory=include_project_memory,
            )
        )

    engine.tool_metadata["system_prompt_refresher"] = _refresh_system_prompt

    # Restore messages from a saved session if provided
    if restore_messages:
        restored = sanitize_conversation_messages(
            [ConversationMessage.model_validate(m) for m in restore_messages]
        )
        engine.load_messages(restored)

    # Start Docker sandbox if configured
    if settings.sandbox.enabled and settings.sandbox.backend == "docker":
        from openharness.sandbox.session import start_docker_sandbox

        await start_docker_sandbox(settings, session_id, Path(cwd))

    return RuntimeBundle(
        api_client=resolved_api_client,
        cwd=cwd,
        mcp_manager=mcp_manager,
        tool_registry=tool_registry,
        app_state=app_state,
        hook_executor=hook_executor,
        engine=engine,
        commands=create_default_command_registry(
            plugin_commands=[
                command
                for plugin in plugins
                if plugin.enabled
                for command in plugin.commands
            ]
        ),
        external_api_client=api_client is not None,
        enforce_max_turns=enforce_max_turns or max_turns is not None,
        session_id=session_id,
        settings_overrides=settings_overrides,
        session_backend=session_backend or DEFAULT_SESSION_BACKEND,
        extra_skill_dirs=normalized_skill_dirs,
        extra_plugin_roots=normalized_plugin_roots,
        memory_backend=memory_backend,
        include_project_memory=include_project_memory,
    )


async def start_runtime(bundle: RuntimeBundle) -> None:
    """Run session start hooks."""
    await bundle.hook_executor.execute(
        HookEvent.SESSION_START,
        {"cwd": bundle.cwd, "event": HookEvent.SESSION_START.value},
    )


async def close_runtime(bundle: RuntimeBundle) -> None:
    """Close runtime-owned resources."""
    from openharness.sandbox.session import stop_docker_sandbox

    await stop_docker_sandbox()
    # Extract local environment rules from session before closing
    try:
        from openharness.personalization.session_hook import update_rules_from_session
        update_rules_from_session(bundle.engine.messages)
    except Exception:
        pass  # personalization is best-effort, never block session end

    await bundle.mcp_manager.close()

    # Tear down memory provider manager (on_session_end + shutdown).
    manager = bundle.engine.tool_metadata.get("memory_provider_manager")
    if manager is not None:
        messages_dicts = [msg.to_api_param() for msg in bundle.engine.messages]
        teardown_memory_provider_manager(manager, messages=messages_dicts)

    await bundle.hook_executor.execute(
        HookEvent.SESSION_END,
        {"cwd": bundle.cwd, "event": HookEvent.SESSION_END.value},
    )


def _last_user_text(messages: list[ConversationMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user" and msg.text.strip():
            return msg.text.strip()
    return ""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _format_pending_tool_results(messages: list[ConversationMessage]) -> str | None:
    """Render a compact summary when we stop after tool execution but before the follow-up model turn."""
    if not messages:
        return None

    last = messages[-1]
    if last.role != "user":
        return None
    tool_results = [block for block in last.content if isinstance(block, ToolResultBlock)]
    if not tool_results:
        return None

    tool_uses_by_id: dict[str, ToolUseBlock] = {}
    assistant_text = ""
    for msg in reversed(messages[:-1]):
        if msg.role != "assistant":
            continue
        if not msg.tool_uses:
            continue
        assistant_text = msg.text.strip()
        for tu in msg.tool_uses:
            tool_uses_by_id[tu.id] = tu
        break

    lines: list[str] = [
        "Pending continuation: tool results were produced, but the model did not get a chance to respond yet."
    ]
    if assistant_text:
        lines.append(f"Last assistant message: {_truncate(assistant_text, 400)}")

    max_results = 3
    for tr in tool_results[:max_results]:
        tu = tool_uses_by_id.get(tr.tool_use_id)
        if tu is not None:
            raw_input = json.dumps(tu.input, ensure_ascii=True, sort_keys=True)
            lines.append(
                f"- {tu.name} {_truncate(raw_input, 200)} -> {_truncate(tr.content.strip(), 400)}"
            )
        else:
            lines.append(
                f"- tool_result[{tr.tool_use_id}] -> {_truncate(tr.content.strip(), 400)}"
            )

    if len(tool_results) > max_results:
        lines.append(f"(+{len(tool_results) - max_results} more tool results)")

    lines.append("To continue from these results, run: /continue [COUNT].")
    return "\n".join(lines)


def sync_app_state(bundle: RuntimeBundle) -> None:
    """Refresh UI state from current settings and dynamic keybindings."""
    settings = bundle.current_settings()
    if bundle.enforce_max_turns:
        bundle.engine.set_max_turns(settings.max_turns)
    provider = detect_provider(settings)
    _sync_runtime_tool_metadata(
        bundle.engine.tool_metadata,
        settings=settings,
        provider_name=provider.name,
    )
    bundle.app_state.set(
        model=settings.model,
        permission_mode=settings.permission.mode.value,
        theme=settings.theme,
        cwd=bundle.cwd,
        provider=provider.name,
        auth_status=auth_status(settings),
        base_url=settings.base_url or "",
        vim_enabled=settings.vim_mode,
        voice_enabled=settings.voice_mode,
        voice_available=provider.voice_supported,
        voice_reason=provider.voice_reason,
        fast_mode=settings.fast_mode,
        effort=settings.effort,
        passes=settings.passes,
        mcp_connected=sum(1 for status in bundle.mcp_manager.list_statuses() if status.state == "connected"),
        mcp_failed=sum(1 for status in bundle.mcp_manager.list_statuses() if status.state == "failed"),
        bridge_sessions=len(get_bridge_manager().list_sessions()),
        output_style=settings.output_style,
        keybindings=load_keybindings(),
    )


def refresh_runtime_client(bundle: RuntimeBundle) -> None:
    """Refresh the active runtime client after provider/auth/profile changes."""
    settings = bundle.current_settings()
    if not bundle.external_api_client:
        bundle.api_client = _resolve_api_client_from_settings(settings)
        bundle.engine.set_api_client(bundle.api_client)
        bundle.hook_executor.update_context(
            api_client=bundle.api_client,
            default_model=settings.model,
        )
    bundle.engine.set_model(settings.model)
    sync_app_state(bundle)


def _save_runtime_snapshot(
    bundle: RuntimeBundle,
    *,
    model: str,
    system_prompt: str,
) -> None:
    messages = bundle.engine.export_messages
    if not messages:
        return
    bundle.session_backend.save_snapshot(
        cwd=bundle.cwd,
        model=model,
        system_prompt=system_prompt,
        messages=messages,
        usage=bundle.engine.total_usage,
        session_id=bundle.session_id,
        tool_metadata=bundle.engine.tool_metadata,
    )


async def handle_line(
    bundle: RuntimeBundle,
    line: str,
    *,
    print_system: SystemPrinter,
    render_event: StreamRenderer,
    clear_output: ClearHandler,
) -> bool:
    """Handle one submitted line for either headless or TUI rendering."""
    if not bundle.external_api_client:
        bundle.hook_executor.update_registry(
            load_hook_registry(bundle.current_settings(), bundle.current_plugins())
        )

    context = CommandContext(
        engine=bundle.engine,
        hooks_summary=bundle.hook_summary(),
        mcp_summary=bundle.mcp_summary(),
        plugin_summary=bundle.plugin_summary(),
        cwd=bundle.cwd,
        tool_registry=bundle.tool_registry,
        app_state=bundle.app_state,
        session_backend=bundle.session_backend,
        session_id=bundle.session_id,
        extra_skill_dirs=bundle.extra_skill_dirs,
        extra_plugin_roots=bundle.extra_plugin_roots,
        memory_backend=bundle.memory_backend,
        include_project_memory=bundle.include_project_memory,
    )
    parsed = bundle.commands.lookup(line)
    result: CommandResult | None = None
    if parsed is not None:
        command, args = parsed
        result = await command.handler(args, context)
    elif line.startswith("/"):
        result = resolve_skill_alias_command(line, context)

    if result is not None:
        if result.refresh_runtime:
            refresh_runtime_client(bundle)
        await _render_command_result(result, print_system, clear_output, render_event)
        if result.submit_prompt is not None:
            original_model = bundle.engine.model
            if result.submit_model:
                bundle.engine.set_model(result.submit_model)
            settings = bundle.current_settings()
            submit_prompt = result.submit_prompt
            system_prompt = build_runtime_system_prompt(
                settings,
                cwd=bundle.cwd,
                latest_user_prompt=submit_prompt,
                extra_skill_dirs=bundle.extra_skill_dirs,
                extra_plugin_roots=bundle.extra_plugin_roots,
                include_project_memory=bundle.include_project_memory,
            )
            bundle.engine.set_system_prompt(system_prompt)
            try:
                async for event in bundle.engine.submit_message(submit_prompt):
                    await render_event(event)
            except MaxTurnsExceeded as exc:
                await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
                pending = _format_pending_tool_results(bundle.engine.messages)
                if pending:
                    await print_system(pending)
            finally:
                if result.submit_model:
                    bundle.engine.set_model(original_model)
            _save_runtime_snapshot(
                bundle,
                model=bundle.engine.model,
                system_prompt=system_prompt,
            )
        if result.continue_pending:
            settings = bundle.current_settings()
            if bundle.enforce_max_turns:
                bundle.engine.set_max_turns(settings.max_turns)
            system_prompt = build_runtime_system_prompt(
                settings,
                cwd=bundle.cwd,
                latest_user_prompt=_last_user_text(bundle.engine.messages),
                extra_skill_dirs=bundle.extra_skill_dirs,
                extra_plugin_roots=bundle.extra_plugin_roots,
                include_project_memory=bundle.include_project_memory,
            )
            bundle.engine.set_system_prompt(system_prompt)
            turns = result.continue_turns if result.continue_turns is not None else bundle.engine.max_turns
            try:
                async for event in bundle.engine.continue_pending(max_turns=turns):
                    await render_event(event)
            except MaxTurnsExceeded as exc:
                await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
                pending = _format_pending_tool_results(bundle.engine.messages)
                if pending:
                    await print_system(pending)
            _save_runtime_snapshot(
                bundle,
                model=settings.model,
                system_prompt=system_prompt,
            )
        if result.submit_prompt is None and not result.continue_pending:
            _save_runtime_snapshot(
                bundle,
                model=bundle.engine.model,
                system_prompt=bundle.engine.system_prompt,
            )
        sync_app_state(bundle)
        return not result.should_exit

    settings = bundle.current_settings()
    if bundle.enforce_max_turns:
        bundle.engine.set_max_turns(settings.max_turns)
    system_prompt = build_runtime_system_prompt(
        settings,
        cwd=bundle.cwd,
        latest_user_prompt=line,
        extra_skill_dirs=bundle.extra_skill_dirs,
        extra_plugin_roots=bundle.extra_plugin_roots,
        include_project_memory=bundle.include_project_memory,
    )
    bundle.engine.set_system_prompt(system_prompt)
    try:
        async for event in bundle.engine.submit_message(line):
            await render_event(event)
    except MaxTurnsExceeded as exc:
        await print_system(f"Stopped after {exc.max_turns} turns (max_turns).")
        pending = _format_pending_tool_results(bundle.engine.messages)
        if pending:
            await print_system(pending)
        _save_runtime_snapshot(
            bundle,
            model=settings.model,
            system_prompt=system_prompt,
        )
        sync_app_state(bundle)
        return True
    _save_runtime_snapshot(
        bundle,
        model=settings.model,
        system_prompt=system_prompt,
    )
    sync_app_state(bundle)
    return True


async def _render_command_result(
    result: CommandResult,
    print_system: SystemPrinter,
    clear_output: ClearHandler,
    render_event: StreamRenderer | None = None,
) -> None:
    if result.clear_screen:
        await clear_output()
    if result.replay_messages and render_event is not None:
        # Replay restored conversation messages as transcript events
        from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete
        from openharness.api.usage import UsageSnapshot

        await clear_output()
        await print_system("Session restored:")
        for msg in result.replay_messages:
            if msg.role == "user":
                await print_system(f"> {msg.text}")
            elif msg.role == "assistant" and msg.text.strip():
                await render_event(AssistantTextDelta(text=msg.text))
                await render_event(AssistantTurnComplete(message=msg, usage=UsageSnapshot()))
    if result.message and not result.replay_messages:
        await print_system(result.message)
