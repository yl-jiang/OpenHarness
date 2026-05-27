import pytest

from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock

from wolo.agent import OpenHarnessWoloAgent


class _Settings:
    model = "test-model"
    max_tokens = 8192

    def merge_cli_overrides(self, *, active_profile=None, model=None):
        return self


class _TextClient:
    def __init__(self, output: str) -> None:
        self.output = output
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(self, request):
        self.requests.append(request)
        message = ConversationMessage(role="assistant", content=[TextBlock(text=self.output)])
        yield ApiMessageCompleteEvent(
            message=message,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


@pytest.mark.asyncio
async def test_report_generation_uses_full_configured_token_budget(monkeypatch):
    client = _TextClient("insight report")
    monkeypatch.setattr("wolo.agent.load_settings", lambda: _Settings())
    agent = OpenHarnessWoloAgent(api_client=client)

    content = await agent.generate_report("monthly", [{"summary": "记录"}], "context")

    assert content == "insight report"
    assert client.requests[0].max_tokens == 8192
