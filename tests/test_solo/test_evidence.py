"""Tests for the evidence layer (solo/core/evidence.py)."""
from __future__ import annotations

import pytest

from solo.core.evidence import _z_score, _compute_prev_period


class TestZScore:
    def test_basic(self):
        assert _z_score(10, 5, 2.5) == 2.0

    def test_zero_std(self):
        assert _z_score(10, 5, 0) == 0.0

    def test_negative(self):
        assert _z_score(0, 5, 2.5) == -2.0

    def test_at_mean(self):
        assert _z_score(5, 5, 2.5) == 0.0


class TestComputePrevPeriod:
    def test_weekly(self):
        prev_start, prev_end = _compute_prev_period("2026-06-16", "2026-06-22")
        assert prev_start == "2026-06-09"
        assert prev_end == "2026-06-15"

    def test_monthly(self):
        prev_start, prev_end = _compute_prev_period("2026-06-01", "2026-06-30")
        assert prev_start == "2026-05-02"
        assert prev_end == "2026-05-31"

    def test_single_day(self):
        prev_start, prev_end = _compute_prev_period("2026-06-16", "2026-06-16")
        assert prev_start == "2026-06-15"
        assert prev_end == "2026-06-15"

class TestFinanceEvidence:
    """Smoke tests for build_finance_evidence with mock store."""

    @pytest.fixture()
    def mock_store(self):
        from dataclasses import dataclass
        from unittest.mock import MagicMock

        @dataclass
        class MockTxn:
            id: str = ""
            date: str = ""
            type: str = "expense"
            category: str = ""
            amount: float = 0.0
            counterparty: str = ""
            description: str = ""

        @dataclass
        class MockBudget:
            id: str = ""
            category: str = ""
            amount: float = 0.0

        store = MagicMock()
        store.list_finance_transactions.return_value = [
            MockTxn(id="1", date="2026-06-16", type="expense", category="dining", amount=100.0, counterparty="Starbucks"),
            MockTxn(id="2", date="2026-06-17", type="expense", category="dining", amount=50.0, counterparty="Starbucks"),
            MockTxn(id="3", date="2026-06-18", type="expense", category="transport", amount=30.0, counterparty=""),
            MockTxn(id="4", date="2026-06-19", type="income", category="", amount=5000.0, counterparty=""),
            MockTxn(id="5", date="2026-06-20", type="expense", category="dining", amount=80.0, counterparty="Starbucks"),
            MockTxn(id="6", date="2026-06-21", type="expense", category="dining", amount=60.0, counterparty="Starbucks"),
            MockTxn(id="7", date="2026-06-22", type="expense", category="dining", amount=70.0, counterparty="Starbucks"),
        ]
        store.list_finance_budgets.return_value = []
        return store

    def test_basic_structure(self, mock_store):
        from solo.core.evidence import build_finance_evidence
        result = build_finance_evidence(
            mock_store, start_date="2026-06-16", end_date="2026-06-22",
        )
        assert "period" in result
        assert "record_count" in result
        assert result["record_count"] == 7
        assert "total_expense" in result
        assert "total_income" in result
        assert "day_of_week_distribution" in result

    def test_prev_period_comparison(self, mock_store):
        from solo.core.evidence import build_finance_evidence
        result = build_finance_evidence(
            mock_store, start_date="2026-06-16", end_date="2026-06-22",
            prev_start="2026-06-09", prev_end="2026-06-15",
        )
        assert "prev_period" in result
        assert result["prev_period"]["start"] == "2026-06-09"

    def test_frequent_merchants_threshold(self, mock_store):
        from solo.core.evidence import build_finance_evidence
        result = build_finance_evidence(
            mock_store, start_date="2026-06-16", end_date="2026-06-22",
        )
        # Starbucks appears 5 times (>= 5 threshold)
        merchants = result["frequent_merchants"]
        starbucks = [m for m in merchants if m["counterparty"] == "Starbucks"]
        assert len(starbucks) == 1
        assert starbucks[0]["count"] == 5


class TestHealthEvidence:
    """Smoke tests for build_health_evidence with mock store."""

    @pytest.fixture()
    def mock_store(self):
        from dataclasses import dataclass
        from unittest.mock import MagicMock

        @dataclass
        class MockRecord:
            id: str = ""
            date: str = ""
            subject: str = "self"
            category: str = ""
            item: str = ""
            sleep_hours: float = 0.0
            mood: str = ""
            mood_sentiment: str = ""
            stress_level: str = ""
            exercise_type: str = ""
            metrics_json: str = "{}"

            @property
            def metrics(self):
                import json
                try:
                    return json.loads(self.metrics_json) if self.metrics_json else {}
                except (json.JSONDecodeError, TypeError):
                    return {}

        store = MagicMock()
        store.list_health_records.return_value = [
            MockRecord(id="1", date="2026-06-16", sleep_hours=7.5, mood="positive", mood_sentiment="positive", category=""),
            MockRecord(id="2", date="2026-06-17", sleep_hours=5.0, mood="negative", mood_sentiment="negative", category=""),
            MockRecord(id="3", date="2026-06-18", sleep_hours=6.8, mood="", category="exercise", exercise_type="running"),
            MockRecord(id="4", date="2026-06-19", sleep_hours=7.2, mood="anxious", mood_sentiment="negative", category=""),
            MockRecord(id="5", date="2026-06-20", sleep_hours=6.0, mood="", category="medication", item="aspirin"),
        ]
        return store

    def test_basic_structure(self, mock_store):
        from solo.core.evidence import build_health_evidence
        result = build_health_evidence(
            mock_store, start_date="2026-06-16", end_date="2026-06-20",
        )
        assert "period" in result
        assert "record_count" in result
        assert result["record_count"] == 5
        assert "sleep" in result
        assert "exercise" in result
        assert "medication_adherence" in result

    def test_prev_period_comparison(self, mock_store):
        from solo.core.evidence import build_health_evidence
        result = build_health_evidence(
            mock_store, start_date="2026-06-16", end_date="2026-06-20",
            prev_start="2026-06-09", prev_end="2026-06-15",
        )
        assert "prev_period" in result
        assert result["sleep"].get("prev_comparison") is not None or result["exercise"].get("prev_comparison") is not None

    def test_sleep_mood_correlation(self, mock_store):
        from solo.core.evidence import build_health_evidence
        result = build_health_evidence(
            mock_store, start_date="2026-06-16", end_date="2026-06-20",
        )
        assert "sleep_mood_correlation" in result



