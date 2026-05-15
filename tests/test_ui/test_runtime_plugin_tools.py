from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from openharness.engine.types import ToolMetadataKey
from openharness.swarm.agent_run_context import (
    AGENT_RUN_CONTEXT_ENV_VAR,
    AgentRunContext,
    ORCHESTRATION_TOOL_NAMES,
)
from openharness.ui.runtime import _sync_runtime_tool_metadata
from openharness.ui.runtime import build_runtime, close_runtime


class _StaticApiClient:
    async def stream_message(self, request):
        del request
        if False:
            yield None


def _write_tool_plugin(plugins_root: Path) -> None:
    plugin_dir = plugins_root / "tool-plugin"
    tools_dir = plugin_dir / "tools"
    tools_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "tool-plugin",
                "version": "1.0.0",
                "description": "Runtime tool plugin",
                "enabled_by_default": True,
            }
        ),
        encoding="utf-8",
    )
    (tools_dir / "echo_tool.py").write_text(
        "from pydantic import BaseModel\n"
        "from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult\n\n"
        "class EchoArgs(BaseModel):\n"
        "    text: str = 'hello'\n\n"
        "class EchoTool(BaseTool):\n"
        "    name = 'plugin_echo'\n"
        "    description = 'Echo from plugin tool'\n"
        "    input_model = EchoArgs\n\n"
        "    async def execute(self, arguments: EchoArgs, context: ToolExecutionContext) -> ToolResult:\n"
        "        del context\n"
        "        return ToolResult(output=arguments.text)\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_build_runtime_registers_enabled_plugin_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "repo"
    plugins_root = project / ".openharness" / "plugins"
    plugins_root.mkdir(parents=True)
    _write_tool_plugin(plugins_root)

    from openharness.config.settings import Settings

    monkeypatch.setattr("openharness.ui.runtime.load_settings", lambda: Settings(allow_project_plugins=True))

    bundle = await build_runtime(cwd=str(project), api_client=_StaticApiClient())
    try:
        tool = bundle.tool_registry.get("plugin_echo")
        assert tool is not None
        assert tool.description == "Echo from plugin tool"
    finally:
        await close_runtime(bundle)


@pytest.mark.asyncio
async def test_build_runtime_whitelists_skill_directories_in_permission_checker(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    skill_dir = tmp_path / "config" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review changes\n---\n\n# Review\nRead sibling resources lazily.\n",
        encoding="utf-8",
    )

    bundle = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient())
    try:
        checker = bundle.engine._permission_checker
        patterns = {rule.pattern for rule in checker._path_rules if rule.allow}
        assert str((skill_dir / "*").resolve()) in patterns
    finally:
        await close_runtime(bundle)


@pytest.mark.asyncio
async def test_build_runtime_logs_session_mode_source(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    events: list[tuple[str, dict[str, object]]] = []

    def _fake_event(name: str, /, **kwargs) -> None:
        events.append((name, kwargs))

    monkeypatch.setattr("openharness.ui.runtime.logger.event", _fake_event)

    bundle = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient())
    try:
        mode_event = next(payload for name, payload in events if name == "runtime_session_mode_resolved")
        assert mode_event["session_id"] == bundle.session_id
        assert mode_event["session_mode"] == "coordinator"
        assert mode_event["session_mode_source"] == "env"
        assert mode_event["coordinator_env_value"] == "1"
    finally:
        await close_runtime(bundle)


@pytest.mark.asyncio
async def test_build_runtime_hides_orchestration_tools_for_leaf_child(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    root = AgentRunContext.root("root-session")
    _, child = root.spawn_child(agent_profile="worker")
    monkeypatch.setenv(AGENT_RUN_CONTEXT_ENV_VAR, child.to_env_payload())

    bundle = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient())
    try:
        for tool_name in ORCHESTRATION_TOOL_NAMES:
            assert bundle.tool_registry.get(tool_name) is None
        runtime_context = bundle.engine.tool_metadata[ToolMetadataKey.AGENT_RUN_CONTEXT.value]
        assert runtime_context["lineage_depth"] == 1
        assert runtime_context["parent_session_id"] == "root-session"
        assert runtime_context["root_session_id"] == "root-session"
        assert runtime_context["orchestration_allowed"] is False
    finally:
        await close_runtime(bundle)


@pytest.mark.asyncio
async def test_build_runtime_uses_settings_max_children_for_primary_context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))

    from openharness.config.settings import Settings

    monkeypatch.setattr("openharness.ui.runtime.load_settings", lambda: Settings(max_children=1))

    bundle = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient())
    try:
        runtime_context = bundle.engine.tool_metadata[ToolMetadataKey.AGENT_RUN_CONTEXT.value]
        assert runtime_context["session_role"] == "primary"
        assert runtime_context["max_children"] == 1
        assert runtime_context["orchestration_allowed"] is True
    finally:
        await close_runtime(bundle)


@pytest.mark.asyncio
async def test_build_runtime_supports_infinite_max_children_from_settings_file(
    tmp_path: Path, monkeypatch
):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text(
        json.dumps({"max_children": "infinity"}),
        encoding="utf-8",
    )

    bundle = await build_runtime(cwd=str(tmp_path), api_client=_StaticApiClient())
    try:
        runtime_context = bundle.engine.tool_metadata[ToolMetadataKey.AGENT_RUN_CONTEXT.value]
        assert math.isinf(runtime_context["max_children"])
        assert runtime_context["orchestration_allowed"] is True
    finally:
        await close_runtime(bundle)


def test_sync_runtime_tool_metadata_uses_enum_keys():
    from openharness.config.settings import Settings

    tool_metadata: dict[str, object] = {}
    settings = Settings(
        model="gpt-5.4",
        api_format="openai",
        base_url="https://example.com/v1",
    ).sync_active_profile_from_flat_fields()

    _sync_runtime_tool_metadata(
        tool_metadata,
        settings=settings,
        provider_name="openai",
    )

    assert tool_metadata[ToolMetadataKey.CURRENT_MODEL.value] == "gpt-5.4"
    assert tool_metadata[ToolMetadataKey.CURRENT_PROVIDER.value] == "openai"
    assert tool_metadata[ToolMetadataKey.CURRENT_API_FORMAT.value] == "openai"
    assert tool_metadata[ToolMetadataKey.CURRENT_BASE_URL.value] == "https://example.com/v1"
    assert tool_metadata[ToolMetadataKey.CURRENT_ACTIVE_PROFILE.value] == settings.resolve_profile()[0]
    assert ToolMetadataKey.CURRENT_MODEL not in ToolMetadataKey.all_persisted_keys()
