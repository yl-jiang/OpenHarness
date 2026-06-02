import pytest

from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock

from solo.agent import OpenHarnessSoloAgent
from solo.core.store import SoloStore


class _Settings:
    model = "test-model"
    max_tokens = 8192

    def merge_cli_overrides(self, *, active_profile=None, model=None):
        return self


class _TextClient:
    def __init__(self, output: str, *, input_tokens: int = 1, output_tokens: int = 1) -> None:
        self.output = output
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(self, request):
        self.requests.append(request)
        message = ConversationMessage(role="assistant", content=[TextBlock(text=self.output)])
        yield ApiMessageCompleteEvent(
            message=message,
            usage=UsageSnapshot(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
            ),
            stop_reason="end_turn",
        )


@pytest.mark.asyncio
async def test_report_generation_uses_full_configured_token_budget(monkeypatch):
    client = _TextClient("insight report")
    monkeypatch.setattr("solo.agent.load_settings", lambda: _Settings())
    agent = OpenHarnessSoloAgent(api_client=client)

    content = await agent.generate_report("monthly", [{"summary": "记录"}], "context")

    assert content == "insight report"
    assert client.requests[0].max_tokens == 8192


@pytest.mark.asyncio
async def test_report_generation_rejects_empty_model_body(monkeypatch):
    client = _TextClient("")
    monkeypatch.setattr("solo.agent.load_settings", lambda: _Settings())
    agent = OpenHarnessSoloAgent(api_client=client)

    with pytest.raises(RuntimeError, match="empty response"):
        await agent.generate_report("monthly", [{"summary": "记录"}], "context")


@pytest.mark.asyncio
async def test_agent_records_model_usage_summary(tmp_path, monkeypatch):
    client = _TextClient("insight report", input_tokens=13, output_tokens=5)
    store = SoloStore(tmp_path / ".solo")
    monkeypatch.setattr("solo.agent.load_settings", lambda: _Settings())
    agent = OpenHarnessSoloAgent(api_client=client, record_model_call=store.record_llm_call)

    await agent.generate_report("monthly", [{"summary": "记录"}], "context")

    usage = store.llm_usage_summary()
    assert usage["total_calls"] == 1
    assert usage["total_input_tokens"] == 13
    assert usage["total_output_tokens"] == 5
    assert usage["models"] == [
        {"model": "test-model", "count": 1, "input_tokens": 13, "output_tokens": 5}
    ]
