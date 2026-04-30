from __future__ import annotations

import json
from pathlib import Path

import pytest

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
