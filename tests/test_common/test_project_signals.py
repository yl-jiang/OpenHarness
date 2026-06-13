"""Tests for common/project_ai/signals.py — state analysis and checkins."""
from __future__ import annotations

import asyncio
from typing import Any

from common.project_ai.signals import (
    _analyze_project,
    _suggest_next_action,
    _deterministic_checkin,
    _deterministic_summary,
    analyze_project_state,
    generate_daily_snapshot,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_detail(**overrides) -> dict[str, Any]:
    base = {
        "id": "p1",
        "title": "Test Project",
        "status": "active",
        "risk_status": "normal",
        "completion_pct": 50,
        "milestone_count": 4,
        "completed_milestone_count": 2,
        "linked_record_count": 10,
        "linked_todo_count": 5,
        "completed_linked_todo_count": 2,
        "activity_7d": 3,
        "activity_30d": 12,
        "last_activity_at": "2026-06-11T10:00:00+00:00",
        "open_blocker_count": 0,
        "target_date": "",
        "created_at": "2026-05-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


class TestAnalyzeProject:
    def test_normal_project_no_signals(self) -> None:
        detail = _make_detail()
        signals = _analyze_project(detail)
        # Normal project with recent activity should have at least progress signal
        types = [s["signal_type"] for s in signals]
        assert "progress" in types

    def test_stale_detection(self) -> None:
        detail = _make_detail(last_activity_at="2026-01-01T00:00:00+00:00")
        signals = _analyze_project(detail)
        types = [s["signal_type"] for s in signals]
        assert "stale" in types

    def test_risk_overdue(self) -> None:
        detail = _make_detail(target_date="2026-01-01")
        signals = _analyze_project(detail)
        types = [s["signal_type"] for s in signals]
        assert "risk" in types
        risk = [s for s in signals if s["signal_type"] == "risk"][0]
        assert risk["severity"] == "critical"

    def test_risk_near_target(self) -> None:
        from datetime import datetime, timedelta, timezone
        target = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
        detail = _make_detail(target_date=target, completion_pct=30)
        signals = _analyze_project(detail)
        types = [s["signal_type"] for s in signals]
        assert "risk" in types

    def test_momentum_signal(self) -> None:
        detail = _make_detail(activity_7d=8)
        signals = _analyze_project(detail)
        types = [s["signal_type"] for s in signals]
        assert "momentum" in types

    def test_blocker_signal(self) -> None:
        detail = _make_detail(open_blocker_count=2)
        signals = _analyze_project(detail)
        types = [s["signal_type"] for s in signals]
        assert "blocker" in types
        blocker = [s for s in signals if s["signal_type"] == "blocker"][0]
        assert blocker["severity"] == "critical"

    def test_completion_signal(self) -> None:
        detail = _make_detail(completion_pct=100, milestone_count=3, completed_milestone_count=3)
        signals = _analyze_project(detail)
        types = [s["signal_type"] for s in signals]
        assert "milestone_evidence" in types


class TestSuggestNextAction:
    def test_blocker_first(self) -> None:
        detail = _make_detail(open_blocker_count=1)
        action = _suggest_next_action(detail)
        assert "blocker" in action.lower()

    def test_stale_project(self) -> None:
        detail = _make_detail(last_activity_at="2026-01-01T00:00:00+00:00")
        action = _suggest_next_action(detail)
        assert "resume" in action.lower() or "pause" in action.lower() or "archive" in action.lower()

    def test_at_risk(self) -> None:
        detail = _make_detail(risk_status="at_risk")
        action = _suggest_next_action(detail)
        assert "target" in action.lower() or "scope" in action.lower() or "reassess" in action.lower()

    def test_completed(self) -> None:
        detail = _make_detail(completion_pct=100)
        action = _suggest_next_action(detail)
        assert "complet" in action.lower() or "review" in action.lower()


class TestDeterministicCheckin:
    def test_at_risk_question(self) -> None:
        contexts = [
            {"id": "p1", "title": "Alpha", "risk_status": "at_risk",
             "activity_7d": 2, "open_blocker_count": 0,
             "completion_pct": 40, "recent_checkin": ""},
        ]
        questions = _deterministic_checkin(contexts)
        assert len(questions) >= 1
        assert "risk" in questions[0]["question"].lower() or "adjust" in questions[0]["question"].lower()

    def test_blocker_question(self) -> None:
        contexts = [
            {"id": "p1", "title": "Beta", "risk_status": "normal",
             "activity_7d": 3, "open_blocker_count": 2,
             "completion_pct": 60, "recent_checkin": ""},
        ]
        questions = _deterministic_checkin(contexts)
        assert len(questions) >= 1
        assert "blocker" in questions[0]["question"].lower() or "unblock" in questions[0]["question"].lower()

    def test_skip_recent_question(self) -> None:
        contexts = [
            {"id": "p1", "title": "Gamma", "risk_status": "normal",
             "activity_7d": 0, "open_blocker_count": 0,
             "completion_pct": None, "recent_checkin": "Same question?"},
        ]
        questions = _deterministic_checkin(contexts)
        # Should not repeat the same question
        for q in questions:
            assert q["question"] != "Same question?"

    def test_max_questions_limit(self) -> None:
        contexts = [
            {"id": f"p{i}", "title": f"Proj {i}", "risk_status": "at_risk",
             "activity_7d": 0, "open_blocker_count": 1,
             "completion_pct": 30, "recent_checkin": ""}
            for i in range(5)
        ]
        questions = _deterministic_checkin(contexts, max_questions=2)
        assert len(questions) <= 2


class TestDeterministicSummary:
    def test_normal_summary(self) -> None:
        detail = _make_detail()
        s = _deterministic_summary(detail)
        assert "on track" in s.lower()

    def test_at_risk_summary(self) -> None:
        detail = _make_detail(risk_status="at_risk")
        s = _deterministic_summary(detail)
        assert "at risk" in s.lower()


class TestProjectReviewPrompt:
    def test_review_prompt_exists(self) -> None:
        from common.project_ai.prompts import PROJECT_REVIEW_SYSTEM_PROMPT
        assert "retrospective" in PROJECT_REVIEW_SYSTEM_PROMPT.lower()
        assert "JSON" in PROJECT_REVIEW_SYSTEM_PROMPT

    def test_review_prompt_output_format(self) -> None:
        from common.project_ai.prompts import PROJECT_REVIEW_SYSTEM_PROMPT
        assert '"review"' in PROJECT_REVIEW_SYSTEM_PROMPT
        assert '"highlights"' in PROJECT_REVIEW_SYSTEM_PROMPT
        assert '"sentiment"' in PROJECT_REVIEW_SYSTEM_PROMPT
