from __future__ import annotations

from openharness.engine.tool_result_normalizer import TextToolResultNormalizer


def test_text_tool_result_normalizer_keeps_small_output_inline(tmp_path) -> None:
    normalizer = TextToolResultNormalizer(
        artifact_dir=tmp_path,
        inline_chars=20,
        preview_chars=10,
    )

    result = normalizer.normalize(
        tool_name="demo",
        tool_use_id="toolu_demo",
        output="small output",
    )

    assert result.inline_content == "small output"
    assert result.artifact_path is None


def test_text_tool_result_normalizer_offloads_large_output(tmp_path) -> None:
    normalizer = TextToolResultNormalizer(
        artifact_dir=tmp_path,
        inline_chars=20,
        preview_chars=10,
    )

    result = normalizer.normalize(
        tool_name="demo/tool",
        tool_use_id="toolu_demo",
        output="abcdefghijklmnopqrstuvwxyz",
    )

    assert result.artifact_path is not None
    assert result.artifact_path.exists()
    assert result.artifact_path.read_text(encoding="utf-8") == "abcdefghijklmnopqrstuvwxyz"
    assert "[Tool output truncated]" in result.inline_content
    assert "Tool: demo/tool" in result.inline_content
    assert "Tool use id: toolu_demo" in result.inline_content
    assert "Original size: 26 chars" in result.inline_content
    assert "Preview:\nabcdefghij" in result.inline_content
    assert "demo_tool" in result.artifact_path.name
