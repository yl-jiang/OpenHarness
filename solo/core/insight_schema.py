"""JSON schema for LLM structured output — InsightReport."""
from __future__ import annotations

INSIGHT_REPORT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "narrative": {"type": "string"},
        "period_comparison": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "current": {"type": "number"},
                    "previous": {"type": "number"},
                    "delta_pct": {"type": "number"},
                    "direction": {"type": "string", "enum": ["up", "down", "flat"]},
                    "unit": {"type": "string"},
                },
                "required": ["metric", "current", "previous", "delta_pct", "direction"],
                "additionalProperties": False,
            },
        },
        "blind_spots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "evidence": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "watch", "alert"]},
                },
                "required": ["title", "why", "evidence", "severity"],
                "additionalProperties": False,
            },
        },
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "icon": {"type": "string"},
                    "title": {"type": "string"},
                    "analysis": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "severity": {"type": "string", "enum": ["info", "watch", "alert"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "analysis", "evidence", "severity"],
                "additionalProperties": False,
            },
        },
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "strength": {"type": "string", "enum": ["strong", "moderate", "weak"]},
                    "detail": {"type": "string"},
                },
                "required": ["name", "strength", "detail"],
                "additionalProperties": False,
            },
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                    "expected_signal": {"type": "string"},
                },
                "required": ["action", "rationale", "expected_signal"],
                "additionalProperties": False,
            },
        },
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                    "trend": {"type": "array", "items": {"type": "number"}},
                    "comparison_value": {"type": "number"},
                    "comparison_label": {"type": "string"},
                },
                "required": ["label", "value", "unit"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "narrative", "blind_spots", "insights", "recommendations"],
    "additionalProperties": False,
}
