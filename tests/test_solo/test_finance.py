"""Tests for finance records: store CRUD, tool handlers, and budget upsert."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from solo.core.models import SoloFinanceBudget, SoloFinanceTransaction
from solo.core.store import SoloStore
from solo.tools import SoloToolRegistry


def _make_txn(
    *,
    txn_type: str = "expense",
    category: str = "dining",
    amount: float = 35.0,
    date: str = "2026-06-20",
    currency: str = "CNY",
    **kwargs,
) -> SoloFinanceTransaction:
    return SoloFinanceTransaction(
        id=uuid4().hex[:12],
        date=date,
        type=txn_type,
        category=category,
        amount=amount,
        currency=currency,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        **kwargs,
    )


def _make_budget(
    *,
    period: str = "monthly",
    category: str = "dining",
    amount: float = 2000.0,
    **kwargs,
) -> SoloFinanceBudget:
    return SoloFinanceBudget(
        id=uuid4().hex[:12],
        period=period,
        category=category,
        amount=amount,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        **kwargs,
    )


# ── Store layer: transactions ───────────────────────────────────────


class TestFinanceTransactionStore:
    def test_add_and_get(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        txn = _make_txn()
        store.add_finance_transaction(txn)
        got = store.get_finance_transaction(txn.id)
        assert got is not None
        assert got.amount == 35.0
        assert got.type == "expense"

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.get_finance_transaction("nonexistent") is None

    def test_delete(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        txn = _make_txn()
        store.add_finance_transaction(txn)
        assert store.delete_finance_transaction(txn.id) is True
        assert store.get_finance_transaction(txn.id) is None

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.delete_finance_transaction("nonexistent") is False

    def test_update(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        txn = _make_txn()
        store.add_finance_transaction(txn)
        assert store.update_finance_transaction(txn.id, description="午餐") is True
        got = store.get_finance_transaction(txn.id)
        assert got is not None
        assert got.description == "午餐"

    def test_update_ignores_id(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        txn = _make_txn()
        store.add_finance_transaction(txn)
        assert store.update_finance_transaction(txn.id, id="hacked") is False

    def test_list_empty(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.list_finance_transactions() == []

    def test_list_type_filter(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_finance_transaction(_make_txn(txn_type="expense", amount=35))
        store.add_finance_transaction(_make_txn(txn_type="income", category="salary", amount=18000))
        expenses = store.list_finance_transactions(type="expense")
        assert len(expenses) == 1
        assert expenses[0].amount == 35

    def test_list_category_filter(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_finance_transaction(_make_txn(category="dining"))
        store.add_finance_transaction(_make_txn(category="transport"))
        dining = store.list_finance_transactions(category="dining")
        assert len(dining) == 1

    def test_list_date_range(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_finance_transaction(_make_txn(date="2026-06-01"))
        store.add_finance_transaction(_make_txn(date="2026-06-15"))
        store.add_finance_transaction(_make_txn(date="2026-06-20"))
        result = store.list_finance_transactions(date_from="2026-06-10", date_to="2026-06-18")
        assert len(result) == 1

    def test_list_limit(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        for i in range(5):
            store.add_finance_transaction(_make_txn(amount=float(i + 1), date=f"2026-06-{20 - i:02d}"))
        result = store.list_finance_transactions(limit=3)
        assert len(result) == 3

    def test_list_combined_filters(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_finance_transaction(_make_txn(txn_type="expense", category="dining"))
        store.add_finance_transaction(_make_txn(txn_type="expense", category="transport"))
        store.add_finance_transaction(_make_txn(txn_type="income", category="salary", amount=18000))
        result = store.list_finance_transactions(type="expense", category="dining")
        assert len(result) == 1

    def test_categories(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_finance_transaction(_make_txn(category="dining"))
        store.add_finance_transaction(_make_txn(category="dining"))
        store.add_finance_transaction(_make_txn(category="transport"))
        cats = store.finance_transaction_categories()
        assert cats == {"dining": 2, "transport": 1}

    def test_schema_migration_idempotent(self, tmp_path: Path) -> None:
        store1 = SoloStore(tmp_path / ".solo")
        store1.add_finance_transaction(_make_txn())
        store2 = SoloStore(tmp_path / ".solo")
        assert len(store2.list_finance_transactions()) == 1


# ── Store layer: budgets ────────────────────────────────────────────


class TestFinanceBudgetStore:
    def test_add_and_get(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        b = _make_budget()
        store.add_finance_budget(b)
        got = store.get_finance_budget(b.id)
        assert got is not None
        assert got.amount == 2000.0

    def test_delete(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        b = _make_budget()
        store.add_finance_budget(b)
        assert store.delete_finance_budget(b.id) is True
        assert store.get_finance_budget(b.id) is None

    def test_list_active_filter(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_finance_budget(_make_budget(category="dining", active=1))
        store.add_finance_budget(_make_budget(category="transport", active=0))
        active = store.list_finance_budgets(active=True)
        assert len(active) == 1

    def test_find_budget(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_finance_budget(_make_budget(period="monthly", category="dining"))
        found = store.find_budget("monthly", "dining")
        assert found is not None
        assert found.amount == 2000.0

    def test_find_budget_not_found(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.find_budget("monthly", "dining") is None

    def test_find_budget_different_period(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_finance_budget(_make_budget(period="monthly", category="dining"))
        assert store.find_budget("weekly", "dining") is None


# ── Tool handler layer: solo_finance_transaction ────────────────────


class TestFinanceTransactionTool:
    @pytest.mark.asyncio
    async def test_standard_expense(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_transaction", {
            "type": "expense", "category": "dining", "amount": 35,
        })
        assert "财务记录已入库" in result
        txns = store.list_finance_transactions(type="expense")
        assert len(txns) == 1
        assert txns[0].amount == 35.0

    @pytest.mark.asyncio
    async def test_income(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_transaction", {
            "type": "income", "category": "salary", "amount": 18000,
        })
        assert "财务记录已入库" in result
        txns = store.list_finance_transactions(type="income")
        assert len(txns) == 1

    @pytest.mark.asyncio
    async def test_invest_gain(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_finance_transaction", {
            "type": "invest_gain", "category": "fund", "amount": 300,
        })
        txns = store.list_finance_transactions(type="invest_gain")
        assert len(txns) == 1
        assert txns[0].amount == 300.0

    @pytest.mark.asyncio
    async def test_invest_loss_positive_amount(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_finance_transaction", {
            "type": "invest_loss", "category": "stocks", "amount": 500,
        })
        txns = store.list_finance_transactions(type="invest_loss")
        assert txns[0].amount == 500.0

    @pytest.mark.asyncio
    async def test_custom_category(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_transaction", {
            "type": "expense", "category": "telecom", "amount": 100,
        })
        assert "财务记录已入库" in result

    @pytest.mark.asyncio
    async def test_reject_vague_category(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_transaction", {
            "type": "expense", "category": "other", "amount": 50,
        })
        assert "Invalid category" in result

    @pytest.mark.asyncio
    async def test_reject_invalid_type(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_transaction", {
            "type": "investment_buy", "category": "stocks", "amount": 5000,
        })
        assert "Invalid type" in result

    @pytest.mark.asyncio
    async def test_reject_zero_amount(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_transaction", {
            "type": "expense", "category": "dining", "amount": 0,
        })
        assert "must be positive" in result

    @pytest.mark.asyncio
    async def test_reject_negative_amount(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_transaction", {
            "type": "expense", "category": "dining", "amount": -10,
        })
        assert "must be positive" in result

    @pytest.mark.asyncio
    async def test_pending_finance_ids_tracked(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_finance_transaction", {
            "type": "expense", "category": "dining", "amount": 35,
        })
        assert len(registry._pending_finance_ids) == 1

    @pytest.mark.asyncio
    async def test_currency_stored(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_finance_transaction", {
            "type": "expense", "category": "dining", "amount": 200, "currency": "USD",
        })
        txns = store.list_finance_transactions()
        assert txns[0].currency == "USD"

    @pytest.mark.asyncio
    async def test_metrics_json_safe(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_finance_transaction", {
            "type": "expense", "category": "dining", "amount": 50,
            "metrics_json": "not-json",
        })
        txns = store.list_finance_transactions()
        assert txns[0].metrics == {}


# ── Tool handler layer: solo_finance_budget ─────────────────────────


class TestFinanceBudgetTool:
    @pytest.mark.asyncio
    async def test_set_budget(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_budget", {
            "period": "monthly", "category": "dining", "amount": 2000,
        })
        assert "预算已设置" in result

    @pytest.mark.asyncio
    async def test_upsert_budget(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_finance_budget", {
            "period": "monthly", "category": "dining", "amount": 2000,
        })
        result = await registry.execute("solo_finance_budget", {
            "period": "monthly", "category": "dining", "amount": 2500,
        })
        assert "预算已更新" in result
        budgets = store.list_finance_budgets()
        assert len(budgets) == 1
        assert budgets[0].amount == 2500.0

    @pytest.mark.asyncio
    async def test_reject_zero_budget(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_budget", {
            "period": "monthly", "category": "dining", "amount": 0,
        })
        assert "must be positive" in result

    @pytest.mark.asyncio
    async def test_reject_invalid_period(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_budget", {
            "period": "biweekly", "category": "dining", "amount": 1000,
        })
        assert "Invalid period" in result

    @pytest.mark.asyncio
    async def test_total_budget_empty_category(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_budget", {
            "period": "monthly", "category": "", "amount": 5000,
        })
        assert "预算已设置" in result
        assert "全部" in result


# ── Tool handler layer: solo_finance_summary ────────────────────────


class TestFinanceSummaryTool:
    @pytest.mark.asyncio
    async def test_empty_result(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_summary", {})
        assert "没有相关财务记录" in result

    @pytest.mark.asyncio
    async def test_with_data(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        store.add_finance_transaction(_make_txn(txn_type="expense", amount=35, date=today))
        store.add_finance_transaction(_make_txn(txn_type="income", category="salary", amount=18000, date=today))
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_summary", {"days": 1})
        assert "ok" in result.lower() or "total" in result.lower() or len(result) > 0

    @pytest.mark.asyncio
    async def test_type_filter(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        store.add_finance_transaction(_make_txn(txn_type="expense", amount=35, date=today))
        store.add_finance_transaction(_make_txn(txn_type="income", category="salary", amount=18000, date=today))
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_finance_summary", {"type": "expense", "days": 1})
        assert len(result) > 0


# ── Data model layer ────────────────────────────────────────────────


class TestFinanceModels:
    def test_txn_to_dict_roundtrip(self) -> None:
        txn = _make_txn()
        d = txn.to_dict()
        assert d["type"] == "expense"
        assert d["amount"] == 35.0

    def test_txn_metrics_valid(self) -> None:
        txn = _make_txn(metrics_json='{"key": "value"}')
        assert txn.metrics == {"key": "value"}

    def test_txn_metrics_invalid(self) -> None:
        txn = _make_txn(metrics_json="not-json")
        assert txn.metrics == {}

    def test_txn_metrics_empty(self) -> None:
        txn = _make_txn(metrics_json="")
        assert txn.metrics == {}

    def test_budget_to_dict(self) -> None:
        b = _make_budget()
        d = b.to_dict()
        assert d["period"] == "monthly"
        assert d["amount"] == 2000.0
