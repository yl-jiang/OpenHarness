"""Tests for build_runtime auth failure handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.ui.runtime import build_runtime


@pytest.mark.asyncio
async def test_build_runtime_exits_cleanly_when_auth_resolution_fails(monkeypatch):
    """build_runtime should raise SystemExit(1) — not ValueError — when auth resolution fails."""

    def fake_resolve_auth(self):
        raise ValueError("No credentials found")

    monkeypatch.setattr("openharness.config.settings.Settings.resolve_auth", fake_resolve_auth)

    with pytest.raises(SystemExit, match="1"):
        await build_runtime(active_profile="claude-api")


@pytest.mark.asyncio
async def test_build_runtime_exits_cleanly_for_openai_format(monkeypatch):
    """Same check for the openai-compatible path."""

    def fake_resolve_auth(self):
        raise ValueError("No credentials found")

    monkeypatch.setattr("openharness.config.settings.Settings.resolve_auth", fake_resolve_auth)

    with pytest.raises(SystemExit, match="1"):
        await build_runtime(active_profile="openai-compatible", api_format="openai")


@pytest.mark.asyncio
async def test_build_runtime_writes_trace_when_auth_resolution_fails(tmp_path: Path, monkeypatch):
    trace_path = tmp_path / "runtime-trace.jsonl"
    monkeypatch.setenv("OPENHARNESS_TRACE_FILE", str(trace_path))

    def fake_resolve_auth(self):
        raise ValueError("No credentials found")

    monkeypatch.setattr("openharness.config.settings.Settings.resolve_auth", fake_resolve_auth)

    with pytest.raises(SystemExit, match="1"):
        await build_runtime(active_profile="claude-api")

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(
        record["event"] == "runtime_auth_resolution_failed"
        and record.get("component") == "runtime"
        and record.get("provider") == "anthropic"
        and record.get("api_format") == "anthropic"
        and "No credentials found" in str(record.get("error", ""))
        for record in records
    )
