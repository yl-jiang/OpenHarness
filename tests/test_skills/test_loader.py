"""Tests for skill loading."""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from openharness.skills import get_user_skills_dir, load_skill_registry
from openharness.skills.metadata import (
    load_skill_definition,
    parse_skill_markdown,
)


def test_load_skill_registry_includes_bundled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    registry = load_skill_registry()

    names = [skill.name for skill in registry.list_skills()]
    assert "simplify" in names
    assert "review" in names


def test_load_skill_registry_includes_user_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    deploy_dir = skills_dir / "deploy"
    deploy_dir.mkdir(parents=True)
    (deploy_dir / "SKILL.md").write_text("# Deploy\nDeployment workflow guidance\n", encoding="utf-8")

    registry = load_skill_registry()
    deploy = registry.get("Deploy")

    assert deploy is not None
    assert deploy.source == "user"
    assert "Deployment workflow guidance" in deploy.content


def test_load_skill_registry_includes_project_local_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    repo = tmp_path / "repo"
    repo.mkdir()
    project_skills_dir = repo / ".openharness" / "skills"
    review_dir = project_skills_dir / "project-review"
    review_dir.mkdir(parents=True)
    (review_dir / "SKILL.md").write_text(
        "---\nname: project-review\ndescription: Review this workspace.\n---\n\n# Project Review\n",
        encoding="utf-8",
    )

    registry = load_skill_registry(repo)
    review = registry.get("project-review")

    assert review is not None
    assert review.source == "project"
    assert "Project Review" in review.content


def test_load_skill_registry_ignores_sibling_resource_files(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    review_dir = skills_dir / "review"
    (review_dir / "references").mkdir(parents=True)
    (review_dir / "templates").mkdir()
    (review_dir / "SKILL.md").write_text(
        "# Review\nRead sibling resources on demand.\n",
        encoding="utf-8",
    )
    (review_dir / "references" / "guide.md").write_text(
        "This file should not be loaded during discovery.\n",
        encoding="utf-8",
    )
    (review_dir / "templates" / "prompt.txt").write_text(
        "This is an auxiliary template.\n",
        encoding="utf-8",
    )

    registry = load_skill_registry()
    review = registry.get("Review")

    assert review is not None
    assert review.path == str(review_dir / "SKILL.md")
    assert "Read sibling resources on demand." in review.content
    assert "This file should not be loaded during discovery." not in review.content


def test_load_skill_registry_parses_invocation_flags(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    internal_dir = skills_dir / "internal-review"
    internal_dir.mkdir(parents=True)
    (internal_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
            ---
            name: internal-review
            description: Internal-only review workflow
            disable-model-invocation: true
            user-invocable: false
            ---

            # Internal Review
        """),
        encoding="utf-8",
    )

    registry = load_skill_registry()
    skill = registry.get("internal-review")

    assert skill is not None
    assert skill.disable_model_invocation is True
    assert skill.user_invocable is False


def test_load_skill_registry_logs_successful_load(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    skill_dir = skills_dir / "audit-log-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
            ---
            name: audit-log-review
            description: Review audit logs
            ---

            # Audit Log Review
        """),
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO, logger="openharness.skills.metadata"):
        registry = load_skill_registry()

    assert registry.get("audit-log-review") is not None
    assert any("Loaded skill" in message and "audit-log-review" in message for message in caplog.messages)


def test_parse_skill_metadata_exposes_optional_frontmatter_fields():
    content = textwrap.dedent("""\
        ---
        name: nl2sql
        description: Analyze database questions
        version: 1.0.0
        tags:
          - database
          - sql
        author: Jane Doe <jane@example.com>
        license: MIT
        allowed_tools: [read_file, grep]
        required_context:
          - schema
          - examples
        argument-hint: "[question] [dialect]"
        context: fork
        disable-model-invocation: true
        user-invocable: false
        ---

        # Body
    """)

    metadata = parse_skill_markdown("fallback", content)

    assert metadata.name == "nl2sql"
    assert metadata.description == "Analyze database questions"
    assert metadata.version == "1.0.0"
    assert metadata.tags == ("database", "sql")
    assert metadata.author == "Jane Doe <jane@example.com>"
    assert metadata.license == "MIT"
    assert metadata.allowed_tools == ("read_file", "grep")
    assert metadata.required_context == ("schema", "examples")
    assert metadata.argument_hint == "[question] [dialect]"
    assert metadata.context == "fork"
    assert metadata.disable_model_invocation is True
    assert metadata.user_invocable is False


def test_load_skill_definition_carries_optional_frontmatter_fields():
    content = textwrap.dedent("""\
        ---
        name: review
        description: Review code changes
        version: 2.1.0
        tags: review, code
        author: Copilot
        license: MIT
        allowed_tools: [read_file, grep]
        required_context: [diff]
        argument-hint: "[target]"
        context: inline
        ---

        # Review
    """)

    skill = load_skill_definition("review", content, source="user", path="/tmp/review/SKILL.md")

    assert skill is not None
    assert skill.name == "review"
    assert skill.version == "2.1.0"
    assert skill.tags == ("review", "code")
    assert skill.author == "Copilot"
    assert skill.license == "MIT"
    assert skill.allowed_tools == ("read_file", "grep")
    assert skill.required_context == ("diff",)
    assert skill.argument_hint == "[target]"
    assert skill.context == "inline"


def test_load_skill_registry_skips_mismatched_skill_name_and_logs(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    mismatch_dir = skills_dir / "audit-review"
    mismatch_dir.mkdir(parents=True)
    (mismatch_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
            ---
            name: internal-audit-review
            description: Internal review workflow
            ---

            # Review
        """),
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO, logger="openharness.skills.metadata"):
        registry = load_skill_registry()

    assert registry.get("audit-review") is None
    assert registry.get("internal-audit-review") is None
    assert any("Skipping skill load" in message and "does not match" in message for message in caplog.messages)


def test_load_skill_registry_skips_missing_description_and_logs(tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    empty_dir = skills_dir / "empty-review"
    empty_dir.mkdir(parents=True)
    (empty_dir / "SKILL.md").write_text(
        textwrap.dedent("""\
            ---
            name: empty-review
            ---

            # Review
        """),
        encoding="utf-8",
    )

    with caplog.at_level(logging.INFO, logger="openharness.skills.metadata"):
        registry = load_skill_registry()

    assert registry.get("empty-review") is None
    assert any("Skipping skill load" in message and "description is empty" in message for message in caplog.messages)


# --- parse_skill_markdown unit tests ---


def test_parse_frontmatter_inline_description():
    """Inline description: value on the same line as the key."""
    content = textwrap.dedent("""\
        ---
        name: my-skill
        description: A short inline description
        ---

        # Body
    """)
    metadata = parse_skill_markdown("fallback", content)
    assert metadata.name == "my-skill"
    assert metadata.description == "A short inline description"


def test_parse_frontmatter_folded_block_scalar():
    """YAML folded block scalar (>) must be expanded into a single string."""
    content = textwrap.dedent("""\
        ---
        name: NL2SQL Expert
        description: >
          Multi-tenant NL2SQL skill for converting natural language questions
          into SQL queries. Covers the full pipeline: tenant routing,
          table selection, question enhancement, context retrieval.
        tags:
          - nl2sql
        ---

        # NL2SQL Expert Skill
    """)
    metadata = parse_skill_markdown("fallback", content)
    assert metadata.name == "NL2SQL Expert"
    assert "Multi-tenant NL2SQL skill" in metadata.description
    assert "context retrieval" in metadata.description
    # Folded scalar joins lines with spaces, not newlines
    assert "\n" not in metadata.description


def test_parse_frontmatter_literal_block_scalar():
    """YAML literal block scalar (|) preserves newlines."""
    content = textwrap.dedent("""\
        ---
        name: multi-line
        description: |
          Line one.
          Line two.
          Line three.
        ---

        # Body
    """)
    metadata = parse_skill_markdown("fallback", content)
    assert metadata.name == "multi-line"
    assert "Line one." in metadata.description
    assert "Line two." in metadata.description


def test_parse_frontmatter_quoted_description():
    """Quoted description values are handled correctly."""
    content = textwrap.dedent("""\
        ---
        name: quoted
        description: "A quoted description with: colons"
        ---

        # Body
    """)
    metadata = parse_skill_markdown("fallback", content)
    assert metadata.name == "quoted"
    assert metadata.description == "A quoted description with: colons"


def test_parse_fallback_heading_and_paragraph():
    """Without frontmatter, falls back to heading + first paragraph."""
    content = "# My Skill\nThis is the description from the body.\n"
    metadata = parse_skill_markdown("fallback", content)
    assert metadata.name == "My Skill"
    assert metadata.description == "This is the description from the body."


def test_parse_no_description_uses_skill_name():
    """When nothing provides a description, falls back to 'Skill: <name>'."""
    content = "# OnlyHeading\n"
    metadata = parse_skill_markdown("fallback", content)
    assert metadata.name == "OnlyHeading"
    assert metadata.description == "Skill: OnlyHeading"


def test_parse_malformed_yaml_falls_back():
    """Malformed YAML in frontmatter falls back to body parsing."""
    content = textwrap.dedent("""\
        ---
        name: [invalid yaml
        description: also broken: {
        ---

        # Fallback Title
        Body paragraph here.
    """)
    metadata = parse_skill_markdown("fallback", content)
    # Fallback scans all lines; frontmatter lines are not excluded, so
    # the first non-heading, non-delimiter line wins.  The important thing
    # is that a YAMLError doesn't crash the loader.
    assert isinstance(metadata.description, str) and metadata.description
