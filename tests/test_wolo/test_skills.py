"""Tests for wolo default skills and workspace skill distribution."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from openharness.skills.metadata import load_skill_definition
from openharness.skills.registry import SkillRegistry
from wolo.core.workspace import get_default_skills_dir, get_skills_dir, initialize_workspace
from wolo.runner import _build_skill_guidance


DEFAULT_SKILL_NAMES = {
    "wolo-project-management",
    "wolo-artifact-extraction",
    "wolo-memory-extraction",
    "wolo-todo-closure",
    "wolo-report-writing",
}


def test_default_skills_dir_exists() -> None:
    default_dir = get_default_skills_dir()
    assert default_dir.exists(), f"default skills dir missing: {default_dir}"
    skill_files = list(default_dir.rglob("SKILL.md"))
    loaded_names = set()
    for path in skill_files:
        skill = load_skill_definition(path.parent.name, path.read_text(encoding="utf-8"), source="test", path=path)
        assert skill is not None, f"failed to parse skill at {path}"
        loaded_names.add(skill.name)
    assert DEFAULT_SKILL_NAMES <= loaded_names, f"missing default skills: {DEFAULT_SKILL_NAMES - loaded_names}"


def test_initialize_workspace_copies_default_skills() -> None:
    with TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "wolo_ws"
        initialize_workspace(workspace)
        user_skills_dir = get_skills_dir(workspace)
        assert user_skills_dir.exists()
        copied = {path.parent.name for path in user_skills_dir.rglob("SKILL.md")}
        assert DEFAULT_SKILL_NAMES <= copied, f"missing copied skills: {DEFAULT_SKILL_NAMES - copied}"


def test_initialize_workspace_preserves_existing_skills_and_copies_missing() -> None:
    with TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "wolo_ws"
        user_skills_dir = get_skills_dir(workspace)
        user_skills_dir.mkdir(parents=True)

        custom_skill_dir = user_skills_dir / "custom-skill"
        custom_skill_dir.mkdir()
        (custom_skill_dir / "SKILL.md").write_text(
            "---\nname: custom-skill\ndescription: custom\n---\n\n# Custom\n", encoding="utf-8"
        )

        existing_project_dir = user_skills_dir / "wolo-project-management"
        existing_project_dir.mkdir()
        (existing_project_dir / "SKILL.md").write_text(
            "---\nname: wolo-project-management\ndescription: user version\n---\n\n# User Project\n", encoding="utf-8"
        )

        initialize_workspace(workspace)
        copied = {path.parent.name for path in user_skills_dir.rglob("SKILL.md")}

        assert "custom-skill" in copied
        assert "wolo-project-management" in copied
        assert "wolo-report-writing" in copied
        assert (existing_project_dir / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname: wolo-project-management\ndescription: user version")


def test_default_skills_declare_triggers() -> None:
    default_dir = get_default_skills_dir()
    skill_files = list(default_dir.rglob("SKILL.md"))
    assert skill_files
    for path in skill_files:
        skill = load_skill_definition(path.parent.name, path.read_text(encoding="utf-8"), source="test", path=path)
        assert skill is not None, f"failed to parse skill at {path}"
        assert skill.triggers, f"default skill {skill.name} should declare triggers"


def test_build_skill_guidance_uses_triggers() -> None:
    registry = SkillRegistry()
    content = "---\nname: demo-skill\ndescription: demo\ntriggers:\n  - foo\n  - bar\n---\n\n# Demo\n"
    skill = load_skill_definition("demo-skill", content, source="test", path="/tmp/demo-skill/SKILL.md")
    assert skill is not None
    registry.register(skill)

    guidance = _build_skill_guidance(registry)
    assert "- foo / bar → `demo-skill`" in guidance


def test_build_skill_guidance_ignores_skills_without_triggers() -> None:
    registry = SkillRegistry()
    content = "---\nname: no-triggers\ndescription: no triggers\n---\n\n# No Triggers\n"
    skill = load_skill_definition("no-triggers", content, source="test", path="/tmp/no-triggers/SKILL.md")
    assert skill is not None
    registry.register(skill)

    guidance = _build_skill_guidance(registry)
    assert "no-triggers" not in guidance
    assert "（当前没有配置专业主题 skill）" in guidance
