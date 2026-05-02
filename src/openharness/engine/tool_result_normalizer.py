"""Normalize raw tool outputs before sending them back to the model."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from openharness.config.paths import get_data_dir
from openharness.services.tool_outputs import tool_output_inline_chars, tool_output_preview_chars


@dataclass(frozen=True)
class NormalizedToolResult:
    inline_content: str
    artifact_path: Path | None = None


class TextToolResultNormalizer:
    """Normalize text tool output with artifact offload for large content."""

    def __init__(
        self,
        *,
        artifact_dir: Path | None = None,
        inline_chars: int | None = None,
        preview_chars: int | None = None,
    ) -> None:
        self._artifact_dir = artifact_dir
        self._inline_chars = inline_chars
        self._preview_chars = preview_chars

    def normalize(self, *, tool_name: str, tool_use_id: str, output: str) -> NormalizedToolResult:
        inline_limit = self._inline_chars or tool_output_inline_chars()
        if len(output) <= inline_limit:
            return NormalizedToolResult(inline_content=output)

        artifact_path = self._artifact_path(tool_name)
        artifact_path.write_text(output, encoding="utf-8", errors="replace")
        preview_limit = self._preview_chars or tool_output_preview_chars()
        preview = output[:preview_limit]
        omitted = max(0, len(output) - len(preview))
        inline = (
            "[Tool output truncated]\n"
            f"Tool: {tool_name}\n"
            f"Tool use id: {tool_use_id}\n"
            f"Original size: {len(output)} chars\n"
            f"Full output saved to: {artifact_path}\n"
            f"Inline preview: first {len(preview)} chars"
        )
        if omitted:
            inline += f" ({omitted} chars omitted)"
        if preview:
            inline += f"\n\nPreview:\n{preview}"
        return NormalizedToolResult(inline_content=inline, artifact_path=artifact_path)

    def _artifact_path(self, tool_name: str) -> Path:
        artifact_dir = self._artifact_dir or _default_tool_artifact_dir()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{_safe_tool_artifact_name(tool_name)}-{uuid4().hex[:12]}.txt"


def _default_tool_artifact_dir() -> Path:
    return get_data_dir() / "tool_artifacts"


def _safe_tool_artifact_name(tool_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name.strip())
    return (normalized or "tool")[:80]
