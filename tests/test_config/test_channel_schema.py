"""Tests for channel compatibility config schemas."""

from __future__ import annotations

from openharness.config.schema import TelegramConfig


def test_telegram_config_exposes_proxy_field():
    assert "proxy" in TelegramConfig.model_fields
    assert TelegramConfig().proxy is None
