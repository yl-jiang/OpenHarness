"""Tests for image_to_text tool and multimodal detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.api.provider import is_model_multimodal
from openharness.config.settings import VisionModelConfig
from openharness.engine.types import ToolMetadataKey
from openharness.tools.base import ToolExecutionContext
from openharness.tools.image_to_text_tool import ImageToTextTool, ImageToTextToolInput


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-sonnet-4-6", True),
        ("claude-opus-4-6", True),
        ("claude-3-5-sonnet-20241022", True),
        ("gpt-4o", True),
        ("gpt-4o-mini", True),
        ("o3-mini", True),
        ("gemini-2.5-flash", True),
        ("qwen2.5-vl-72b", True),
        ("deepseek-vl2", True),
        ("llava-v1.6-34b", True),
        ("pixtral-12b", True),
        ("kimi-k2.5", True),
        ("anthropic/claude-sonnet-4-6", True),
        ("openai/gpt-4o", True),
        ("claude-2.1", False),
        ("gpt-4", False),
        ("deepseek-chat", False),
        ("qwen-plus", False),
        ("kimi-k2", False),
        ("unknown-model-123", False),
        ("", False),
        ("openai/gpt-4", False),
    ],
)
def test_is_model_multimodal(model: str, expected: bool) -> None:
    assert is_model_multimodal(model) == expected


def test_image_to_text_input_defaults() -> None:
    inp = ImageToTextToolInput(image_data="iVBORw0KGgo=")

    assert inp.image_data == "iVBORw0KGgo="
    assert inp.media_type == "image/png"
    assert "image" in inp.prompt.lower()
    assert inp.max_tokens == 2048


@pytest.mark.asyncio
async def test_execute_no_input(tmp_path: Path) -> None:
    result = await ImageToTextTool().execute(ImageToTextToolInput(), ToolExecutionContext(cwd=tmp_path))

    assert result.is_error
    assert "provide either" in result.output


@pytest.mark.asyncio
async def test_execute_nonexistent_path(tmp_path: Path) -> None:
    result = await ImageToTextTool().execute(
        ImageToTextToolInput(image_path="/nonexistent/path/image.png"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert result.is_error
    assert "provide either" in result.output


@pytest.mark.asyncio
async def test_execute_no_vision_config(tmp_path: Path) -> None:
    result = await ImageToTextTool().execute(
        ImageToTextToolInput(image_data="iVBORw0KGgo="),
        ToolExecutionContext(cwd=tmp_path, metadata={ToolMetadataKey.VISION_MODEL_CONFIG.value: {}}),
    )

    assert result.is_error
    assert "vision model is not configured" in result.output


@pytest.mark.asyncio
async def test_execute_with_image_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    png_path = tmp_path / "test_image.png"
    png_path.write_bytes(
        bytes(
            [
                0x89,
                0x50,
                0x4E,
                0x47,
                0x0D,
                0x0A,
                0x1A,
                0x0A,
            ]
        )
    )

    async def fake_call_vision_model(**kwargs) -> str:
        assert kwargs["media_type"] == "image/png"
        assert kwargs["model"] == "gpt-4o"
        return "a tiny png"

    monkeypatch.setattr(ImageToTextTool, "_call_vision_model", staticmethod(fake_call_vision_model))
    result = await ImageToTextTool().execute(
        ImageToTextToolInput(image_path=str(png_path)),
        ToolExecutionContext(
            cwd=tmp_path,
            metadata={
                ToolMetadataKey.VISION_MODEL_CONFIG.value: {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "base_url": "",
                }
            },
        ),
    )

    assert result.is_error is False
    assert "a tiny png" in result.output


def test_image_to_text_is_read_only() -> None:
    assert ImageToTextTool().is_read_only(ImageToTextToolInput(image_data="data"))


def test_vision_model_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENHARNESS_VISION_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENHARNESS_VISION_API_KEY", "sk-env-key")
    monkeypatch.setenv("OPENHARNESS_VISION_BASE_URL", "https://api.example.com/v1")

    cfg = VisionModelConfig.from_env()

    assert cfg.model == "gpt-4o"
    assert cfg.api_key == "sk-env-key"
    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.is_configured


def test_tool_registered() -> None:
    from openharness.tools import create_default_tool_registry

    tool = create_default_tool_registry().get("image_to_text")

    assert tool is not None
    assert tool.name == "image_to_text"
    assert tool.input_model.__name__ == "ImageToTextToolInput"
