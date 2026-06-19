"""Validate structured record data produced by the model.

This module defines a set of rules that model output must satisfy.
When violations are found, the caller can feed the errors back to
the model and ask it to correct them.

All field length limits are defined here as the single source of truth.
Other modules (prompts, tool descriptions) import these constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.constants import EMOTION_MAX_LENGTH, SUMMARY_MAX_LENGTH

FIELD_LIMITS: dict[str, int] = {
    "emotion": EMOTION_MAX_LENGTH,
    "summary": SUMMARY_MAX_LENGTH,
}


@dataclass(frozen=True)
class Rule:
    name: str
    field: str
    max_length: int | None
    description: str


RULES: list[Rule] = [
    Rule(
        "emotion_max_length",
        "emotion",
        EMOTION_MAX_LENGTH,
        f"emotion 必须是简短的情绪关键词（≤{EMOTION_MAX_LENGTH}字），不能是一句描述或分析",
    ),
    Rule(
        "summary_max_length",
        "summary",
        SUMMARY_MAX_LENGTH,
        f"summary 必须是一句简洁摘要（≤{SUMMARY_MAX_LENGTH}字），保持语义完整",
    ),
]


def validate_record_data(data: dict[str, Any]) -> list[str]:
    """Check *data* against all rules. Returns violation messages (empty = pass)."""
    errors: list[str] = []
    for rule in RULES:
        value = str(data.get(rule.field) or "")
        if not value:
            continue
        if rule.max_length is not None and len(value) > rule.max_length:
            errors.append(
                f"[{rule.field}] 当前值「{value}」共 {len(value)} 字，"
                f"超出上限 {rule.max_length} 字。{rule.description}"
            )

    summary = str(data.get("summary") or "")
    raw = str(data.get("corrected_content") or data.get("raw_content") or "")
    if summary and raw and len(raw) > 30 and len(summary) < len(raw) / 3:
        errors.append(
            f"[summary] 摘要「{summary}」仅 {len(summary)} 字，"
            f"原文 {len(raw)} 字，压缩过度导致语义丢失。"
            f"请保留关键细节，保持语法通顺"
        )

    return errors


def format_validation_feedback(errors: list[str]) -> str:
    """Format validation errors into a feedback string for the model."""
    lines = ["## 上次输出验证未通过，请修正以下问题："]
    for error in errors:
        lines.append(f"- {error}")
    return "\n".join(lines)
