"""Tests for skill ``!{cmd}`` shell injection."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.commands.core import CommandContext
from openharness.config.settings import PermissionSettings
from openharness.engine.query_engine import QueryEngine
from openharness.permissions import PermissionChecker, PermissionMode
from openharness.skills.metadata import parse_skill_markdown
from openharness.skills.shell_injection import (
    SkillShellInjectionError,
    extract_injections,
    render_skill_prompt_with_shell,
)
from openharness.skills.types import SkillDefinition
from openharness.tools import create_default_tool_registry


class _NeverCalledApiClient:
    async def stream_message(self, request):  # pragma: no cover - guard
        del request
        raise AssertionError("model should not run during skill render")
        if False:  # pragma: no cover
            yield None


def _make_context(
    tmp_path: Path,
    *,
    permission_mode: PermissionMode = PermissionMode.FULL_AUTO,
) -> CommandContext:
    tool_registry = create_default_tool_registry()
    engine = QueryEngine(
        api_client=_NeverCalledApiClient(),
        tool_registry=tool_registry,
        permission_checker=PermissionChecker(PermissionSettings(mode=permission_mode)),
        cwd=tmp_path,
        model="test-model",
        system_prompt="system",
    )
    return CommandContext(
        engine=engine,
        cwd=str(tmp_path),
        tool_registry=tool_registry,
    )


def _make_skill(
    *,
    content: str,
    shell_injection: bool = False,
    name: str = "demo",
) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        description="demo skill",
        content=content,
        source="user",
        shell_injection=shell_injection,
    )


# ---------------------------------------------------------------------------
# extract_injections
# ---------------------------------------------------------------------------


def test_extract_injections_handles_plain_text() -> None:
    segs = extract_injections("just plain text")
    assert [(s.kind, s.value) for s in segs] == [("text", "just plain text")]


def test_extract_injections_separates_shell_blocks() -> None:
    segs = extract_injections("before !{ls -la} middle !{git status} after")
    assert [(s.kind, s.value) for s in segs] == [
        ("text", "before "),
        ("shell", "ls -la"),
        ("text", " middle "),
        ("shell", "git status"),
        ("text", " after"),
    ]


def test_extract_injections_supports_nested_braces() -> None:
    segs = extract_injections("!{python -c 'print({\"a\": 1})'}")
    assert segs == [type(segs[0])("shell", "python -c 'print({\"a\": 1})'")]


def test_extract_injections_unterminated_raises() -> None:
    with pytest.raises(SkillShellInjectionError):
        extract_injections("hello !{ls -la")


def test_extract_injections_empty_block_kept() -> None:
    segs = extract_injections("a!{}b")
    assert [(s.kind, s.value) for s in segs] == [("text", "a"), ("shell", ""), ("text", "b")]


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------


def test_parse_skill_markdown_reads_shell_injection_flag() -> None:
    md = (
        "---\n"
        "name: demo\n"
        "description: demo\n"
        "shell-injection: true\n"
        "---\n"
        "# Demo\nBody.\n"
    )
    meta = parse_skill_markdown("demo", md)
    assert meta.shell_injection is True


def test_parse_skill_markdown_underscore_alias() -> None:
    md = (
        "---\nname: demo\ndescription: demo\nshell_injection: true\n---\n# Demo\nBody.\n"
    )
    assert parse_skill_markdown("demo", md).shell_injection is True


def test_parse_skill_markdown_default_is_false() -> None:
    md = "---\nname: demo\ndescription: demo\n---\n# Demo\nBody.\n"
    assert parse_skill_markdown("demo", md).shell_injection is False


# ---------------------------------------------------------------------------
# render_skill_prompt_with_shell
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_shell_injection_raises(tmp_path: Path) -> None:
    skill = _make_skill(content="hello !{echo hi}", shell_injection=False)
    context = _make_context(tmp_path)
    with pytest.raises(SkillShellInjectionError, match="shell-injection"):
        await render_skill_prompt_with_shell(skill, "", context=context)


@pytest.mark.asyncio
async def test_single_injection_runs_and_replaces(tmp_path: Path) -> None:
    skill = _make_skill(
        content="Pre\n!{echo hello-shell}\nPost", shell_injection=True
    )
    context = _make_context(tmp_path)
    rendered = await render_skill_prompt_with_shell(skill, "", context=context)
    assert "Pre\n" in rendered
    assert "hello-shell" in rendered
    assert "\nPost" in rendered
    assert "!{" not in rendered


@pytest.mark.asyncio
async def test_empty_injection_replaces_with_nothing(tmp_path: Path) -> None:
    skill = _make_skill(content="a!{}b", shell_injection=True)
    context = _make_context(tmp_path)
    rendered = await render_skill_prompt_with_shell(skill, "", context=context)
    assert rendered == "ab"


@pytest.mark.asyncio
async def test_unterminated_injection_raises(tmp_path: Path) -> None:
    skill = _make_skill(content="bad !{ls", shell_injection=True)
    context = _make_context(tmp_path)
    with pytest.raises(SkillShellInjectionError, match="Unterminated"):
        await render_skill_prompt_with_shell(skill, "", context=context)


@pytest.mark.asyncio
async def test_arguments_inside_shell_are_quoted(tmp_path: Path) -> None:
    # The user passes shell-meaningful input.  If $1 substitution were not
    # shell-escaped this would behave as ``echo a; rm -rf /`` — instead the
    # arg must be quoted into a single literal token.
    skill = _make_skill(content="!{echo $1}", shell_injection=True)
    context = _make_context(tmp_path)
    rendered = await render_skill_prompt_with_shell(
        skill, "'a; rm -rf /'", context=context
    )
    # The command must have echoed the literal string back, including the
    # semicolon — proof that the shell did not treat it as a separator.
    assert "a; rm -rf /" in rendered


@pytest.mark.asyncio
async def test_arguments_in_text_are_raw(tmp_path: Path) -> None:
    skill = _make_skill(
        content="user said: $1\n!{echo done}", shell_injection=True
    )
    context = _make_context(tmp_path)
    rendered = await render_skill_prompt_with_shell(
        skill, "hello world", context=context
    )
    assert "user said: hello world" in rendered
    assert "done" in rendered


@pytest.mark.asyncio
async def test_permission_denied_aborts_all_commands(tmp_path: Path, monkeypatch) -> None:
    # Mark this command as denied via PLAN mode for mutating ops.
    skill = _make_skill(
        content="!{echo safe-first}\n!{touch should-not-exist.txt}",
        shell_injection=True,
    )
    context = _make_context(tmp_path, permission_mode=PermissionMode.PLAN)

    executed: list[str] = []
    from openharness.tools import bash_tool as bash_tool_module

    original_execute = bash_tool_module.BashTool.execute

    async def _spy(self, arguments, ctx):
        executed.append(arguments.command)
        return await original_execute(self, arguments, ctx)

    monkeypatch.setattr(bash_tool_module.BashTool, "execute", _spy)

    with pytest.raises(SkillShellInjectionError, match="denied"):
        await render_skill_prompt_with_shell(skill, "", context=context)

    # No commands executed — denial aborts the whole batch.
    assert executed == []
    assert not (tmp_path / "should-not-exist.txt").exists()


@pytest.mark.asyncio
async def test_no_injection_falls_through_to_plain_content(tmp_path: Path) -> None:
    # When extract_injections finds no shell, the function returns the raw
    # content unchanged.  Caller-side dispatch logic is responsible for the
    # template-substitution fast path.
    skill = _make_skill(content="just text $1", shell_injection=True)
    context = _make_context(tmp_path)
    rendered = await render_skill_prompt_with_shell(skill, "world", context=context)
    assert rendered == "just text $1"


# ---------------------------------------------------------------------------
# Integration through resolve_skill_alias_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_skill_alias_runs_shell_injection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    skill_dir = tmp_path / "config" / "skills" / "shellskill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: shellskill\n"
        "description: demo\n"
        "shell-injection: true\n"
        "---\n"
        "# Demo\n!{echo hello-from-skill}\n",
        encoding="utf-8",
    )

    from openharness.commands.skills import resolve_skill_alias_command

    context = _make_context(tmp_path)
    result = await resolve_skill_alias_command("/shellskill", context)
    assert result is not None
    assert result.message == "Loaded skill: shellskill"
    assert "hello-from-skill" in (result.submit_prompt or "")
    assert "!{" not in (result.submit_prompt or "")


@pytest.mark.asyncio
async def test_resolve_skill_alias_reports_injection_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    skill_dir = tmp_path / "config" / "skills" / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: broken\ndescription: demo\n---\n# Broken\n!{echo nope}\n",
        encoding="utf-8",
    )

    from openharness.commands.skills import resolve_skill_alias_command

    context = _make_context(tmp_path)
    result = await resolve_skill_alias_command("/broken", context)
    assert result is not None
    assert result.submit_prompt is None
    assert "shell-injection" in (result.message or "")
