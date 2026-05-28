"""Anthropic API client wrapper with retry logic."""

from __future__ import annotations

import asyncio
import json
import re
from openharness.utils.log import get_logger
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Protocol

from anthropic import APIError, APIStatusError, AsyncAnthropic

from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)
from openharness.auth.external import (
    claude_attribution_header,
    claude_oauth_betas,
    claude_oauth_headers,
    get_claude_code_session_id,
)
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock, assistant_message_from_api

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 30.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}
OAUTH_BETA_HEADER = "oauth-2025-04-20"


@dataclass(frozen=True)
class ApiMessageRequest:
    """Input parameters for a model invocation."""

    model: str
    messages: list[ConversationMessage]
    system_prompt: str | None = None
    max_tokens: int = 4096
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ApiTextDeltaEvent:
    """Incremental text produced by the model."""

    text: str


@dataclass(frozen=True)
class ApiReasoningDeltaEvent:
    """Incremental reasoning/thinking text from the model."""

    text: str


@dataclass(frozen=True)
class ApiMessageCompleteEvent:
    """Terminal event containing the full assistant message."""

    message: ConversationMessage
    usage: UsageSnapshot
    stop_reason: str | None = None
    truncated_tool_calls: int = 0


@dataclass(frozen=True)
class ApiRetryEvent:
    """A recoverable upstream failure that will be retried automatically."""

    message: str
    attempt: int
    max_attempts: int
    delay_seconds: float


ApiStreamEvent = ApiTextDeltaEvent | ApiReasoningDeltaEvent | ApiMessageCompleteEvent | ApiRetryEvent


class SupportsStreamingMessages(Protocol):
    """Protocol used by the query engine in tests and production."""

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Yield streamed events for the request."""


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is retryable."""
    if isinstance(exc, APIStatusError):
        return exc.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, APIError):
        return True  # Network errors are retryable
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return False


def _get_retry_delay(attempt: int, exc: Exception | None = None) -> float:
    """Calculate delay with exponential backoff and jitter."""
    import random

    # Check for Retry-After header
    if isinstance(exc, APIStatusError):
        retry_after = getattr(exc, "headers", {})
        if hasattr(retry_after, "get"):
            val = retry_after.get("retry-after")
            if val:
                try:
                    return min(float(val), MAX_DELAY)
                except (ValueError, TypeError):
                    pass

    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


class AnthropicApiClient:
    """Thin wrapper around the Anthropic async SDK with retry logic."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        auth_token: str | None = None,
        base_url: str | None = None,
        claude_oauth: bool = False,
        auth_token_resolver: Callable[[], str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._auth_token = auth_token
        self._base_url = base_url
        self._claude_oauth = claude_oauth
        self._auth_token_resolver = auth_token_resolver
        self._session_id = get_claude_code_session_id() if claude_oauth else ""
        self._client = self._create_client()

    def _create_client(self) -> AsyncAnthropic:
        kwargs: dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._auth_token:
            kwargs["auth_token"] = self._auth_token
            kwargs["default_headers"] = (
                claude_oauth_headers()
                if self._claude_oauth
                else {"anthropic-beta": OAUTH_BETA_HEADER}
            )
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return AsyncAnthropic(**kwargs)

    def _refresh_client_auth(self) -> None:
        if not self._claude_oauth or self._auth_token_resolver is None:
            return
        next_token = self._auth_token_resolver()
        if next_token and next_token != self._auth_token:
            self._auth_token = next_token
            self._client = self._create_client()

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Yield text deltas and the final assistant message with retry on transient errors."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                self._refresh_client_auth()
                async for event in self._stream_once(request):
                    yield event
                return  # Success
            except OpenHarnessApiError:
                raise  # Auth errors are not retried
            except Exception as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not _is_retryable(exc):
                    if isinstance(exc, APIError):
                        raise _translate_api_error(exc) from exc
                    raise RequestFailure(str(exc)) from exc

                delay = _get_retry_delay(attempt, exc)
                status = getattr(exc, "status_code", "?")
                logger.warning(
                    "API request failed (attempt %d/%d, status=%s), retrying in %.1fs: %s",
                    attempt + 1, MAX_RETRIES + 1, status, delay, exc,
                )
                yield ApiRetryEvent(
                    message=str(exc),
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES + 1,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            if isinstance(last_error, APIError):
                raise _translate_api_error(last_error) from last_error
            raise RequestFailure(str(last_error)) from last_error

    async def _stream_once(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Single attempt at streaming a message."""
        params: dict[str, Any] = {
            "model": request.model,
            "messages": [message.to_api_param() for message in request.messages],
            "max_tokens": request.max_tokens,
        }
        if request.system_prompt:
            params["system"] = request.system_prompt
        if self._claude_oauth:
            attribution = claude_attribution_header()
            params["system"] = (
                f"{attribution}\n{params['system']}"
                if params.get("system")
                else attribution
            )
        if request.tools:
            params["tools"] = request.tools
        if self._claude_oauth:
            params["betas"] = claude_oauth_betas()
            params["metadata"] = {
                "user_id": json.dumps(
                    {
                        "device_id": "openharness",
                        "session_id": self._session_id,
                        "account_uuid": "",
                    },
                    separators=(",", ":"),
                )
            }
            params["extra_headers"] = {"x-client-request-id": str(uuid.uuid4())}

        try:
            stream_api = self._client.beta.messages if self._claude_oauth else self._client.messages
            async with stream_api.stream(**params) as stream:
                # Buffer text to strip <tool_call>...</tool_call> XML that some
                # models (e.g. MiMo) embed in text content instead of using
                # proper tool_use input fields.
                _tc_buf = ""

                async for event in stream:
                    event_type = getattr(event, "type", None)
                    # The Anthropic SDK (≥0.89) emits a processed ThinkingEvent
                    # (type="thinking") for each thinking_delta SSE, in addition
                    # to the raw content_block_delta. Handle only the processed
                    # event to avoid yielding reasoning text twice.
                    if event_type == "thinking":
                        thinking_text = getattr(event, "thinking", "") or getattr(event, "text", "")
                        if thinking_text:
                            yield ApiReasoningDeltaEvent(text=thinking_text)
                        continue
                    if event_type != "content_block_delta":
                        continue
                    delta = getattr(event, "delta", None)
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "thinking_delta":
                        # Skip: already handled via the processed "thinking" event above.
                        continue
                    if delta_type != "text_delta":
                        continue
                    text = getattr(delta, "text", "")
                    if text:
                        _tc_buf += text
                        visible, _tc_buf = _strip_xml_tool_calls(_tc_buf)
                        if visible:
                            yield ApiTextDeltaEvent(text=visible)

                # Flush any remaining buffer that wasn't a tool_call.
                if _tc_buf and not _TOOL_CALL_RE.search(_tc_buf):
                    yield ApiTextDeltaEvent(text=_tc_buf)

                final_message = await stream.get_final_message()
        except APIError as exc:
            if isinstance(exc, APIStatusError) and exc.status_code in RETRYABLE_STATUS_CODES:
                raise  # Let retry logic handle it
            raise _translate_api_error(exc) from exc

        # Post-process: fill empty tool_use inputs from XML in text content.
        msg = assistant_message_from_api(final_message)
        _patch_empty_tool_inputs(msg)

        usage = getattr(final_message, "usage", None)
        yield ApiMessageCompleteEvent(
            message=msg,
            usage=UsageSnapshot(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            ),
            stop_reason=getattr(final_message, "stop_reason", None),
        )


def _translate_api_error(exc: APIError) -> OpenHarnessApiError:
    name = exc.__class__.__name__
    if name in {"AuthenticationError", "PermissionDeniedError"}:
        return AuthenticationFailure(str(exc))
    if name == "RateLimitError":
        return RateLimitFailure(str(exc))
    return RequestFailure(str(exc))


# ---------------------------------------------------------------------------
# XML tool-call extraction for models that embed tool calls as text.
# Some models (e.g. MiMo) emit tool_use blocks with empty input AND put the
# actual call in text as: <tool_call><function=name><parameter=k>v</parameter>
# </function></tool_call>
# ---------------------------------------------------------------------------

_TOOL_CALL_OPEN = "<tool_call>"
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>",
    re.DOTALL,
)
_PARAM_RE = re.compile(r"<parameter=(\w+)>(.*?)</parameter>", re.DOTALL)


def _parse_xml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Extract tool calls from XML markup in text.

    Returns a list of dicts: [{"name": ..., "input": {...}}, ...]
    """
    results: list[dict[str, Any]] = []
    for match in _TOOL_CALL_RE.finditer(text):
        name = match.group(1)
        body = match.group(2)
        params: dict[str, Any] = {}
        for pm in _PARAM_RE.finditer(body):
            params[pm.group(1)] = pm.group(2)
        results.append({"name": name, "input": params})
    return results


def _strip_xml_tool_calls(buf: str) -> tuple[str, str]:
    """Strip complete <tool_call>...</tool_call> blocks from buffered text.

    Returns (visible_text, leftover_buffer).
    The leftover is retained when an opening <tool_call> has no closing tag yet.
    """
    cleaned = _TOOL_CALL_RE.sub("", buf)

    # Hold back any unclosed <tool_call> tag for the next chunk.
    open_idx = cleaned.find(_TOOL_CALL_OPEN)
    if open_idx != -1:
        return cleaned[:open_idx], cleaned[open_idx:]

    # Handle partial opening tag at the end of buffer.
    max_prefix = min(len(cleaned), len(_TOOL_CALL_OPEN) - 1)
    for prefix_len in range(max_prefix, 0, -1):
        if _TOOL_CALL_OPEN.startswith(cleaned[-prefix_len:]):
            return cleaned[:-prefix_len], cleaned[-prefix_len:]

    return cleaned, ""


def _patch_empty_tool_inputs(msg: ConversationMessage) -> None:
    """Fill empty tool_use inputs from XML tool calls found in preceding text blocks.

    Some models (e.g. MiMo) emit a tool_use block with empty input={} while putting
    the actual parameters as XML text. This function extracts them and patches in-place.
    """
    # Collect all text content to look for XML tool calls.
    full_text = ""
    for block in msg.content:
        if isinstance(block, TextBlock):
            full_text += block.text

    if not full_text or _TOOL_CALL_OPEN not in full_text:
        return

    parsed_calls = _parse_xml_tool_calls(full_text)
    if not parsed_calls:
        return

    # Build a lookup: tool name → parsed input (use first match per name).
    parsed_by_name: dict[str, dict[str, Any]] = {}
    for pc in parsed_calls:
        parsed_by_name.setdefault(pc["name"], pc["input"])

    # Patch tool_use blocks with empty input.
    for block in msg.content:
        if isinstance(block, ToolUseBlock) and not block.input:
            extracted = parsed_by_name.get(block.name)
            if extracted:
                block.input = extracted

    # Strip the XML tool call text from text blocks so it doesn't persist in conversation.
    for i, block in enumerate(msg.content):
        if isinstance(block, TextBlock) and _TOOL_CALL_OPEN in block.text:
            stripped = _TOOL_CALL_RE.sub("", block.text).strip()
            # Use object.__setattr__ for frozen/validated models if needed,
            # but Pydantic v2 BaseModel fields are mutable by default.
            block.text = stripped
