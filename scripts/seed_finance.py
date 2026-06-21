"""Seed finance_transactions from existing solo records.

Reads all records, identifies finance-related content with specific amounts,
and creates structured finance transactions. Idempotent: skips if already seeded.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solo.core.models import SoloFinanceTransaction
from solo.core.store import SoloStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _txn(
    date: str,
    txn_type: str,
    category: str,
    amount: float,
    *,
    description: str = "",
    counterparty: str = "",
    account: str = "",
    tags: str = "",
    currency: str = "CNY",
) -> SoloFinanceTransaction:
    return SoloFinanceTransaction(
        id=uuid4().hex[:12],
        date=date,
        type=txn_type,
        category=category,
        amount=amount,
        currency=currency,
        account=account,
        counterparty=counterparty,
        description=description,
        tags=tags,
        source="import",
        created_at=_now(),
        updated_at=_now(),
    )


# ── Seed data: extracted from records with specific amounts ────────

SEED_TRANSACTIONS: list[SoloFinanceTransaction] = [
    # 2026-06-20
    _txn("2026-06-20", "expense", "housing", 29.55,
         description="腾讯王卡话费代扣", account="农业银行信用卡", tags="话费,代扣"),
    _txn("2026-06-20", "expense", "shopping", 69.7,
         description="滨特尔净水器PP棉滤芯4支（淘宝）", account="淘宝", tags="净水器,滤芯"),
    _txn("2026-06-20", "expense", "shopping", 12.67,
         description="淘宝: 风扇配件", account="淘宝", tags="风扇,配件"),
    _txn("2026-06-20", "income", "refund", 100.0,
         description="戴尔U2722QE晒单返100元京东E卡", tags="晒单,返现"),

    # 2026-06-19
    _txn("2026-06-19", "expense", "groceries", 18.0,
         description="早餐", tags="早餐"),
    _txn("2026-06-19", "expense", "groceries", 67.0,
         description="排骨", tags="买菜"),
    _txn("2026-06-19", "expense", "groceries", 26.0,
         description="买菜", tags="买菜"),
    _txn("2026-06-19", "income", "gift", 3000.0,
         description="爷爷给了图图3000元", counterparty="爷爷", tags="红包,图图"),

    # 2026-06-18
    _txn("2026-06-18", "expense", "shopping", 4196.56,
         description="戴尔U2722QE 27寸显示器（京东白条6期免息，原价4499）",
         account="京东白条", tags="显示器,戴尔,分期"),
    _txn("2026-06-18", "expense", "shopping", 5.66,
         description="拼多多: 肥皂架", account="拼多多", tags="家居"),
    _txn("2026-06-18", "expense", "shopping", 29.9,
         description="拼多多: 羊脂皂", account="拼多多", tags="日用"),

    # 2026-06-17
    _txn("2026-06-17", "expense", "health", 25.0,
         description="新华医院挂号费（医保报销部分已扣）", tags="医院,挂号"),

    # 2026-06-16
    _txn("2026-06-16", "expense", "groceries", 74.06,
         description="零食很忙", tags="零食"),

    # 2026-06-15
    _txn("2026-06-15", "expense", "groceries", 59.0,
         description="一箱桃子", tags="水果"),

    # 2026-06-14
    _txn("2026-06-14", "expense", "groceries", 9.0,
         description="玉米+蒜苗", tags="买菜"),
    _txn("2026-06-14", "expense", "groceries", 36.0,
         description="肋排", tags="买菜"),
    _txn("2026-06-14", "expense", "groceries", 15.8,
         description="一串葡萄", tags="水果"),
    _txn("2026-06-14", "expense", "dining", 26.5,
         description="KFC早餐周末四拼", tags="KFC,早餐"),

    # 2026-06-12
    _txn("2026-06-12", "expense", "groceries", 25.0,
         description="夏黑葡萄", tags="水果"),

    # 2026-06-11
    _txn("2026-06-11", "expense", "groceries", 14.0,
         description="潮汕卤水鹅凉菜", tags="卤味"),
    _txn("2026-06-11", "expense", "groceries", 19.0,
         description="半个西瓜", tags="水果"),

    # 2026-06-07
    _txn("2026-06-07", "expense", "education", 59.0,
         description="阿里Coder QODER 月订阅", tags="AI工具,订阅"),

    # 2026-06-06
    _txn("2026-06-06", "expense", "dining", 23.0,
         description="比星咖啡拿铁大杯（原价36.9）", tags="咖啡"),

    # 2026-06-05
    _txn("2026-06-05", "expense", "dining", 49.9,
         description="麦当劳双人套餐外卖（原价121）", tags="麦当劳,外卖"),

    # 2026-06-04
    _txn("2026-06-04", "expense", "groceries", 50.0,
         description="零食很忙", tags="零食"),

    # 2026-06-02
    _txn("2026-06-02", "expense", "education", 467.0,
         description="图图6月学费", counterparty="图图", tags="学费,图图"),

    # 2026-06-01
    _txn("2026-06-01", "expense", "groceries", 57.6,
         description="好特卖: 零食+电池+瓜子等（原价80.3）", tags="好特卖,零食"),
    _txn("2026-06-01", "expense", "dining", 30.0,
         description="妯娌至尊老鸭粉丝汤", tags="外卖"),

    # 2026-05-30
    _txn("2026-05-30", "expense", "dining", 23.9,
         description="KFC早餐四拼", tags="KFC,早餐"),

    # 2026-05-29
    _txn("2026-05-29", "expense", "dining", 45.9,
         description="麦当劳外卖", tags="麦当劳,外卖"),

    # 2026-05-27
    _txn("2026-05-27", "expense", "education", 100.0,
         description="小米MiMo充值", tags="AI工具,充值"),

    # 2026-05-22
    _txn("2026-05-22", "expense", "dining", 54.6,
         description="麦当劳四件套", tags="麦当劳"),
    _txn("2026-05-22", "expense", "groceries", 11.0,
         description="杨梅", tags="水果"),

    # 2026-05-21
    _txn("2026-05-21", "expense", "dining", 184.0,
         description="牛New寿喜烧自助餐", tags="自助,日料"),

    # 2026-05-20
    _txn("2026-05-20", "expense", "dining", 61.5,
         description="老乡鸡外卖", tags="外卖"),

    # 2026-05-19
    _txn("2026-05-19", "expense", "education", 100.0,
         description="阿里百炼充值（图片转文本）", tags="AI工具,充值"),
]


def seed() -> None:
    store = SoloStore(Path.home() / ".solo")

    # Check if already seeded (by source="import" tag)
    existing = store.list_finance_transactions()
    if existing:
        print(f"Finance table already has {len(existing)} records. Skipping seed.")
        print("To re-seed, delete existing records first.")
        return

    for txn in SEED_TRANSACTIONS:
        store.add_finance_transaction(txn)

    print(f"Seeded {len(SEED_TRANSACTIONS)} finance transactions.")

    # Summary
    total_expense = sum(t.amount for t in SEED_TRANSACTIONS if t.type == "expense")
    total_income = sum(t.amount for t in SEED_TRANSACTIONS if t.type == "income")
    print(f"  Total expenses: ¥{total_expense:,.2f} ({sum(1 for t in SEED_TRANSACTIONS if t.type == 'expense')} items)")
    print(f"  Total income:   ¥{total_income:,.2f} ({sum(1 for t in SEED_TRANSACTIONS if t.type == 'income')} items)")

    # By category
    from collections import Counter
    cats = Counter(t.category for t in SEED_TRANSACTIONS)
    print(f"\n  By category:")
    for cat, count in cats.most_common():
        cat_total = sum(t.amount for t in SEED_TRANSACTIONS if t.category == cat)
        print(f"    {cat:15s} {count:3d} items  ¥{cat_total:,.2f}")


if __name__ == "__main__":
    seed()
