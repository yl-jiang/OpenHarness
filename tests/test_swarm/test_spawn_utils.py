"""Tests for teammate spawn helper behavior."""

from __future__ import annotations

import sys

from openharness.swarm.spawn_utils import (
    TEAMMATE_COMMAND_ENV_VAR,
    build_inherited_cli_flags,
    build_inherited_env_vars,
    get_teammate_command,
)


def test_get_teammate_command_prefers_current_interpreter(monkeypatch):
    monkeypatch.delenv(TEAMMATE_COMMAND_ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/current-python")

    command = get_teammate_command()

    assert command == "/tmp/current-python"


def test_build_inherited_env_vars_disables_coordinator_mode(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")

    env = build_inherited_env_vars()

    assert env["CLAUDE_CODE_COORDINATOR_MODE"] == "0"


def test_build_inherited_env_vars_applies_explicit_runtime_overrides(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")

    env = build_inherited_env_vars(
        model="Kimi-K2.5",
        api_format="openai",
        base_url="https://api.moonshot.cn/v1",
        provider="moonshot",
    )

    assert env["OPENHARNESS_MODEL"] == "Kimi-K2.5"
    assert env["OPENHARNESS_API_FORMAT"] == "openai"
    assert env["OPENHARNESS_BASE_URL"] == "https://api.moonshot.cn/v1"
    assert env["OPENHARNESS_PROVIDER"] == "moonshot"


def test_build_inherited_cli_flags_forward_runtime_overrides():
    flags = build_inherited_cli_flags(
        model="Kimi-K2.5",
        api_format="openai",
        base_url="https://api.moonshot.cn/v1",
    )

    assert flags == [
        "--model",
        "Kimi-K2.5",
        "--api-format",
        "openai",
        "--base-url",
        "https://api.moonshot.cn/v1",
    ]
