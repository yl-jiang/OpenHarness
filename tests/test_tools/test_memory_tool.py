"""Tests for the OpenHarness memory tool adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.tools import create_default_tool_registry
from openharness.tools.base import ToolExecutionContext
from openharness.tools.memory_tool import MemoryTool, MemoryToolInput


@pytest.mark.asyncio
async def test_memory_tool_add_replace_remove_and_read(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    tool = MemoryTool()
    context = ToolExecutionContext(cwd=project_dir)

    added = await tool.execute(
        MemoryToolInput(action="add", target="memory", content="Project uses uv."),
        context,
    )
    replaced = await tool.execute(
        MemoryToolInput(
            action="replace",
            target="memory",
            old_text="uv",
            content="Project uses uv run for Python commands.",
        ),
        context,
    )
    read_back = await tool.execute(MemoryToolInput(action="read", target="memory"), context)
    removed = await tool.execute(
        MemoryToolInput(action="remove", target="memory", old_text="uv run"),
        context,
    )

    assert added.is_error is False
    assert replaced.is_error is False
    assert json.loads(read_back.output)["entries"] == ["Project uses uv run for Python commands."]
    assert removed.is_error is False


@pytest.mark.asyncio
async def test_memory_tool_reports_invalid_input(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    tool = MemoryTool()

    result = await tool.execute(
        MemoryToolInput(action="replace", target="memory", content="new"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error is True
    assert "old_text is required" in result.output


def test_memory_tool_is_registered_by_default():
    registry = create_default_tool_registry()

    assert registry.get("memory") is not None
