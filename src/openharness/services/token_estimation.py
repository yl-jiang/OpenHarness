"""Simple token estimation utilities."""

from __future__ import annotations

import importlib
from functools import lru_cache
from typing import Protocol, cast


_DEFAULT_TIKTOKEN_ENCODING = "cl100k_base"


class _TokenEncoder(Protocol):
    def encode(self, text: str) -> list[int]: ...


class _TiktokenModule(Protocol):
    def get_encoding(self, encoding_name: str) -> _TokenEncoder: ...


@lru_cache(maxsize=1)
def _get_tiktoken_encoder() -> _TokenEncoder | None:
    try:
        tiktoken = cast(_TiktokenModule, importlib.import_module("tiktoken"))
    except ModuleNotFoundError:
        return None
    return tiktoken.get_encoding(_DEFAULT_TIKTOKEN_ENCODING)


def estimate_tokens(text: str) -> int:
    """Estimate tokens from plain text with tiktoken when available."""
    if not text:
        return 0
    encoder = _get_tiktoken_encoder()
    if encoder is not None:
        return len(encoder.encode(text))
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(messages: list[str]) -> int:
    """Estimate tokens for a collection of message strings."""
    return sum(estimate_tokens(message) for message in messages)
