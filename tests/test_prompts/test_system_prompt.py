"""Tests for openharness.prompts.system_prompt."""

from __future__ import annotations

from openharness.prompts.environment import EnvironmentInfo
from openharness.prompts.system_prompt import build_system_prompt


def _make_env(**overrides) -> EnvironmentInfo:
    defaults = dict(
        os_name="Linux",
        os_version="5.15.0",
        platform_machine="x86_64",
        shell="bash",
        cwd="/home/user/project",
        home_dir="/home/user",
        date="2026-04-01",
        python_version="3.10.17",
        python_executable="/home/user/.openharness-venv/bin/python",
        virtual_env="/home/user/.openharness-venv",
        is_git_repo=True,
        git_branch="main",
        hostname="testhost",
    )
    defaults.update(overrides)
    return EnvironmentInfo(**defaults)


def test_build_system_prompt_contains_environment():
    env = _make_env()
    prompt = build_system_prompt(env=env)
    assert "Linux 5.15.0" in prompt
    assert "x86_64" in prompt
    assert "bash" in prompt
    assert "/home/user/project" in prompt
    assert "2026-04-01" in prompt
    assert "3.10.17" in prompt
    assert "/home/user/.openharness-venv/bin/python" in prompt
    assert "Virtual environment: /home/user/.openharness-venv" in prompt
    assert "branch: main" in prompt


def test_build_system_prompt_no_git():
    env = _make_env(is_git_repo=False, git_branch=None)
    prompt = build_system_prompt(env=env)
    assert "Git:" not in prompt


def test_build_system_prompt_git_no_branch():
    env = _make_env(is_git_repo=True, git_branch=None)
    prompt = build_system_prompt(env=env)
    assert "Git: yes" in prompt
    assert "branch:" not in prompt


def test_build_system_prompt_custom_prompt():
    env = _make_env()
    prompt = build_system_prompt(custom_prompt="You are a helpful bot.", env=env)
    assert prompt.startswith("You are a helpful bot.")
    assert "Linux 5.15.0" in prompt
    # Base prompt should not appear
    assert "OpenHarness" not in prompt


def test_build_system_prompt_default_includes_base():
    env = _make_env()
    prompt = build_system_prompt(env=env)
    assert "OpenHarness" in prompt


def test_build_system_prompt_emphasizes_surgical_bug_fixing():
    env = _make_env()
    prompt = build_system_prompt(env=env)
    assert "Prefer small, surgical changes" in prompt
    assert "reproduce it first with a test or a concrete failing case" in prompt


def test_build_system_prompt_includes_agent_behavior_quality_rules():
    env = _make_env()
    prompt = build_system_prompt(env=env)

    assert "Confirmation protocol" in prompt
    assert "no negotiating" in prompt
    assert "3-Strike Reset" in prompt
    assert "Distinguish between Inquiries and Directives" in prompt
    assert "Context efficiency" in prompt
    assert "Efficiency is secondary to correctness" in prompt


def test_build_system_prompt_requires_ask_user_question_for_user_input():
    env = _make_env()
    prompt = build_system_prompt(env=env)

    assert "ask_user_question" not in prompt
    assert "clarification, confirmation, or a choice" not in prompt
    assert "do not ask in normal assistant text" not in prompt
    assert "decisions needing user input" not in prompt


def test_build_system_prompt_injects_mode_label_in_preamble():
    env = _make_env()
    default_prompt = build_system_prompt(env=env, mode_label="Default")
    plan_prompt = build_system_prompt(env=env, mode_label="Plan")
    no_label_prompt = build_system_prompt(env=env)

    assert "currently operating in **Plan** mode" in plan_prompt
    assert "currently operating in **Default** mode" in default_prompt
    assert "You are OpenHarness" in plan_prompt
    # No mode_label → no injection
    assert "currently operating in" not in no_label_prompt


def test_build_system_prompt_mode_label_ignored_for_custom_prompt():
    """mode_label must not modify a user-supplied custom prompt."""
    env = _make_env()
    prompt = build_system_prompt(
        custom_prompt="You are a helpful bot. You are an interactive agent.",
        env=env,
        mode_label="Plan",
    )
    assert "currently operating in" not in prompt
