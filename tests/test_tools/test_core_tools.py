"""Tests for built-in tools."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import openharness.tools.skill_manager_tool as skill_manager_module
from openharness.tools.bash_tool import BashTool, BashToolInput
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.brief_tool import BriefTool, BriefToolInput
from openharness.tools.cron_manager_tool import CronManagerTool
from openharness.tools.config_tool import ConfigTool, ConfigToolInput
from openharness.tools.enter_worktree_tool import EnterWorktreeTool, EnterWorktreeToolInput
from openharness.tools.exit_worktree_tool import ExitWorktreeTool, ExitWorktreeToolInput
from openharness.tools.file_edit_tool import FileEditTool, FileEditToolInput
from openharness.tools.file_read_tool import FileReadTool, FileReadToolInput
from openharness.tools.file_write_tool import FileWriteTool, FileWriteToolInput
from openharness.tools.glob_tool import GlobTool, GlobToolInput
from openharness.tools.grep_tool import GrepTool, GrepToolInput
from openharness.tools.lsp_tool import LspTool, LspToolInput
from openharness.tools.notebook_edit_tool import NotebookEditTool, NotebookEditToolInput
from openharness.tools.remote_trigger_tool import RemoteTriggerTool, RemoteTriggerToolInput
from openharness.tools.skill_manager_tool import SkillManagerTool, SkillManagerToolInput, validate_skill_content
from openharness.tools.todo_tool import TodoTool, TodoToolInput
from openharness.tools.tool_search_tool import ToolSearchTool, ToolSearchToolInput
from openharness.tools import create_default_tool_registry
from pydantic import BaseModel


@pytest.mark.asyncio
async def test_file_write_read_and_edit(tmp_path: Path):
    context = ToolExecutionContext(cwd=tmp_path)

    write_result = await FileWriteTool().execute(
        FileWriteToolInput(path="notes.txt", content="one\ntwo\nthree\n"),
        context,
    )
    assert write_result.is_error is False
    assert (tmp_path / "notes.txt").exists()

    read_result = await FileReadTool().execute(
        FileReadToolInput(path="notes.txt", offset=1, limit=2),
        context,
    )
    assert "2\ttwo" in read_result.output
    assert "3\tthree" in read_result.output

    edit_result = await FileEditTool().execute(
        FileEditToolInput(path="notes.txt", old_str="two", new_str="TWO"),
        context,
    )
    assert edit_result.is_error is False
    assert "TWO" in (tmp_path / "notes.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_glob_and_grep(tmp_path: Path):
    context = ToolExecutionContext(cwd=tmp_path)
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")

    glob_result = await GlobTool().execute(GlobToolInput(pattern="*.py"), context)
    assert glob_result.output.splitlines() == ["a.py", "b.py"]

    aliased_input = GlobTool().input_model.model_validate({"path": "*.py"})
    aliased_glob_result = await GlobTool().execute(aliased_input, context)
    assert aliased_glob_result.output.splitlines() == ["a.py", "b.py"]

    grep_result = await GrepTool().execute(
        GrepToolInput(pattern=r"def\s+beta", file_glob="*.py"),
        context,
    )
    assert "b.py:1:def beta():" in grep_result.output

    file_root_result = await GrepTool().execute(
        GrepToolInput(pattern=r"def\s+alpha", root="a.py"),
        context,
    )
    assert "a.py:1:def alpha():" in file_root_result.output


@pytest.mark.asyncio
async def test_glob_tool_accepts_absolute_patterns(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("openharness.tools.glob_tool.shutil.which", lambda _: None)
    context = ToolExecutionContext(cwd=tmp_path.parent)
    nested = tmp_path / "pkg"
    nested.mkdir()
    (nested / "a.py").write_text("print('a')\n", encoding="utf-8")
    (nested / "b.txt").write_text("b\n", encoding="utf-8")

    result = await GlobTool().execute(
        GlobToolInput(pattern=str(tmp_path / "**" / "*.py")),
        context,
    )

    assert result.is_error is False
    assert result.output.replace("\\", "/").splitlines() == ["pkg/a.py"]


@pytest.mark.asyncio
async def test_bash_tool_runs_command(tmp_path: Path):
    result = await BashTool().execute(
        BashToolInput(command="printf 'hello'"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert result.output == "hello"


@pytest.mark.asyncio
async def test_tool_search_and_brief_tools(tmp_path: Path):
    registry = create_default_tool_registry()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry})

    search_result = await ToolSearchTool().execute(
        ToolSearchToolInput(query="file"),
        context,
    )
    assert "read_file" in search_result.output

    brief_result = await BriefTool().execute(
        BriefToolInput(text="abcdefghijklmnopqrstuvwxyz", max_chars=20),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert brief_result.output == "abcdefghijklmnopqrst..."


@pytest.mark.asyncio
async def test_skill_todo_and_config_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = tmp_path / "config" / "skills"
    skills_dir.mkdir(parents=True)
    pytest_dir = skills_dir / "pytest"
    pytest_dir.mkdir()
    (pytest_dir / "SKILL.md").write_text("# Pytest\nHelpful pytest notes.\n", encoding="utf-8")

    skill_result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="load", name="Pytest"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert "Helpful pytest notes." in skill_result.output

    todo_result = await TodoTool().execute(
        TodoToolInput(
            todos=[{"id": "wire-commands", "content": "wire commands", "status": "pending"}],
        ),
        ToolExecutionContext(cwd=tmp_path),
    )
    todo_payload = json.loads(todo_result.output)
    assert todo_result.is_error is False
    assert todo_payload["summary"]["total"] == 1
    assert todo_payload["todos"] == [
        {"id": "wire-commands", "content": "wire commands", "status": "pending"}
    ]
    assert "wire commands" in (tmp_path / "TODO.md").read_text(encoding="utf-8")


    config_result = await ConfigTool().execute(
        ConfigToolInput(action="set", key="theme", value="solarized"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert config_result.output == "Updated theme"


def test_tool_registry_caches_api_schema_and_returns_defensive_copy() -> None:
    class CountingInput(BaseModel):
        value: str

    class CountingTool(BaseTool):
        name = "counting"
        description = "Count schema calls"
        input_model = CountingInput

        def __init__(self) -> None:
            self.schema_calls = 0

        async def execute(self, arguments: CountingInput, context: ToolExecutionContext) -> ToolResult:
            del arguments, context
            return ToolResult(output="ok")

        def to_api_schema(self) -> dict[str, object]:
            self.schema_calls += 1
            return {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": {"value": {"type": "string"}}},
            }

    tool = CountingTool()
    registry = ToolRegistry()
    registry.register(tool)

    first = registry.to_api_schema()
    first[0]["name"] = "mutated"
    second = registry.to_api_schema()

    assert tool.schema_calls == 1
    assert second[0]["name"] == "counting"


def test_tool_registry_invalidates_schema_cache_on_register_and_unregister() -> None:
    class CountingInput(BaseModel):
        value: str = ""

    class CountingTool(BaseTool):
        description = "Count schema calls"
        input_model = CountingInput

        def __init__(self, name: str) -> None:
            self.name = name
            self.schema_calls = 0

        async def execute(self, arguments: CountingInput, context: ToolExecutionContext) -> ToolResult:
            del arguments, context
            return ToolResult(output="ok")

        def to_api_schema(self) -> dict[str, object]:
            self.schema_calls += 1
            return {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": {"value": {"type": "string"}}},
            }

    first_tool = CountingTool("first")
    second_tool = CountingTool("second")
    registry = ToolRegistry()
    registry.register(first_tool)
    assert [schema["name"] for schema in registry.to_api_schema()] == ["first"]

    registry.register(second_tool)
    assert [schema["name"] for schema in registry.to_api_schema()] == ["first", "second"]
    assert first_tool.schema_calls == 2
    assert second_tool.schema_calls == 1

    registry.unregister("first")
    assert [schema["name"] for schema in registry.to_api_schema()] == ["second"]
    assert second_tool.schema_calls == 2


def test_default_registry_exposes_done_tool() -> None:
    registry = create_default_tool_registry()
    tool = registry.get("done")

    assert tool is not None
    schema = tool.to_api_schema()
    assert schema["name"] == "done"
    assert schema["parameters"]["required"] == ["message"]
    assert set(schema["parameters"]["properties"]) == {"message"}


@pytest.mark.asyncio
async def test_todo_write_merge_and_read(tmp_path: Path):
    tool = TodoTool()
    ctx = ToolExecutionContext(cwd=tmp_path)

    initial = await tool.execute(
        TodoToolInput(
            todos=[
                {"id": "task-a", "content": "task A", "status": "in_progress"},
                {"id": "task-b", "content": "task B", "status": "pending"},
            ]
        ),
        ctx,
    )
    assert initial.is_error is False
    assert json.loads(initial.output)["summary"] == {
        "total": 2,
        "pending": 1,
        "in_progress": 1,
        "completed": 0,
        "cancelled": 0,
    }

    result = await tool.execute(
        TodoToolInput(
            todos=[
                {"id": "task-a", "content": "task A", "status": "completed"},
                {"id": "task-c", "content": "task C", "status": "pending"},
            ],
            merge=True,
        ),
        ctx,
    )
    payload = json.loads(result.output)
    assert result.is_error is False
    assert payload["todos"] == [
        {"id": "task-a", "content": "task A", "status": "completed"},
        {"id": "task-b", "content": "task B", "status": "pending"},
        {"id": "task-c", "content": "task C", "status": "pending"},
    ]
    assert payload["summary"] == {
        "total": 3,
        "pending": 2,
        "in_progress": 0,
        "completed": 1,
        "cancelled": 0,
    }

    read_back = await tool.execute(TodoToolInput(), ctx)
    assert json.loads(read_back.output) == payload
    content = (tmp_path / "TODO.md").read_text(encoding="utf-8")
    assert content.count("task A") == 1
    assert "## ⬜ Pending" in content
    assert "## ✅ Completed" in content
    assert "task B" in content
    assert "task C" in content


@pytest.mark.asyncio
async def test_notebook_edit_tool(tmp_path: Path):
    result = await NotebookEditTool().execute(
        NotebookEditToolInput(path="demo.ipynb", cell_index=0, new_source="print('nb ok')\n"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is False
    assert "demo.ipynb" in result.output
    assert "nb ok" in (tmp_path / "demo.ipynb").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_lsp_tool(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "utils.py").write_text(
        'def greet(name):\n    """Return a greeting."""\n    return f"hi {name}"\n',
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "app.py").write_text(
        "from pkg.utils import greet\n\nprint(greet('world'))\n",
        encoding="utf-8",
    )
    context = ToolExecutionContext(cwd=tmp_path)

    document_symbols = await LspTool().execute(
        LspToolInput(operation="document_symbol", path="pkg/utils.py"),
        context,
    )
    assert "function greet" in document_symbols.output

    definition = await LspTool().execute(
        LspToolInput(operation="go_to_definition", path="pkg/app.py", symbol="greet"),
        context,
    )
    assert "pkg/utils.py:1:1" in definition.output.replace("\\", "/")

    references = await LspTool().execute(
        LspToolInput(operation="find_references", path="pkg/app.py", symbol="greet"),
        context,
    )
    assert "pkg/app.py:1:from pkg.utils import greet" in references.output.replace("\\", "/")

    hover = await LspTool().execute(
        LspToolInput(operation="hover", path="pkg/app.py", symbol="greet"),
        context,
    )
    assert "Return a greeting." in hover.output


@pytest.mark.asyncio
async def test_worktree_tools(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "openharness@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "OpenHarness Tests"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "demo.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    enter_result = await EnterWorktreeTool().execute(
        EnterWorktreeToolInput(branch="feature/demo"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert enter_result.is_error is False
    worktree_path = Path(enter_result.output.split("Path: ", 1)[1].strip())
    assert worktree_path.exists()

    exit_result = await ExitWorktreeTool().execute(
        ExitWorktreeToolInput(path=str(worktree_path)),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert exit_result.is_error is False
    assert not worktree_path.exists()


@pytest.mark.asyncio
async def test_cron_and_remote_trigger_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    context = ToolExecutionContext(cwd=tmp_path)

    create_result = await CronManagerTool().execute(
        CronManagerTool().input_model(action="create", name="nightly", schedule="0 0 * * *", command="printf 'CRON_OK'"),
        context,
    )
    assert create_result.is_error is False

    list_result = await CronManagerTool().execute(CronManagerTool().input_model(action="list"), context)
    assert "nightly" in list_result.output

    trigger_result = await RemoteTriggerTool().execute(
        RemoteTriggerToolInput(name="nightly"),
        context,
    )
    assert trigger_result.is_error is False
    assert "CRON_OK" in trigger_result.output

    delete_result = await CronManagerTool().execute(
        CronManagerTool().input_model(action="delete", name="nightly"),
        context,
    )
    assert delete_result.is_error is False


# ---------------------------------------------------------------------------
# write_skill_tool tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_skill_creates_skill_file(tmp_path: Path, monkeypatch):
    """write_skill creates SKILL.md under the user skills directory."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)
    content = "---\nname: my-workflow\ndescription: My custom workflow\n---\n# My Workflow\nDo stuff.\n"

    result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="write", name="my-workflow", content=content),
        ctx,
    )

    assert result.is_error is False
    assert "created" in result.output
    assert "my-workflow" in result.output
    skill_path = tmp_path / "config" / "skills" / "my-workflow" / "SKILL.md"
    assert skill_path.exists()
    assert skill_path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_write_skill_normalises_name_to_lowercase(tmp_path: Path, monkeypatch):
    """Skill name is normalised to lowercase before writing."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    result = await SkillManagerTool().execute(
        SkillManagerToolInput(
            action="write",
            name="MySkill",
            content="---\nname: MySkill\ndescription: A test skill\n---\n\n# MySkill\nSome content.\n",
        ),
        ctx,
    )

    assert result.is_error is False
    skill_path = tmp_path / "config" / "skills" / "myskill" / "SKILL.md"
    assert skill_path.exists()


@pytest.mark.asyncio
async def test_write_skill_rejects_invalid_name(tmp_path: Path, monkeypatch):
    """write_skill returns an error for names with spaces or special characters."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    for bad_name in ("My Skill", "skill!", "../etc", ""):
        result = await SkillManagerTool().execute(
            SkillManagerToolInput(action="write", name=bad_name, content="# content\n"),
            ctx,
        )
        assert result.is_error is True, f"Expected error for name={bad_name!r}"


@pytest.mark.asyncio
async def test_write_skill_rejects_empty_content(tmp_path: Path, monkeypatch):
    """write_skill returns an error when content is blank (no frontmatter → format error)."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="write", name="empty-skill", content="   "),
        ctx,
    )
    assert result.is_error is True
    assert "format" in result.output.lower() or "frontmatter" in result.output.lower()


@pytest.mark.asyncio
async def test_write_skill_overwrite_protection(tmp_path: Path, monkeypatch):
    """write_skill refuses to overwrite an existing skill unless overwrite=True."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    guard_orig = "---\nname: guard\ndescription: Guard skill\n---\n\n# Guard\nOriginal.\n"
    guard_repl = "---\nname: guard\ndescription: Guard skill\n---\n\n# Guard\nReplaced.\n"

    # Create it first.
    await SkillManagerTool().execute(
        SkillManagerToolInput(action="write", name="guard", content=guard_orig),
        ctx,
    )

    # Second write without overwrite flag should fail.
    result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="write", name="guard", content=guard_repl),
        ctx,
    )
    assert result.is_error is True
    assert "overwrite" in result.output.lower()

    # With overwrite=True it should succeed and update content.
    result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="write", name="guard", content=guard_repl, overwrite=True),
        ctx,
    )
    assert result.is_error is False
    assert "updated" in result.output
    skill_path = tmp_path / "config" / "skills" / "guard" / "SKILL.md"
    assert "Replaced." in skill_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_write_skill_immediately_loadable(tmp_path: Path, monkeypatch):
    """A skill written by write_skill can be loaded immediately via load_skill."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)
    content = "---\nname: auto-test\ndescription: Automated test skill\n---\n# Auto Test\nTest content here.\n"

    write_result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="write", name="auto-test", content=content),
        ctx,
    )
    assert write_result.is_error is False

    load_result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="load", name="auto-test"),
        ctx,
    )
    assert load_result.is_error is False
    assert "Test content here." in load_result.output


@pytest.mark.asyncio
async def test_load_skill_includes_base_directory_and_sampled_files(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)
    skill_dir = tmp_path / "config" / "skills" / "research"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "templates").mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: research\ndescription: Research workflow\n---\n\n# Research\nUse references on demand.\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "guide.md").write_text(
        "This text must stay lazy-loaded.",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "demo.sh").write_text("#!/usr/bin/env bash\necho demo\n", encoding="utf-8")
    (skill_dir / "templates" / "prompt.txt").write_text("Template body", encoding="utf-8")

    load_result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="load", name="research"),
        ctx,
    )
    expected_base = skill_dir.resolve().as_uri()

    assert load_result.is_error is False
    assert '<skill_content name="research">' in load_result.output
    assert f"Base directory for this skill: {expected_base}" in load_result.output
    assert "Relative paths in this skill" in load_result.output
    assert "<skill_files>" in load_result.output
    assert f"<file>{skill_dir / 'references' / 'guide.md'}</file>" in load_result.output
    assert f"<file>{skill_dir / 'scripts' / 'demo.sh'}</file>" in load_result.output
    assert f"<file>{skill_dir / 'SKILL.md'}</file>" not in load_result.output
    assert "This text must stay lazy-loaded." not in load_result.output
    assert load_result.metadata == {
        "skill_name": "research",
        "skill_dir": str(skill_dir.resolve()),
    }


def test_sample_skill_files_uses_rg_and_stops_at_limit(tmp_path: Path, monkeypatch):
    class _FakeProcess:
        def __init__(self) -> None:
            self.stdout = self
            self._lines = iter(
                [
                    "SKILL.md\n",
                    "references/guide.md\n",
                    ".hidden/config.json\n",
                    "scripts/demo.sh\n",
                    "",
                ]
            )
            self.terminated = False
            self.wait_called = False

        def readline(self) -> str:
            return next(self._lines)

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.wait_called = True
            return 0

    fake_process = _FakeProcess()

    def _fake_popen(*args, **kwargs):
        del args, kwargs
        return fake_process

    monkeypatch.setattr(skill_manager_module.shutil, "which", lambda _: "/usr/bin/rg")
    monkeypatch.setattr(skill_manager_module.subprocess, "Popen", _fake_popen)

    files = skill_manager_module._sample_skill_files(tmp_path, limit=2)

    assert files == [
        str((tmp_path / "references" / "guide.md").resolve()),
        str((tmp_path / ".hidden" / "config.json").resolve()),
    ]
    assert fake_process.terminated is True
    assert fake_process.wait_called is True


def test_sample_skill_files_falls_back_when_rg_missing(tmp_path: Path, monkeypatch):
    (tmp_path / "references").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    (tmp_path / "references" / "guide.md").write_text("guide", encoding="utf-8")
    (tmp_path / ".hidden" / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "scripts" / "demo.sh").write_text("echo demo\n", encoding="utf-8")
    monkeypatch.setattr(skill_manager_module.shutil, "which", lambda _: None)

    files = skill_manager_module._sample_skill_files(tmp_path, limit=2)

    assert files == [
        str((tmp_path / ".hidden" / "config.json").resolve()),
        str((tmp_path / "references" / "guide.md").resolve()),
    ]


@pytest.mark.asyncio
async def test_load_skill_lists_skills_when_name_omitted(tmp_path: Path, monkeypatch):
    """load_skill returns a list of available skills when name is not provided."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    # Write two skills so there's something to list.
    for name in ("skill-alpha", "skill-beta"):
        await SkillManagerTool().execute(
            SkillManagerToolInput(
                action="write",
                name=name,
                content=f"---\nname: {name}\ndescription: Skill {name}\n---\n\n# {name}\nContent.\n",
            ),
            ctx,
        )

    result = await SkillManagerTool().execute(SkillManagerToolInput(action="list"), ctx)
    assert result.is_error is False
    assert "skill-alpha" in result.output
    assert "skill-beta" in result.output
    assert "action='load'" in result.output


@pytest.mark.asyncio
async def test_load_skill_not_found_shows_available(tmp_path: Path, monkeypatch):
    """load_skill error message includes available skill names for easier discovery."""
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    # Write a known skill.
    await SkillManagerTool().execute(
        SkillManagerToolInput(
            action="write",
            name="existing-skill",
            content="---\nname: existing-skill\ndescription: An existing skill\n---\n\n# Existing Skill\nHello.\n",
        ),
        ctx,
    )

    result = await SkillManagerTool().execute(
        SkillManagerToolInput(action="load", name="nonexistent"),
        ctx,
    )
    assert result.is_error is True
    assert "nonexistent" in result.output
    # The available list may show display names or directory names; either is acceptable.
    assert "existing" in result.output.lower()


# ---------------------------------------------------------------------------
# validate_skill_content unit tests
# ---------------------------------------------------------------------------


_VALID_CONTENT = """\
---
name: my-skill
description: Does X when Y
---

# My Skill

This is the body.
"""


def test_validate_skill_content_valid():
    """Well-formed content passes validation."""
    result = validate_skill_content(_VALID_CONTENT)
    assert result.is_valid
    assert result.errors == []


def test_validate_skill_content_missing_frontmatter():
    """Content without a frontmatter block is rejected."""
    result = validate_skill_content("# My Skill\n\nNo frontmatter here.\n")
    assert not result.is_valid
    assert any("frontmatter" in e.lower() for e in result.errors)


def test_validate_skill_content_unclosed_frontmatter():
    """Frontmatter that is opened but never closed is rejected."""
    result = validate_skill_content("---\nname: x\ndescription: y\n# Missing closing ---\n")
    assert not result.is_valid
    assert any("closed" in e.lower() or "closing" in e.lower() for e in result.errors)


def test_validate_skill_content_invalid_yaml():
    """Invalid YAML inside the frontmatter block is rejected."""
    bad_yaml = "---\nname: [unclosed\ndescription: ok\n---\n\n# Body\n"
    result = validate_skill_content(bad_yaml)
    assert not result.is_valid
    assert any("invalid" in e.lower() or "yaml" in e.lower() for e in result.errors)


def test_validate_skill_content_missing_name_field():
    """Frontmatter without a 'name' field is rejected."""
    content = "---\ndescription: A description\n---\n\n# Body\n"
    result = validate_skill_content(content)
    assert not result.is_valid
    assert any("name" in e.lower() for e in result.errors)


def test_validate_skill_content_missing_description_field():
    """Frontmatter without a 'description' field is rejected."""
    content = "---\nname: my-skill\n---\n\n# Body\n"
    result = validate_skill_content(content)
    assert not result.is_valid
    assert any("description" in e.lower() for e in result.errors)


def test_validate_skill_content_empty_body():
    """Valid frontmatter but whitespace-only body is rejected."""
    content = "---\nname: my-skill\ndescription: Does X\n---\n\n   \n"
    result = validate_skill_content(content)
    assert not result.is_valid
    assert any("body" in e.lower() or "empty" in e.lower() for e in result.errors)


def test_validate_skill_content_multiple_errors():
    """Multiple missing fields produce multiple error entries."""
    # Valid frontmatter structure but both required fields are missing.
    content = "---\nauthor: me\n---\n\n# Body\n"
    result = validate_skill_content(content)
    assert not result.is_valid
    assert len(result.errors) >= 2
