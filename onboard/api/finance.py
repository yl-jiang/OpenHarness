"""Finance API routes (solo-only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from onboard.services.solo_service import SoloService


router = APIRouter(prefix="/api/solo/finance", tags=["finance"])


def _service(workspace: str | None = None) -> SoloService:
    return SoloService(workspace)


@router.get("/overview")
def finance_overview(workspace: str | None = None) -> dict[str, Any]:
    return _service(workspace).finance_overview()


@router.get("/transactions")
def finance_transactions(
    type: str | None = None,
    category: str | None = None,
    account: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).list_finance_transactions(
        type=type, category=category, account=account,
        date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )


@router.get("/transactions/summary")
def finance_transactions_summary(
    type: str | None = None,
    days: int = Query(30, ge=1, le=365),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).finance_transactions_summary(type=type, days=days)


@router.get("/transactions/trend")
def finance_transactions_trend(
    days: int = Query(180, ge=1, le=3650),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).finance_transactions_trend(days=days)


@router.get("/transactions/daily")
def finance_transactions_daily(
    month: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).finance_transactions_daily(month=month)


@router.get("/invest/trend")
def finance_invest_trend(
    days: int = Query(180, ge=1, le=3650),
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).finance_invest_trend(days=days)


@router.get("/budgets")
def finance_budgets(
    active: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    return _service(workspace).finance_budgets(active=active)


# ── Restricted write operations ─────────────────────────────

@router.delete("/transactions/{txn_id}")
def delete_finance_transaction(
    txn_id: str,
    workspace: str | None = None,
) -> dict[str, bool]:
    return {"ok": _service(workspace).delete_finance_transaction(txn_id)}


@router.patch("/transactions/{txn_id}")
def update_finance_transaction(
    txn_id: str,
    updates: dict[str, Any],
    workspace: str | None = None,
) -> dict[str, bool]:
    return {"ok": _service(workspace).update_finance_transaction(txn_id, updates)}


@router.delete("/budgets/{b_id}")
def delete_finance_budget(
    b_id: str,
    workspace: str | None = None,
) -> dict[str, bool]:
    return {"ok": _service(workspace).delete_finance_budget(b_id)}


@router.patch("/budgets/{b_id}")
def update_finance_budget(
    b_id: str,
    updates: dict[str, Any],
    workspace: str | None = None,
) -> dict[str, bool]:
    return {"ok": _service(workspace).update_finance_budget(b_id, updates)}
