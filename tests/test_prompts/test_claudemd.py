"""Tests for CLAUDE.md loading."""

from __future__ import annotations

from pathlib import Path

from openharness.config.paths import get_project_issue_file, get_project_pr_comments_file
import openharness.prompts.context as context_module
from openharness.prompts import (
    PromptBlock,
    build_runtime_prompt_blocks,
    build_runtime_system_prompt,
    discover_claude_md_files,
    format_prompt_blocks_debug,
    load_claude_md_prompt,
    render_prompt_blocks,
)
from openharness.config.settings import Settings


def test_discover_claude_md_files(tmp_path: Path):
    repo = tmp_path / "repo"
    nested = repo / "pkg" / "mod"
    nested.mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("root instructions", encoding="utf-8")
    rules_dir = repo / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "python.md").write_text("rule instructions", encoding="utf-8")

    files = discover_claude_md_files(nested)

    assert repo / "CLAUDE.md" in files
    assert rules_dir / "python.md" in files


def test_load_claude_md_prompt(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("be careful", encoding="utf-8")

    prompt = load_claude_md_prompt(repo)

    assert prompt is not None
    assert "Project Instructions" in prompt
    assert "be careful" in prompt


def test_build_runtime_system_prompt_combines_sections(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("repo rules", encoding="utf-8")

    prompt = build_runtime_system_prompt(Settings(), cwd=repo, latest_user_prompt="hello")

    assert "Environment" in prompt
    assert "Project Instructions" in prompt
    assert "repo rules" in prompt
    assert "Memory" in prompt


def test_build_runtime_system_prompt_caches_identical_inputs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    settings = Settings(memory={"enabled": False})
    calls: list[str | None] = []

    def fake_build_system_prompt(custom_prompt=None, env=None, cwd=None):
        del env
        calls.append(cwd)
        return f"base prompt for {cwd}"

    monkeypatch.setattr(context_module, "build_system_prompt", fake_build_system_prompt)
    context_module.clear_runtime_system_prompt_cache()
    try:
        first = build_runtime_system_prompt(settings, cwd=repo, latest_user_prompt="hello")
        second = build_runtime_system_prompt(settings, cwd=repo, latest_user_prompt="hello")
        changed = build_runtime_system_prompt(Settings(fast_mode=True, memory={"enabled": False}), cwd=repo, latest_user_prompt="hello")
    finally:
        context_module.clear_runtime_system_prompt_cache()

    assert first == second
    assert "Fast mode is enabled" in changed
    assert calls == [str(repo), str(repo)]


def test_runtime_prompt_blocks_expose_metadata_and_render_default_prompt(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()

    blocks = build_runtime_prompt_blocks(Settings(memory={"enabled": False}), cwd=repo, latest_user_prompt="hello")
    block_ids = [block.id for block in blocks]

    assert block_ids == list(dict.fromkeys(block_ids))
    assert "base-system" in block_ids
    assert "reasoning-settings" in block_ids
    assert "tool-use-enforcement" in block_ids
    assert all(block.source for block in blocks)
    assert all(block.priority > 0 for block in blocks)
    assert render_prompt_blocks(blocks) == build_runtime_system_prompt(
        Settings(memory={"enabled": False}),
        cwd=repo,
        latest_user_prompt="hello",
    )


def test_runtime_tool_enforcement_requires_ask_user_question_for_user_input(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()

    prompt = build_runtime_system_prompt(
        Settings(memory={"enabled": False}),
        cwd=repo,
        latest_user_prompt="commit changes",
    )

    assert "ask_user_question" not in prompt
    assert "clarification, confirmation, or a choice" not in prompt
    assert "Do not end your turn with a plain-text question" not in prompt


def test_agent_prompt_profiles_control_runtime_sections(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review changes\n---\n\n# Review\nCheck changes.",
        encoding="utf-8",
    )

    plan_prompt = build_runtime_system_prompt(
        Settings(memory={"enabled": False}),
        cwd=repo,
        extra_skill_dirs=[skill_root],
        agent_profile="plan",
    )
    compact_prompt = build_runtime_system_prompt(
        Settings(memory={"enabled": False}),
        cwd=repo,
        extra_skill_dirs=[skill_root],
        agent_profile="compact",
    )

    assert "planning mode" in plan_prompt
    assert "Available Skills" in plan_prompt
    assert "compact summary mode" in compact_prompt
    assert "Tool-use enforcement" not in compact_prompt
    assert "Delegation And Subagents" not in compact_prompt
    assert "Available Skills" not in compact_prompt


def test_skills_section_uses_runtime_skill_manager_tool_name(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review changes\n---\n\n# Review\nCheck changes.",
        encoding="utf-8",
    )

    prompt = build_runtime_system_prompt(
        Settings(memory={"enabled": False}),
        cwd=repo,
        extra_skill_dirs=[skill_root],
        latest_user_prompt="review this project",
    )

    assert "via the `skill_manager` tool" in prompt
    assert 'skill_manager(action="load", name="<skill_name>")' in prompt
    assert "via the `skill` tool" not in prompt


def test_skills_section_excludes_disable_model_invocation_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    skill_root = tmp_path / "skills"
    visible_dir = skill_root / "review"
    visible_dir.mkdir(parents=True)
    (visible_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review changes\n---\n\n# Review\nCheck changes.",
        encoding="utf-8",
    )
    hidden_dir = skill_root / "internal-review"
    hidden_dir.mkdir(parents=True)
    (hidden_dir / "SKILL.md").write_text(
        "---\nname: internal-review\ndescription: Internal workflow\ndisable-model-invocation: true\n---\n\n# Internal Review\nCheck internal changes.",
        encoding="utf-8",
    )

    prompt = build_runtime_system_prompt(
        Settings(memory={"enabled": False}),
        cwd=repo,
        extra_skill_dirs=[skill_root],
        latest_user_prompt="review this project",
    )

    assert "**review**: Review changes" in prompt
    assert "internal-review" not in prompt


def test_skills_section_cache_tracks_project_local_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    settings = Settings(memory={"enabled": False})

    context_module.clear_runtime_system_prompt_cache()
    try:
        before = build_runtime_system_prompt(settings, cwd=repo, latest_user_prompt="review this project")

        review_dir = repo / ".openharness" / "skills" / "project-review"
        review_dir.mkdir(parents=True)
        (review_dir / "SKILL.md").write_text(
            "---\nname: project-review\ndescription: Review this workspace.\n---\n\n# Project Review\n",
            encoding="utf-8",
        )

        after = build_runtime_system_prompt(settings, cwd=repo, latest_user_prompt="review this project")
    finally:
        context_module.clear_runtime_system_prompt_cache()

    assert "project-review" not in before
    assert "**project-review**: Review this workspace." in after


def test_format_prompt_blocks_debug_aligns_columns() -> None:
    output = format_prompt_blocks_debug(
        [
            PromptBlock(id="base-system", title="Base", content="alpha", priority=1000, source="system"),
            PromptBlock(id="tool-use-enforcement", title="Tools", content="beta gamma", priority=950, source="runtime"),
        ]
    )

    assert output == "\n".join(
        [
            "Runtime prompt blocks:",
            "ID                    CHARS  TOKENS  PRIORITY  SOURCE   CACHEABLE",
            "tool-use-enforcement     10       2       950  runtime  true",
            "base-system               5       1      1000  system   true",
        ]
    )


def test_build_runtime_system_prompt_includes_project_context_and_fast_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "repo"
    repo.mkdir()
    get_project_issue_file(repo).write_text("# Bug\nNeed to fix flaky test.\n", encoding="utf-8")
    get_project_pr_comments_file(repo).write_text(
        "# PR Comments\n- app.py:12: Please simplify this branch.\n",
        encoding="utf-8",
    )

    prompt = build_runtime_system_prompt(Settings(fast_mode=True), cwd=repo, latest_user_prompt="fix it")

    assert "Fast mode is enabled" in prompt
    assert "Issue Context" in prompt
    assert "Need to fix flaky test" in prompt
    assert "Pull Request Comments" in prompt
    assert "Please simplify this branch" in prompt


def test_build_runtime_system_prompt_uses_coordinator_prompt_when_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()

    prompt = build_runtime_system_prompt(Settings(), cwd=repo, latest_user_prompt="investigate")

    assert "You are a **coordinator**." in prompt
    assert "Coordinator User Context" not in prompt
    assert "Workers spawned via the agent tool have access to these tools" not in prompt


def test_build_runtime_system_prompt_skips_coordinator_context_when_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()

    prompt = build_runtime_system_prompt(Settings(), cwd=repo, latest_user_prompt="investigate")

    assert "Coordinator User Context" not in prompt
    assert "You are a **coordinator**." not in prompt
    assert "Delegation And Subagents" in prompt
    assert 'subagent_type="worker"' in prompt
    assert "/agents show TASK_ID" in prompt
    assert "Environment" in prompt
