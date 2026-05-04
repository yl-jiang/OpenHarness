"""API error types for OpenHarness."""

from __future__ import annotations


class OpenHarnessApiError(RuntimeError):
    """Base class for upstream API failures."""


class AuthenticationFailure(OpenHarnessApiError):
    """Raised when the upstream service rejects the provided credentials."""


class RateLimitFailure(OpenHarnessApiError):
    """Raised when the upstream service rejects the request due to rate limits."""


class RequestFailure(OpenHarnessApiError):
    """Raised for generic request or transport failures."""


_PROMPT_TOO_LONG_ERROR_MARKERS = (
    "prompt too long",
    "context_length_exceeded",
    "context length",
    "maximum context",
    "context window",
    "input tokens exceed",
    "messages resulted in",
    "reduce the length of the messages",
    "configured limit",
    "too many tokens",
    "too large for the model",
    "maximum context length",
    "exceed_context",
    "exceeds the available context size",
    "available context size",
)


def is_prompt_too_long_error(exc: Exception) -> bool:
    """Return True for common provider context-window overflow errors."""
    text = str(exc).lower()
    return any(marker in text for marker in _PROMPT_TOO_LONG_ERROR_MARKERS)
