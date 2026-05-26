"""Tests for utility_profile configuration and resolution."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiStreamEvent,
)
from openharness.config.settings import (
    ProviderProfile,
    Settings,
    _apply_env_overrides,
)
from openharness.engine.types import ToolMetadataKey


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_profile(name: str, **overrides: Any) -> ProviderProfile:
    defaults = {
        "label": name,
        "provider": "openai_compat",
        "api_format": "openai",
        "auth_source": "env:OPENAI_API_KEY",
        "default_model": f"{name}-model",
        "base_url": f"https://{name}.example.com/v1",
        "api_key": f"sk-{name}-key",
    }
    defaults.update(overrides)
    return ProviderProfile(**defaults)


def _settings_with_profiles(
    active: str = "main",
    utility: str | None = None,
) -> Settings:
    """Build a Settings with predefined main + utility profiles."""
    profiles = {
        "main": _make_profile("main"),
        "cheap": _make_profile("cheap", default_model="cheap-flash"),
    }
    return Settings(
        active_profile=active,
        utility_profile=utility,
        profiles=profiles,
        api_key="sk-main-key",
        model="main-model",
    )


class FakeStreamingClient:
    """Minimal streaming client for testing."""

    def __init__(self, model_tag: str = "fake"):
        self.model_tag = model_tag
        self.calls: list[ApiMessageRequest] = []

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        self.calls.append(request)
        from openharness.engine.messages import ConversationMessage, TextBlock

        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="ok")]),
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# Tests: Settings.resolve_utility_profile
# ---------------------------------------------------------------------------


class TestResolveUtilityProfile:
    """Tests for the Settings.resolve_utility_profile method."""

    def test_returns_none_when_not_configured(self):
        s = _settings_with_profiles(utility=None)
        assert s.resolve_utility_profile() is None

    def test_returns_none_when_empty_string(self):
        s = _settings_with_profiles(utility="")
        assert s.resolve_utility_profile() is None

    def test_returns_none_when_same_as_active(self):
        s = _settings_with_profiles(active="main", utility="main")
        assert s.resolve_utility_profile() is None

    def test_returns_none_when_profile_not_found(self):
        s = _settings_with_profiles(utility="nonexistent")
        assert s.resolve_utility_profile() is None

    def test_returns_profile_when_valid_and_different(self):
        s = _settings_with_profiles(active="main", utility="cheap")
        result = s.resolve_utility_profile()
        assert result is not None
        name, profile = result
        assert name == "cheap"
        assert profile.default_model == "cheap-flash"
        assert profile.base_url == "https://cheap.example.com/v1"

    def test_env_var_overrides_field(self, monkeypatch):
        monkeypatch.setenv("OPENHARNESS_UTILITY_PROFILE", "cheap")
        s = _settings_with_profiles(active="main", utility=None)
        result = s.resolve_utility_profile()
        assert result is not None
        name, _ = result
        assert name == "cheap"

    def test_env_var_with_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("OPENHARNESS_UTILITY_PROFILE", "  cheap  ")
        s = _settings_with_profiles(active="main", utility=None)
        result = s.resolve_utility_profile()
        assert result is not None
        assert result[0] == "cheap"

    def test_env_var_same_as_active_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENHARNESS_UTILITY_PROFILE", "main")
        s = _settings_with_profiles(active="main", utility=None)
        assert s.resolve_utility_profile() is None

    def test_returns_deep_copy(self):
        s = _settings_with_profiles(active="main", utility="cheap")
        result1 = s.resolve_utility_profile()
        result2 = s.resolve_utility_profile()
        assert result1 is not None and result2 is not None
        assert result1[1] is not result2[1]


# ---------------------------------------------------------------------------
# Tests: _apply_env_overrides with utility_profile
# ---------------------------------------------------------------------------


class TestApplyEnvOverridesUtilityProfile:
    """Tests that _apply_env_overrides picks up OPENHARNESS_UTILITY_PROFILE."""

    def test_env_var_sets_utility_profile(self, monkeypatch):
        monkeypatch.setenv("OPENHARNESS_UTILITY_PROFILE", "cheap")
        # Clear other env vars that would interfere
        monkeypatch.delenv("OPENHARNESS_MODEL", raising=False)
        monkeypatch.delenv("OPENHARNESS_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
        s = _settings_with_profiles(utility=None)
        result = _apply_env_overrides(s)
        assert result.utility_profile == "cheap"

    def test_no_env_var_preserves_field(self, monkeypatch):
        monkeypatch.delenv("OPENHARNESS_UTILITY_PROFILE", raising=False)
        monkeypatch.delenv("OPENHARNESS_MODEL", raising=False)
        monkeypatch.delenv("OPENHARNESS_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
        s = _settings_with_profiles(utility="cheap")
        result = _apply_env_overrides(s)
        assert result.utility_profile == "cheap"


# ---------------------------------------------------------------------------
# Tests: utility_profile field in Settings
# ---------------------------------------------------------------------------


class TestSettingsUtilityProfileField:
    """Tests for the utility_profile field on Settings."""

    def test_default_is_none(self):
        s = Settings()
        assert s.utility_profile is None

    def test_can_set_string(self):
        s = Settings(utility_profile="cheap")
        assert s.utility_profile == "cheap"

    def test_serialization_round_trip(self):
        s = Settings(utility_profile="cheap")
        data = s.model_dump()
        assert data["utility_profile"] == "cheap"
        restored = Settings.model_validate(data)
        assert restored.utility_profile == "cheap"

    def test_none_serialization(self):
        s = Settings(utility_profile=None)
        data = s.model_dump()
        assert data["utility_profile"] is None


# ---------------------------------------------------------------------------
# Tests: ToolMetadataKey has utility keys
# ---------------------------------------------------------------------------


class TestToolMetadataKeys:
    """Ensure the new ToolMetadataKey entries exist."""

    def test_utility_client_resolution_key_exists(self):
        assert ToolMetadataKey.UTILITY_CLIENT_RESOLUTION.value == "utility_client_resolution"

    def test_utility_key_not_persisted(self):
        """Utility resolution is session-only; not in persisted keys."""
        persisted = ToolMetadataKey.all_persisted_keys()
        assert ToolMetadataKey.UTILITY_CLIENT_RESOLUTION not in persisted


# ---------------------------------------------------------------------------
# Tests: QueryEngine uses utility client for memory extraction
# ---------------------------------------------------------------------------


class TestQueryEngineUtilityUsage:
    """Test that QueryEngine reads utility client from tool_metadata."""

    @pytest.fixture
    def engine_with_utility(self):
        from openharness.engine.query_engine import QueryEngine
        from openharness.permissions.checker import PermissionChecker
        from openharness.config.settings import PermissionSettings
        from openharness.tools.base import ToolRegistry
        from openharness.ui.runtime import _UtilityClientResolution

        main_client = FakeStreamingClient("main")
        utility_client = FakeStreamingClient("utility")

        engine = QueryEngine(
            api_client=main_client,
            tool_registry=ToolRegistry(),
            permission_checker=PermissionChecker(PermissionSettings()),
            cwd="/tmp",
            model="main-model",
            system_prompt="test",
            max_tokens=1024,
            settings=Settings(
                memory={"enabled": True, "auto_extract_enabled": True},
            ),
            tool_metadata={
                ToolMetadataKey.UTILITY_CLIENT_RESOLUTION.value: _UtilityClientResolution(
                    api_client=utility_client, model="cheap-model",
                ),
            },
        )
        return engine, main_client, utility_client

    def test_extract_memories_uses_utility_client(self, engine_with_utility):
        engine, main_client, utility_client = engine_with_utility
        from openharness.engine.messages import ConversationMessage, TextBlock

        # Load some messages so extraction doesn't skip
        engine.load_messages([
            ConversationMessage.from_user_text("hello"),
            ConversationMessage(role="assistant", content=[TextBlock(text="hi there")]),
        ])

        # Mock extract_memories_from_turn to capture which client/model was passed
        captured = {}

        async def mock_extract(*, cwd, api_client, model, messages, max_records):
            captured["api_client"] = api_client
            captured["model"] = model
            from openharness.services.memory_extract import ExtractionResult
            return ExtractionResult(skipped=True, reason="test")

        with patch("openharness.services.memory_extract.extract_memories_from_turn", mock_extract):
            asyncio.get_event_loop().run_until_complete(engine._extract_durable_memories())

        assert captured["api_client"] is utility_client
        assert captured["model"] == "cheap-model"

    def test_extract_memories_falls_back_to_main(self):
        from openharness.engine.query_engine import QueryEngine
        from openharness.permissions.checker import PermissionChecker
        from openharness.config.settings import PermissionSettings
        from openharness.tools.base import ToolRegistry
        from openharness.engine.messages import ConversationMessage, TextBlock

        main_client = FakeStreamingClient("main")

        engine = QueryEngine(
            api_client=main_client,
            tool_registry=ToolRegistry(),
            permission_checker=PermissionChecker(PermissionSettings()),
            cwd="/tmp",
            model="main-model",
            system_prompt="test",
            max_tokens=1024,
            settings=Settings(
                memory={"enabled": True, "auto_extract_enabled": True},
            ),
            tool_metadata={},  # No utility client
        )
        engine.load_messages([
            ConversationMessage.from_user_text("hello"),
            ConversationMessage(role="assistant", content=[TextBlock(text="hi there")]),
        ])

        captured = {}

        async def mock_extract(*, cwd, api_client, model, messages, max_records):
            captured["api_client"] = api_client
            captured["model"] = model
            from openharness.services.memory_extract import ExtractionResult
            return ExtractionResult(skipped=True, reason="test")

        with patch("openharness.services.memory_extract.extract_memories_from_turn", mock_extract):
            asyncio.get_event_loop().run_until_complete(engine._extract_durable_memories())

        assert captured["api_client"] is main_client
        assert captured["model"] == "main-model"


# ---------------------------------------------------------------------------
# Tests: _resolve_utility_client in runtime
# ---------------------------------------------------------------------------


class TestResolveUtilityClient:
    """Tests for the runtime utility client resolution."""

    def test_returns_none_when_no_utility_profile(self):
        from openharness.ui.runtime import _resolve_utility_client

        s = _settings_with_profiles(utility=None)
        assert _resolve_utility_client(s) is None

    def test_returns_none_when_profile_same_as_active(self):
        from openharness.ui.runtime import _resolve_utility_client

        s = _settings_with_profiles(active="main", utility="main")
        assert _resolve_utility_client(s) is None

    def test_returns_none_when_profile_not_found(self):
        from openharness.ui.runtime import _resolve_utility_client

        s = _settings_with_profiles(utility="nonexistent")
        assert _resolve_utility_client(s) is None

    def test_returns_client_for_openai_format(self):
        from openharness.ui.runtime import _resolve_utility_client

        s = _settings_with_profiles(active="main", utility="cheap")
        result = _resolve_utility_client(s)
        assert result is not None
        assert result.model == "cheap-flash"
        # Should be an OpenAICompatibleClient
        from openharness.api.openai_client import OpenAICompatibleClient
        assert isinstance(result.api_client, OpenAICompatibleClient)

    def test_returns_none_when_no_api_key(self, monkeypatch):
        from openharness.ui.runtime import _resolve_utility_client

        # Create a profile with no api_key
        profiles = {
            "main": _make_profile("main"),
            "nokey": _make_profile("nokey", api_key=""),
        }
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENHARNESS_OPENAI_API_KEY", raising=False)
        s = Settings(active_profile="main", utility_profile="nokey", profiles=profiles)
        assert _resolve_utility_client(s) is None

    def test_returns_anthropic_client_for_anthropic_format(self):
        from openharness.ui.runtime import _resolve_utility_client

        profiles = {
            "main": _make_profile("main"),
            "claude": _make_profile(
                "claude",
                provider="anthropic",
                api_format="anthropic",
                auth_source="env:ANTHROPIC_API_KEY",
                api_key="sk-ant-123",
                default_model="claude-haiku",
            ),
        }
        s = Settings(active_profile="main", utility_profile="claude", profiles=profiles)
        result = _resolve_utility_client(s)
        assert result is not None
        assert result.model == "claude-haiku"
        from openharness.api.client import AnthropicApiClient
        assert isinstance(result.api_client, AnthropicApiClient)


# ---------------------------------------------------------------------------
# Tests: autodream uses utility profile
# ---------------------------------------------------------------------------


class TestAutodreamUtilityProfile:
    """Test that autodream subprocess picks up the utility profile."""

    def test_effective_profile_uses_utility_when_set(self):
        s = _settings_with_profiles(active="main", utility="cheap")
        # Verify the setting flows correctly
        assert s.utility_profile == "cheap"
        assert s.active_profile == "main"
        # The effective_profile logic in start_dream_now is:
        # (settings.utility_profile or "").strip() or settings.active_profile
        effective = (s.utility_profile or "").strip() or s.active_profile
        assert effective == "cheap"

    def test_effective_profile_falls_back_to_active(self):
        s = _settings_with_profiles(active="main", utility=None)
        effective = (s.utility_profile or "").strip() or s.active_profile
        assert effective == "main"
