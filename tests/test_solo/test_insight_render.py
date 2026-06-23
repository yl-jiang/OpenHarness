"""Tests for the insight Markdown renderer (solo/core/insight_render.py)."""
from __future__ import annotations

from solo.core.insight_render import render_insight_markdown


def test_minimal_insight():
    insight = {
        "headline": "Test headline",
        "narrative": "Test narrative",
        "blind_spots": [],
        "insights": [],
        "recommendations": [],
    }
    md = render_insight_markdown(insight, "finance", "weekly")
    assert "财务周报洞察" in md
    assert "Test headline" in md
    assert "Test narrative" in md


def test_full_insight():
    insight = {
        "headline": "周五消费黑洞",
        "narrative": "本周消费模式异常",
        "period_comparison": [
            {"metric": "总支出", "current": 8200, "previous": 6100, "delta_pct": 34.4, "direction": "up", "unit": "¥"},
        ],
        "blind_spots": [
            {"title": "周五支出高", "why": "周五均值超出日常47%", "evidence": "6/7 ¥530", "severity": "watch"},
        ],
        "insights": [
            {"icon": "🔍", "title": "外卖依赖", "analysis": "外卖占比上升", "evidence": ["6/12 ¥58"], "severity": "info"},
        ],
        "patterns": [
            {"name": "睡眠↔情绪", "strength": "strong", "detail": "短睡后情绪负面率68%"},
        ],
        "recommendations": [
            {"action": "设置日限额", "rationale": "周五平均超出47%", "expected_signal": "下周五<¥350"},
        ],
        "metrics": [
            {"label": "日均支出", "value": 273.5, "unit": "¥", "trend": [210, 280, 310]},
        ],
    }
    md = render_insight_markdown(insight, "finance", "weekly")
    assert "周五消费黑洞" in md
    assert "周期对比" in md
    assert "8200¥" in md
    assert "你可能忽视的" in md
    assert "深度洞察" in md
    assert "模式识别" in md
    assert "行动建议" in md


def test_health_domain():
    insight = {
        "headline": "睡眠持续缩短",
        "narrative": "本周睡眠质量下降",
        "blind_spots": [],
        "insights": [],
        "recommendations": [],
    }
    md = render_insight_markdown(insight, "health", "monthly")
    assert "健康月报洞察" in md


def test_empty_fields():
    insight = {
        "headline": "",
        "narrative": "",
        "blind_spots": [],
        "insights": [],
        "recommendations": [],
    }
    md = render_insight_markdown(insight, "finance", "weekly")
    assert "财务周报洞察" in md
    # Should not crash on empty strings
