"""Tests for token estimation fallbacks."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import openharness.services.token_estimation as token_estimation


def test_estimate_tokens_uses_tiktoken_when_available(monkeypatch):
    class FakeEncoding:
        def encode(self, text: str) -> list[int]:
            assert text == "hello world"
            return [1, 2, 3, 4, 5]

    fake_tiktoken = SimpleNamespace(get_encoding=lambda name: FakeEncoding())
    monkeypatch.setattr(importlib, "import_module", lambda name: fake_tiktoken)
    importlib.reload(token_estimation)

    assert token_estimation.estimate_tokens("hello world") == 5


def test_estimate_tokens_falls_back_when_tiktoken_is_unavailable(monkeypatch):
    def _import_module(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", _import_module)
    importlib.reload(token_estimation)

    assert token_estimation.estimate_tokens("abcd") == 1
