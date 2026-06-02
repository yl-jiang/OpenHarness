"""Helpers for observing model requests without changing provider clients."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from openharness.api.client import ApiMessageRequest, ApiStreamEvent, SupportsStreamingMessages

ModelCallRecorder = Callable[[str], None]


class ModelCallRecordingClient:
    """Proxy an API client and record each requested model name."""

    def __init__(
        self,
        api_client: SupportsStreamingMessages,
        recorder: ModelCallRecorder,
    ) -> None:
        self._api_client = api_client
        self._recorder = recorder

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        self._recorder(request.model)
        async for event in self._api_client.stream_message(request):
            yield event


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
