"""Helpers for observing model requests without changing provider clients."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiStreamEvent,
    SupportsStreamingMessages,
)


class ModelCallRecorder(Protocol):
    def __call__(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        created_at: str | None = None,
    ) -> None:
        """Persist one logical model invocation."""


class ModelCallRecordingClient:
    """Proxy an API client and record each logical model invocation."""

    def __init__(
        self,
        api_client: SupportsStreamingMessages,
        recorder: ModelCallRecorder,
    ) -> None:
        self._api_client = api_client
        self._recorder = recorder

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        recorded = False
        try:
            async for event in self._api_client.stream_message(request):
                if isinstance(event, ApiMessageCompleteEvent) and not recorded:
                    self._recorder(
                        request.model,
                        input_tokens=max(0, int(event.usage.input_tokens)),
                        output_tokens=max(0, int(event.usage.output_tokens)),
                    )
                    recorded = True
                yield event
        finally:
            if not recorded:
                self._recorder(request.model)


def wrap_with_model_call_recorder(
    api_client: SupportsStreamingMessages,
    recorder: ModelCallRecorder | None,
) -> SupportsStreamingMessages:
    """Return a client proxy that records model calls when a recorder is provided."""

    if recorder is None:
        return api_client
    if isinstance(api_client, ModelCallRecordingClient):
        return api_client
    return ModelCallRecordingClient(api_client, recorder)
