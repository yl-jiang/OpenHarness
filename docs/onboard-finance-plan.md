# Onboard Finance Page -- 实施计划（精简版）

> 基于 [onboard-finance-design.md](./onboard-finance-design.md)（精简版）的分步实施计划。
> 核心架构：**2 张结构化表（交易/预算）+ 3 个 Agent 工具（transaction/budget/summary）+ 提示词优化 + Onboard 可视化页面**。
> **注意：Finance 模块仅适用于 solo，不适用于 wolo。**
>
> 本计划假设当前代码库已实现 health 模块（schema v7），财务模块从 schema v8 开始。
> 整体对齐 health 模块的落地模式（单/双表 + 两/三工具 + 一页面），工时约 **9–10h**（health ≈ 13.5h）。

---

## Phase 1: 数据模型与数据库表

### 目标

在 `SoloStore` 中新增 `finance_transactions` / `finance_budgets` 两张结构化表及对应 dataclass。

### 步骤

#### 1.1 新增两个 dataclass 模型

**文件**: `solo/core/models.py`

在 `SoloHealthRecord` 之后添加 `SoloFinanceTransaction`、`SoloFinanceBudget`（完整定义见 design §2.3）。两者均：
- `@dataclass(frozen=True)`
- 含 `metrics` property（统一解析 metrics_json，失败兜底 `{}`）
- 含 `from_json` / `to_dict` / `to_json`

```python
@dataclass(frozen=True)
class SoloFinanceTransaction:
    """One structured finance transaction (expense/income/transfer/invest gain-loss)."""
    id: str
    record_id: str = ""
    date: str = ""
    type: str = ""              # expense|income|transfer|invest_gain|invest_loss
    category: str = ""
    amount: float = 0.0
    currency: str = "CNY"
    account: str = ""
    counterparty: str = ""
    description: str = ""
    tags: str = ""
    source: str = "agent"
    metrics_json: str = "{}"
    created_at: str = ""
    updated_at: str = ""

    @property
    def metrics(self) -> dict[str, Any]:
        if not self.metrics_json:
            return {}
        try:
            r = json.loads(self.metrics_json)
            return r if isinstance(r, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @classmethod
    def from_json(cls, line: str) -> "SoloFinanceTransaction":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "record_id": self.record_id, "date": self.date,
            "type": self.type, "category": self.category, "amount": self.amount,
            "currency": self.currency, "account": self.account,
            "counterparty": self.counterparty, "description": self.description,
            "tags": self.tags, "source": self.source,
            "metrics_json": self.metrics_json,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class SoloFinanceBudget:
    """A recurring spending budget."""
    id: str
    period: str = "monthly"
    category: str = ""
    amount: float = 0.0
    currency: str = "CNY"
    name: str = ""
    active: int = 1
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    # metrics property + from_json/to_dict/to_json 同上模式
```

#### 1.2 新增两张表 DDL

**文件**: `solo/core/store.py`

在 `_apply_migrations()` 中新增两张表（完整 DDL 见 design §2.1-2.2，含索引）。关键索引：
- `finance_transactions`: date / type / category / record_id
- `finance_budgets`: period / category / active

#### 1.3 Schema 版本升级（v7 → v8）与迁移

**文件**: `solo/core/store.py`

1. `_SCHEMA_VERSION` 从 `7` 提升到 `8`
2. `_apply_migrations()` 末尾追加**幂等**迁移（沿用 health v7 的 `executescript + CREATE TABLE IF NOT EXISTS` 模式，**不用** `_table_exists()`）：

```python
# v8: finance tables（幂等 CREATE TABLE IF NOT EXISTS）
self._conn.executescript("""
    CREATE TABLE IF NOT EXISTS finance_transactions (...);
    CREATE INDEX IF NOT EXISTS idx_finance_txn_date ...;
    -- ... finance_budgets 全部 DDL
""")
self._conn.commit()
```

> **不要**照搬 `_table_exists()` 写法——项目无此方法，IF NOT EXISTS 已幂等。

### 验证

```bash
rm -f /tmp/test_solo_store.db
uv run pytest tests/test_solo/test_store.py -v -k "finance or migration"
```

---

## Phase 2: Store CRUD 方法

### 目标

实现两张表的增删改查。**所有 `list_*` 的过滤字段全部下推 SQL**（本方案无 `latest_holdings` 窗口函数，纯 CRUD + 等值查询）。

### 步骤

#### 2.1 Transaction Store 方法

**文件**: `solo/core/store.py`

```python
_FINANCE_TXN_COLUMNS = [
    "id", "record_id", "date", "type", "category", "amount", "currency",
    "account", "counterparty", "description", "tags", "source",
    "metrics_json", "created_at", "updated_at",
]

def add_finance_transaction(self, txn: SoloFinanceTransaction) -> None:
    cols = list(self._FINANCE_TXN_COLUMNS)
    vals = [getattr(txn, c) for c in cols]
    placeholders = ", ".join("?" * len(vals))
    self._db.execute(
        f"INSERT INTO finance_transactions ({', '.join(cols)}) VALUES ({placeholders})", vals
    )
    self._db.commit()

def list_finance_transactions(
    self, *,
    type: str | None = None,
    category: str | None = None,
    account: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
) -> list[SoloFinanceTransaction]:
    clauses, params = [], []
    if type:
        clauses.append("type = ?"); params.append(type)
    if category:
        clauses.append("category = ?"); params.append(category)
    if account:
        clauses.append("account = ?"); params.append(account)
    if date_from:
        clauses.append("date >= ?"); params.append(date_from)
    if date_to:
        clauses.append("date <= ?"); params.append(date_to)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "ORDER BY date DESC, created_at DESC"
    sql = f"SELECT * FROM finance_transactions{where} {order}"
    if limit is not None:
        sql += " LIMIT ?"; params.append(limit)
    cur = self._db.execute(sql, params)
    return [self._row_to_finance_txn(r) for r in cur.fetchall()]

def get_finance_transaction(self, txn_id: str) -> SoloFinanceTransaction | None: ...
def update_finance_transaction(self, txn_id: str, **fields) -> bool:
    allowed = set(self._FINANCE_TXN_COLUMNS) - {"id"}
    # 注意：PATCH handler 层会再排除 type/amount；store 层只排除 id
    ...
def delete_finance_transaction(self, txn_id: str) -> bool: ...
def finance_transaction_categories(self) -> dict[str, int]: ...
```

> `_row_to_finance_txn` 按 `_FINANCE_TXN_COLUMNS` 顺序解包（与 health 的 `_row_to_health_record` 同模式）。

#### 2.2 Budget Store 方法

**文件**: `solo/core/store.py`

```python
def add_finance_budget(self, b: SoloFinanceBudget) -> None: ...
def list_finance_budgets(self, *, active: bool | None = None, category: str | None = None) -> list[SoloFinanceBudget]: ...
def get_finance_budget(self, b_id: str) -> SoloFinanceBudget | None: ...
def update_finance_budget(self, b_id: str, **fields) -> bool: ...
def delete_finance_budget(self, b_id: str) -> bool: ...

def find_budget(self, period: str, category: str) -> SoloFinanceBudget | None:
    """按 (period, category) 等值查找已有预算，供 upsert 用。简单查询，无窗口函数。"""
    cur = self._db.execute(
        "SELECT * FROM finance_budgets WHERE period = ? AND category = ? ORDER BY created_at DESC LIMIT 1",
        [period, category],
    )
    rows = cur.fetchall()
    return self._row_to_finance_budget(rows[0]) if rows else None
```

#### 2.3 导入更新

**文件**: `solo/core/store.py` 顶部 `from solo.core.models import (...)` 添加两个新类。

### 验证

```bash
uv run pytest tests/test_solo/test_store.py -v -k "finance"
```

测试必须覆盖：category/type/account/date_from/date_to/limit 组合过滤（全部下推 SQL）、空表返回 `[]`、`find_budget(period, category)` upsert、schema 迁移 v8 幂等。

---

## Phase 3: Agent 工具 -- `solo_finance_transaction`

### 目标

实现交易记录工具，注册到 registry。**参数列表不含 record_id**（见 design §2.6）。

### 步骤

#### 3.1 工具定义

**文件**: `solo/tools.py`

完整定义见 design §3.1。关键参数：
- 必填：`type`, `category`, `amount`
- 可选：`currency`, `date`, `account`, `counterparty`, `description`, `tags`
- **不含** rate/amount_cny（不做折算）、record_id（编排层回填）

#### 3.2 category 校验辅助

```python
_EXPENSE_CATEGORIES = {"dining", "groceries", "transport", "shopping", "housing",
                       "health", "education", "entertainment", "family", "social"}
_INCOME_CATEGORIES = {"salary", "bonus", "refund", "gift", "other_income"}
_INVEST_CATEGORIES = {"stocks", "fund", "bond", "crypto", "gold", "savings", "insurance"}
_VAGUE_NAMES = {"other", "misc", "general", "unknown", "custom", "test"}

def _is_valid_finance_category(txn_type: str, category: str) -> bool:
    """推荐类别直接通过；新类别需满足约束。"""
    preferred = _EXPENSE_CATEGORIES | _INCOME_CATEGORIES | _INVEST_CATEGORIES
    if category in preferred:
        return True
    if category in _VAGUE_NAMES:
        return False
    return category.isalpha() and category.islower() and len(category) <= 20
```

#### 3.3 工具处理器

**文件**: `solo/tools.py`

```python
async def _handle_finance_transaction(self, arguments: dict[str, Any]) -> dict[str, Any]:
    txn_type = _required_text(arguments, "type")
    category = _required_text(arguments, "category")
    amount = float(arguments.get("amount") or 0)
    if amount <= 0:
        return {"ok": False, "error": f"amount must be positive, got {amount}"}
    if txn_type not in {"expense", "income", "transfer", "invest_gain", "invest_loss"}:
        return {"ok": False, "error": f"Invalid type '{txn_type}'."}
    if not _is_valid_finance_category(txn_type, category):
        return {"ok": False, "error": f"Invalid category '{category}' for type '{txn_type}'."}

    local_today = _now()[:10]
    txn = SoloFinanceTransaction(
        id=uuid4().hex[:12],
        record_id=str(arguments.get("record_id") or ""),
        date=str(arguments.get("date") or local_today),
        type=txn_type,
        category=category,
        amount=amount,
        currency=str(arguments.get("currency") or "CNY").upper(),
        account=str(arguments.get("account") or ""),
        counterparty=str(arguments.get("counterparty") or ""),
        description=str(arguments.get("description") or ""),
        tags=str(arguments.get("tags") or ""),
        source="agent",
        metrics_json=str(arguments.get("metrics_json") or "{}"),
        created_at=_now(),
        updated_at=_now(),
    )
    self.store.add_finance_transaction(txn)
    self._pending_finance_ids.append(txn.id)   # 供 post_turn_backfill 回填 record_id
    return {"ok": True, "message": f"财务记录已入库：{txn_type}/{category} {amount} {txn.currency} ({txn.date})"}
```

> **返回契约**：成功 `{"ok": True, "message": ...}`，失败 `{"ok": False, "error": ...}`，不返回 id。

#### 3.4 post_turn_backfill 挂载

**文件**: `solo/tools.py`（`post_turn_backfill` 方法，health backfill 之后）

```python
self._pending_finance_ids: list[str] = []   # __init__ 中初始化

# 在 post_turn_backfill 中（health 之后）：
for fid in self._pending_finance_ids:
    if self._created_record_ids:
        self.store.update_finance_transaction(fid, record_id=list(self._created_record_ids)[-1])
self._pending_finance_ids.clear()
```

#### 3.5 注册

```python
SoloDomainTool(_tool_finance_transaction(), self._handle_finance_transaction),
```

#### 3.6 导入更新

**文件**: `solo/tools.py` 顶部 `from solo.core.models import (...)` 添加 `SoloFinanceTransaction`。

### 验证

```bash
uv run pytest tests/test_solo/test_tools.py -v -k "finance_transaction"
```

---

## Phase 4: Agent 工具 -- `solo_finance_budget`

### 目标

实现预算设定工具（带 upsert）。

### 步骤

#### 4.1 工具定义 + 处理器

**文件**: `solo/tools.py`

完整定义见 design §3.2。handler 检查 `(period, category)` 是否已存在，存在则 update amount，否则新增：

```python
async def _handle_finance_budget(self, arguments: dict[str, Any]) -> dict[str, Any]:
    period = str(arguments.get("period") or "monthly").lower()
    category = str(arguments.get("category") or "").strip()
    amount = float(arguments.get("amount") or 0)
    if amount <= 0:
        return {"ok": False, "error": f"amount must be positive, got {amount}"}
    if period not in {"monthly", "weekly", "yearly"}:
        return {"ok": False, "error": f"Invalid period '{period}'."}

    existing = self.store.find_budget(period, category)
    if existing:
        self.store.update_finance_budget(existing.id, amount=amount,
                                         currency=str(arguments.get("currency") or existing.currency).upper(),
                                         name=str(arguments.get("name") or existing.name),
                                         note=str(arguments.get("note") or existing.note),
                                         updated_at=_now())
        return {"ok": True, "message": f"预算已更新：{period}/{category or '全部'} {amount}"}
    b = SoloFinanceBudget(
        id=uuid4().hex[:12],
        period=period, category=category, amount=amount,
        currency=str(arguments.get("currency") or "CNY").upper(),
        name=str(arguments.get("name") or ""), active=1,
        created_at=_now(), updated_at=_now(),
        note=str(arguments.get("note") or ""),
    )
    self.store.add_finance_budget(b)
    return {"ok": True, "message": f"预算已设置：{period}/{category or '全部'} {amount}"}
```

#### 4.2 注册

```python
SoloDomainTool(_tool_finance_budget(), self._handle_finance_budget),
```

### 验证

```bash
uv run pytest tests/test_solo/test_tools.py -v -k "finance_budget"
```

---

## Phase 5: 辅助查询工具 -- `solo_finance_summary`

### 目标

让 agent 能查询财务历史回答用户问题。

### 步骤

**文件**: `solo/tools.py`

完整定义见 design §3.3。handler 用 `list_finance_transactions` 取数并聚合（沿用 `_handle_health_summary` 模式）：

```python
async def _handle_finance_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timedelta
    from collections import Counter

    txn_type = str(arguments.get("type") or "").strip() or None
    category = str(arguments.get("category") or "").strip() or None
    account = str(arguments.get("account") or "").strip() or None
    days = int(arguments.get("days") or 30)
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    records = self.store.list_finance_transactions(
        type=txn_type, category=category, account=account, date_from=date_from,
    )
    if not records:
        return {"ok": True, "total": 0,
                "message": f"过去 {days} 天没有相关财务记录。"}

    expense = sum(r.amount for r in records if r.type == "expense" and r.currency == "CNY")
    income = sum(r.amount for r in records if r.type == "income" and r.currency == "CNY")
    invest_net = (sum(r.amount for r in records if r.type == "invest_gain" and r.currency == "CNY")
                  - sum(r.amount for r in records if r.type == "invest_loss" and r.currency == "CNY"))
    by_category = Counter(r.category for r in records)

    return {
        "ok": True,
        "total": len(records),
        "days": days,
        "type_filter": txn_type,
        "category_filter": category,
        "account_filter": account,
        "expense_cny": round(expense, 2),
        "income_cny": round(income, 2),
        "invest_net_cny": round(invest_net, 2),
        "by_category": dict(by_category),
        "recent_records": [r.to_dict() for r in records[:10]],
    }
```

> 聚合默认只算 CNY（design §2.6），外币记录仍出现在 `recent_records` 里但不计入合计。

#### 注册

```python
SoloDomainTool(_tool_finance_summary(), self._handle_finance_summary),
```

### 验证

```bash
uv run pytest tests/test_solo/test_tools.py -v -k "finance_summary"
```

---

## Phase 6: 提示词优化

### 目标

更新 solo system prompt，使 agent 能识别财务信息并调用对应工具。

### 步骤

#### 6.1 工具路由决策表新增

**文件**: `solo/prompts.py`（health 行之后）

```
| 用户提到资金流动（消费、收入、转账、理财盈亏结果） | → solo_finance_transaction（同一轮与 solo_record 并行调用） |
| 用户设定消费预算（"餐饮每月2000"、"这个月控制在5000"） | → solo_finance_budget |
```

#### 6.2 新增"财务记录提取原则"章节

**文件**: `solo/prompts.py`

在"健康记录提取原则"之后新增平级章节（完整内容见 design §4.2）。核心要点：
- 两种财务记录的区别（流水 transaction / 限额 budget / 长效 remember）
- 金额提取规则（只提取用户明确说的金额，不估算不拆分）
- **理财只记盈亏结果**（invest_gain/loss），不记买卖动作
- 隐含财务信息识别（逐句扫描）
- 交易类型选择（5 种 type 表）
- 消费类别选择（category 表）
- 不提取的情况（无金额、计划、稳定事实、买卖动作未提盈亏）

#### 6.3 更新 solo_record 的 SIDE-EFFECT CHECK

**文件**: `solo/tools.py`

在 `_tool_record()` 的 `SIDE-EFFECT CHECK` 中追加（保留 remember / health 部分）：

```
If this message contains money flows (spending, income, transfers, or an investment
GAIN/LOSS RESULT with specific amounts), also call solo_finance_transaction in the SAME
turn — once per distinct transaction. Extract ONLY the EXACT amount the user stated; do NOT
estimate or split. For investment, record only the gain/loss result (e.g. '基金赚了300'),
NOT buy/sell actions. If the user sets a spending budget, call solo_finance_budget.
```

### 验证

```bash
uv run python -c "from solo.prompts import build_system_prompt; print(len(build_system_prompt()))"
```

---

## Phase 7: Onboard 后端 API

### 目标

新增 Finance API 路由，查询两张表。**所有读取端点支持过滤（下推 SQL），提供受限写操作。**

### 步骤

#### 7.1 扩展 SoloService

**文件**: `onboard/services/solo_service.py`

添加财务数据查询方法（沿用 health service 模式，Python 层聚合）：

```python
# ── Finance overview ───────────────────────────────────────

def finance_overview(self) -> dict[str, Any]:
    """月度概览：本月支出/收入/结余/理财净盈亏 + 类别合计。"""
    from datetime import datetime

    month_start = datetime.now().strftime("%Y-%m-01")
    month_txns = self.store.list_finance_transactions(date_from=month_start)
    # 默认只算 CNY
    expense = sum(t.amount for t in month_txns if t.type == "expense" and t.currency == "CNY")
    income = sum(t.amount for t in month_txns if t.type == "income" and t.currency == "CNY")
    invest_net = (sum(t.amount for t in month_txns if t.type == "invest_gain" and t.currency == "CNY")
                  - sum(t.amount for t in month_txns if t.type == "invest_loss" and t.currency == "CNY"))

    # 类别合计（支出侧）
    from collections import defaultdict
    by_category: dict[str, float] = defaultdict(float)
    for t in month_txns:
        if t.type == "expense" and t.currency == "CNY":
            by_category[t.category] += t.amount

    return {
        "month_expense": round(expense, 2),
        "month_income": round(income, 2),
        "month_net": round(income - expense, 2),
        "invest_net": round(invest_net, 2),
        "by_category": [{"category": k, "amount": round(v, 2)}
                        for k, v in sorted(by_category.items(), key=lambda x: -x[1])],
    }

def list_finance_transactions(self, *, type=None, category=None, account=None,
                              date_from=None, date_to=None, limit=20, offset=0) -> dict[str, Any]:
    records = self.store.list_finance_transactions(
        type=type, category=category, account=account,
        date_from=date_from, date_to=date_to)
    total = len(records)
    page = records[offset:offset + limit]
    return {"items": [r.to_dict() for r in page], "total": total, "limit": limit, "offset": offset}

def finance_transactions_summary(self, type: str | None = None, days: int = 30) -> dict[str, Any]:
    """按 category 聚合（用于类别排行）。"""
    from datetime import datetime, timedelta
    from collections import defaultdict
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_finance_transactions(type=type, date_from=cutoff)
    by_cat: dict[str, float] = defaultdict(float)
    for r in records:
        if r.currency == "CNY":   # 默认只聚合 CNY
            by_cat[r.category] += r.amount
    return {
        "by_category": [{"category": k, "amount": round(v, 2)}
                        for k, v in sorted(by_cat.items(), key=lambda x: -x[1])],
        "total": round(sum(by_cat.values()), 2),
        "count": len(records),
    }

def finance_transactions_trend(self, days: int = 180) -> dict[str, Any]:
    """月度收入/支出/结余序列（按 strftime('%Y-%m', date) 分组）。"""
    from datetime import datetime, timedelta
    from collections import defaultdict
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_finance_transactions(date_from=cutoff)
    by_month: dict[str, dict] = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    for r in records:
        if r.currency != "CNY":
            continue
        m = r.date[:7]   # YYYY-MM
        if r.type == "income":
            by_month[m]["income"] += r.amount
        elif r.type == "expense":
            by_month[m]["expense"] += r.amount
    trend = [{"month": m, "income": round(v["income"], 2), "expense": round(v["expense"], 2),
              "net": round(v["income"] - v["expense"], 2)}
             for m, v in sorted(by_month.items())]
    return {"trend": trend}

def finance_budgets(self, active: bool = True) -> dict[str, Any]:
    """预算列表 + 当前周期消耗率。"""
    from datetime import datetime
    budgets = self.store.list_finance_budgets(active=active if active else None)
    now = datetime.now()
    period_start = now.strftime("%Y-%m-01")   # v1.0 简化：仅 monthly 精确，weekly/yearly 后续细化
    items = []
    for b in budgets:
        spent = sum(t.amount for t in self.store.list_finance_transactions(
            type="expense", category=(b.category or None), date_from=period_start)
            if t.currency == "CNY")
        items.append({
            "id": b.id, "period": b.period, "category": b.category,
            "amount": b.amount, "currency": b.currency, "name": b.name,
            "active": b.active, "note": b.note,
            "spent": round(spent, 2),
            "utilization": round(spent / b.amount, 3) if b.amount else 0,
        })
    return {"items": items, "period_start": period_start}

def finance_transactions_daily(self, month: str | None = None) -> dict[str, Any]:
    """某月每日支出合计（用于消费日历热力图 Zone 5）。"""
    from datetime import datetime, timedelta
    from collections import defaultdict
    if month:
        y, m = month.split('-')
        date_from = f"{y}-{m}-01"
        d = datetime(int(y), int(m) + 1, 1) - timedelta(days=1)
        date_to = d.strftime("%Y-%m-%d")
    else:
        now = datetime.now()
        date_from = now.strftime("%Y-%m-01")
        date_to = now.strftime("%Y-%m-%d")
    records = self.store.list_finance_transactions(type="expense", date_from=date_from, date_to=date_to)
    daily: dict[str, float] = defaultdict(float)
    for r in records:
        if r.currency == "CNY":
            daily[r.date] += r.amount
    items = [{"date": d, "amount": round(v, 2)} for d, v in sorted(daily.items())]
    return {"items": items, "month": month or datetime.now().strftime("%Y-%m")}

def finance_invest_trend(self, days: int = 180) -> dict[str, Any]:
    """月度理财盈亏累计净值序列（0 基线 → Zone 4 右 AreaChart）。"""
    from datetime import datetime, timedelta
    from collections import defaultdict
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_finance_transactions(date_from=cutoff)
    by_month: dict[str, dict] = defaultdict(lambda: {"gain": 0.0, "loss": 0.0})
    for r in records:
        if r.currency != "CNY":
            continue
        m = r.date[:7]
        if r.type == "invest_gain":
            by_month[m]["gain"] += r.amount
        elif r.type == "invest_loss":
            by_month[m]["loss"] += r.amount
    trend = [{"month": m, "net": round(v["gain"] - v["loss"], 2)}
             for m, v in sorted(by_month.items())]
    return {"trend": trend}

# ── 写操作（受限，见 design §6.2）──────────────────────────

def delete_finance_transaction(self, txn_id: str) -> bool:
    return self.store.delete_finance_transaction(txn_id)

def update_finance_transaction(self, txn_id: str, updates: dict[str, Any]) -> bool:
    forbidden = {"type", "amount", "id"}   # 身份字段不可改
    safe = {k: v for k, v in updates.items() if k not in forbidden}
    return self.store.update_finance_transaction(txn_id, **safe)

def delete_finance_budget(self, b_id: str) -> bool:
    return self.store.delete_finance_budget(b_id)

def update_finance_budget(self, b_id: str, updates: dict[str, Any]) -> bool:
    forbidden = {"category", "period", "id"}
    safe = {k: v for k, v in updates.items() if k not in forbidden}
    return self.store.update_finance_budget(b_id, **safe)
```

#### 7.2 创建 Finance API 路由

**文件**: `onboard/api/finance.py`

挂在 `/api/solo/finance` 前缀（solo 命名空间隔离）：

```python
"""Finance API routes (solo-only)."""

from fastapi import APIRouter, Query

from onboard.services.solo_service import SoloService

router = APIRouter(prefix="/api/solo/finance", tags=["finance"])


def _service(workspace: str | None = None) -> SoloService:
    return SoloService(workspace)


@router.get("/overview")
def finance_overview(workspace: str | None = None):
    return _service(workspace).finance_overview()

@router.get("/transactions")
def finance_transactions(
    workspace: str | None = None,
    type: str | None = None,
    category: str | None = None,
    account: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return _service(workspace).list_finance_transactions(
        type=type, category=category, account=account,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset)

@router.get("/transactions/summary")
def finance_transactions_summary(
    workspace: str | None = None,
    type: str | None = None,
    days: int = Query(30, ge=1, le=365),
):
    return _service(workspace).finance_transactions_summary(type=type, days=days)

@router.get("/transactions/trend")
def finance_transactions_trend(
    workspace: str | None = None,
    days: int = Query(180, ge=1, le=3650),
):
    return _service(workspace).finance_transactions_trend(days=days)

@router.get("/transactions/daily")
def finance_transactions_daily(
    workspace: str | None = None,
    month: str | None = None,
):
    return _service(workspace).finance_transactions_daily(month=month)

@router.get("/invest/trend")
def finance_invest_trend(
    workspace: str | None = None,
    days: int = Query(180, ge=1, le=3650),
):
    return _service(workspace).finance_invest_trend(days=days)

@router.get("/budgets")
def finance_budgets(workspace: str | None = None, active: bool = True):
    return _service(workspace).finance_budgets(active=active)

# ── 写操作（受限）─────────────────────────────────────────

@router.delete("/transactions/{txn_id}")
def delete_finance_transaction(txn_id: str, workspace: str | None = None):
    return {"ok": _service(workspace).delete_finance_transaction(txn_id)}

@router.patch("/transactions/{txn_id}")
def update_finance_transaction(txn_id: str, updates: dict, workspace: str | None = None):
    return {"ok": _service(workspace).update_finance_transaction(txn_id, updates)}

@router.delete("/budgets/{b_id}")
def delete_finance_budget(b_id: str, workspace: str | None = None):
    return {"ok": _service(workspace).delete_finance_budget(b_id)}

@router.patch("/budgets/{b_id}")
def update_finance_budget(b_id: str, updates: dict, workspace: str | None = None):
    return {"ok": _service(workspace).update_finance_budget(b_id, updates)}
```

> **删除的端点**（相比旧 plan）：`net-worth-trend`、`holdings`、`holdings/allocation`。

#### 7.3 注册路由

**文件**: `onboard/server.py`

```python
from onboard.api import chat, finance, health, lifecycle, solo_routes, stats, wolo_routes  # 新增 finance

app.include_router(finance.router)
```

> Token Gate 全局保护；solo-only 靠命名空间隔离（design §5.6）。

### 验证

```bash
uv run onboard run --reload &
curl -s http://localhost:8090/api/solo/finance/overview | python -m json.tool
curl -s "http://localhost:8090/api/solo/finance/transactions?type=expense&category=dining" | python -m json.tool
curl -s "http://localhost:8090/api/solo/finance/transactions/trend?days=180" | python -m json.tool
curl -s "http://localhost:8090/api/solo/finance/transactions/daily?month=2025-06" | python -m json.tool
curl -s "http://localhost:8090/api/solo/finance/invest/trend?days=180" | python -m json.tool
curl -s http://localhost:8090/api/solo/finance/budgets | python -m json.tool
```

---

## Phase 8: Onboard 前端 -- 类型定义与 API 客户端

### 步骤

#### 8.1 扩展类型定义

**文件**: `onboard/frontend/src/api/types.ts`

```typescript
// ── Finance ────────────────────────────────────────────────

export type FinanceTxnType = 'expense' | 'income' | 'transfer' | 'invest_gain' | 'invest_loss';

export interface SoloFinanceTransaction {
  id: string;
  record_id: string;
  date: string;
  type: FinanceTxnType;
  category: string;
  amount: number;
  currency: string;
  account: string;
  counterparty: string;
  description: string;
  tags: string;
  source: string;
  metrics_json: string;
  created_at: string;
  updated_at: string;
}

export interface SoloFinanceBudget {
  id: string;
  period: string;       // monthly|weekly|yearly
  category: string;     // '' = 总预算
  amount: number;
  currency: string;
  name: string;
  active: number;
  note: string;
  // 计算字段（来自 service）
  spent?: number;
  utilization?: number;
}

export interface FinanceOverview {
  month_expense: number;
  month_income: number;
  month_net: number;
  invest_net: number;
  prev_expense: number;
  prev_income: number;
  prev_net: number;
  prev_invest_net: number;
  by_category: { category: string; amount: number }[];
}

export interface FinanceDailyItem {
  date: string;
  amount: number;
}

export interface FinanceInvestTrend {
  trend: { month: string; net: number }[];
}
```

#### 8.2 扩展 API 客户端

**文件**: `onboard/frontend/src/api/client.ts`

```typescript
// ── Finance (solo-only) ──────────────────────────────────────
finance: {
  overview: (workspace?: string) =>
    request<FinanceOverview>(`/api/solo/finance/overview${query({ ...(workspace ? { workspace } : {}) })}`),
  transactions: (params: Record<string, QueryValue> = {}) =>
    request<PaginatedResponse<SoloFinanceTransaction>>(`/api/solo/finance/transactions${query(params)}`),
  transactionsSummary: (type?: FinanceTxnType, days: number = 30) =>
    request<{ by_category: { category: string; amount: number }[]; total: number; count: number }>(
      `/api/solo/finance/transactions/summary${query({ ...(type ? { type } : {}), days })}`),
  transactionsTrend: (days: number = 180) =>
    request<{ trend: { month: string; income: number; expense: number; net: number }[] }>(
      `/api/solo/finance/transactions/trend${query({ days })}`),
  transactionsDaily: (month?: string) =>
    request<{ items: FinanceDailyItem[]; month: string }>(
      `/api/solo/finance/transactions/daily${query({ ...(month ? { month } : {}) })}`),
  investTrend: (days: number = 180) =>
    request<FinanceInvestTrend>(`/api/solo/finance/invest/trend${query({ days })}`),
  budgets: (active: boolean = true) =>
    request<{ items: SoloFinanceBudget[]; period_start: string }>(`/api/solo/finance/budgets${query({ active })}`),
  deleteTransaction: (id: string) =>
    request<{ ok: boolean }>(`/api/solo/finance/transactions/${id}`, { method: 'DELETE' }),
  updateTransaction: (id: string, updates: Partial<SoloFinanceTransaction>) =>
    request<{ ok: boolean }>(`/api/solo/finance/transactions/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }),
  deleteBudget: (id: string) =>
    request<{ ok: boolean }>(`/api/solo/finance/budgets/${id}`, { method: 'DELETE' }),
  updateBudget: (id: string, updates: Partial<SoloFinanceBudget>) =>
    request<{ ok: boolean }>(`/api/solo/finance/budgets/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }),
},
```

### 验证

```bash
cd onboard/frontend && npx tsc --noEmit
```

---

## Phase 9: Onboard 前端 -- 视觉仪表盘（6 Zone / 8 组件）

> 核心原则：**图表为主、文字为辅、一眼读懂。** 详见 design §5.3–5.6。

### 9.1 修改 Sidebar

**文件**: `onboard/frontend/src/components/Sidebar.tsx`

Finance 仅 solo 可见，插入到 Health 之后（与 health 同模式）：

```typescript
const SOLO_FINANCE_ITEM = ['/finance', '💰', 'Finance'] as const;
// solo 分支: [...commonItems(前4), SOLO_HEALTH_ITEM, SOLO_FINANCE_ITEM, ...commonItems(4及之后)]
```

### 9.2 注册路由

**文件**: `onboard/frontend/src/App.tsx`

```typescript
const Finance = lazy(() => import('./pages/Finance').then((m) => ({ default: m.Finance })));
{ path: 'finance', element: <SuspenseLoader><Finance /></SuspenseLoader> },
```

### 9.3 主页面组件

**文件**: `onboard/frontend/src/pages/Finance.tsx`

**顶层结构**（沿用 Health/Dashboard 的 `space-y-6` 容器）：

```tsx
return (
  <div className="relative space-y-6" style={{ zIndex: 1 }}>
    <SciFiBackground accent="#d4a574" />   {/* 极光氛围，与 Dashboard 同款 */}

    {/* Header + 时间范围选择器 */}
    <div className="flex items-start justify-between">
      <div>
        <h1 className="text-2xl font-serif text-text">Finance</h1>
        <p className="text-sm text-text-muted mt-1">个人消费追踪与预算</p>
      </div>
      <TimeRangeSelector selected={selectedDays} onSelect={setSelectedDays} />
    </div>

    {/* Zone 1: 月度总览 4×StatsCard */}
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <SpendingCards overview={overview} />
    </div>

    {/* Zone 2: 月度收支趋势 ComposedChart（全宽） */}
    <Section title="月度收支趋势">
      <CashflowTrend days={selectedDays} />
    </Section>

    {/* Zone 3: 双列 — Donut + RadialBar */}
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Section title="消费类别构成"><CategoryDonut days={selectedDays} /></Section>
      <Section title="预算追踪"><BudgetRings /></Section>
    </div>

    {/* Zone 4: 双列 — 横向条形 + 理财盈亏 AreaChart */}
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Section title="消费类别排行"><CategoryRanking days={selectedDays} /></Section>
      <Section title="理财盈亏趋势"><InvestTrend days={selectedDays} /></Section>
    </div>

    {/* Zone 5: 消费日历热力图 */}
    <Section title="消费日历">
      <SpendingHeatmap month={chartMonth} onMonthChange={setChartMonth} />
    </Section>

    {/* Zone 6: 流水时间线（可折叠 + type 筛选 chip） */}
    <TransactionTimeline />
  </div>
);
```

**图表配置约定**（所有组件统一遵守）：
- `ResponsiveContainer width="100%" height={200}`（日历除外）
- `CartesianGrid strokeDasharray="3 3" stroke="#2e2e33"`
- `tooltipStyle`：bg `#1c1c21`、border `#2e2e33`、mono 字体
- `palette`：`['#b8956a','#6a9e8e','#8b7db8','#c4a35a','#b87070','#6a8a9e','#7eb87e','#c48a6a']`
- 盈利 `var(--color-success)` `#34d399`，亏损 `var(--color-danger)` `#f87171`
- 预算环形：`< 0.6` 绿、`0.6–0.8` 黄、`0.8–1.0` 橙、`≥ 1.0` 红
- 空数据：每个图表各自 `<EmptyState title="暂无数据" />`

### 9.4 子组件（8 个）

| 组件 | 文件 | 图表类型 | 数据源 API | 复用/新增 |
|------|------|---------|-----------|----------|
| `SpendingCards` | `components/finance/SpendingCards.tsx` | 4× `StatsCard`（count-up 动画 + 环比 ↑↓） | `overview()` | **复用** StatsCard |
| `CashflowTrend` | `components/finance/CashflowTrend.tsx` | `ComposedChart`（Bar + Line） | `transactionsTrend(days)` | **新增**图表类型 |
| `CategoryDonut` | `components/finance/CategoryDonut.tsx` | `PieChart` donut（innerRadius 48%） | `transactionsSummary('expense', days)` | **复用** EmotionPieChart 模式 |
| `BudgetRings` | `components/finance/BudgetRings.tsx` | `RadialBarChart` 环形进度 | `budgets()` | **新增**图表类型 |
| `CategoryRanking` | `components/finance/CategoryRanking.tsx` | `BarChart layout="vertical"` | `transactionsSummary('expense', days)` | **复用** TagBarChart 模式 |
| `InvestTrend` | `components/finance/InvestTrend.tsx` | `AreaChart`（0 基线，盈绿亏红） | `investTrend(days)` | **复用** Health Sleep AreaChart 模式 |
| `SpendingHeatmap` | `components/finance/SpendingHeatmap.tsx` | 月历热力图（日消费强度） | `transactionsDaily(month)` | **复用** ActivityHeatmap |
| `TransactionTimeline` | `components/finance/TransactionTimeline.tsx` | 列表 + type 筛选 chip | `transactions({limit})` | **复用** SubjectFilter chip 模式 |

> **6 个直接复用现成组件模式 + 2 个用 recharts 内置新图表类型。**

### 验证

```bash
cd onboard/frontend && npx tsc --noEmit
# 访问 /finance 确认所有 6 个 Zone 渲染
# 切换时间范围选择器确认 Zone 2/3/4 趋势图刷新
# 切换月份确认 Zone 5 消费日历刷新
# 确认预算环形颜色（绿/黄/橙/红）正确
# 确认理财盈亏 0 基线 + 盈绿亏红
# 确认每个图表空数据独立降级
# 确认极光背景渲染
# 确认 Solo/Wolo 切换（wolo 下不显示 Finance）
```

---

## Phase 10: 测试与收尾

### 10.1 测试用例清单（必须补全）

新增 `tests/test_solo/test_finance.py`（Store 层）并在 `test_tools.py` 中补 finance 用例：

#### Store 层
- `add_finance_transaction` + `get_finance_transaction` 往返一致
- `list_finance_transactions` type/category/account/date_from/date_to/limit 组合过滤（**全部下推 SQL**）
- `list_finance_transactions` 空表返回 `[]`
- `find_budget(period, category)` upsert：已存在则 update，不存在则 add
- `finance_transaction_categories` 计数正确
- schema 迁移 v8 幂等：对已有 finance 表的库重复跑迁移不报错

#### Tool handler 层
- `_handle_finance_transaction` 合法标准类别成功，返回 `{"ok": True, "message": ...}`（**不含 id**）
- `_handle_finance_transaction` 合法新类别（如 `telecom`）成功
- `_handle_finance_transaction` 拒绝 vague 名（`other`/`misc`）→ `{"ok": False, "error": ...}`
- `_handle_finance_transaction` 拒绝 amount ≤ 0
- `_handle_finance_transaction` 拒绝非法 type（如 `investment_buy` 旧值）
- `_handle_finance_transaction` invest_loss 存正数（"亏了500" → amount=500）
- `_handle_finance_budget` 首次设置 vs 重复设置（同 period+category → update）
- `_handle_finance_budget` 拒绝 amount ≤ 0、非法 period
- `_handle_finance_summary` type/category/account 过滤
- `_handle_finance_summary` expense/income/invest_net 聚合只算 CNY
- `_handle_finance_summary` 空结果返回 total: 0

#### 集成层（通过 agent）
- 消息"午饭花了35，打车15"触发**两次** `solo_finance_transaction`（expense/dining + expense/transport）
- 消息"和朋友AA火锅花了120"正确提取 amount=120（人均，不是240）
- 消息"工资到账1万8"触发 income/salary amount=18000
- 消息"基金赚了300"触发 invest_gain/fund amount=300
- 消息"股票亏了500"触发 invest_loss/stocks amount=500
- 消息"餐饮每月预算2000"触发 `solo_finance_budget`
- 消息"我月工资到手1万8"走 `solo_remember`（稳定事实，不进 finance 表）
- 消息"买了100股茅台"（只说买卖未提盈亏）**不触发**任何 finance 工具
- 消息"$200"识别 currency=USD（不折算，原值入库）
- 消息"想买辆车"不触发任何 finance 工具（计划，未发生）

#### API 层
- `GET /api/solo/finance/overview` 返回本月收支/结余/理财净盈亏
- `GET /api/solo/finance/transactions?type=expense&category=dining` 过滤正确
- `GET /api/solo/finance/transactions/summary?type=expense` 类别排行
- `GET /api/solo/finance/transactions/trend?days=180` 月度趋势
- `GET /api/solo/finance/budgets` 含消耗率
- `GET /api/solo/finance/transactions/daily?month=2025-06` 每日支出序列
- `GET /api/solo/finance/invest/trend?days=180` 理财盈亏趋势
- `DELETE /api/solo/finance/transactions/{id}` 删除成功
- `PATCH /api/solo/finance/transactions/{id}` 不能改 type/amount（被忽略）
- `PATCH /api/solo/finance/budgets/{id}` 不能改 category/period（被忽略）
- wolo 模式下 `/api/solo/finance/*` 不被前端调用

### 10.2 端到端验证

1. solo CLI 发送含财务信息的消息，验证 agent 自动调用对应工具
2. Onboard Finance 页面验证数据展示
3. 时间范围选择器切换后趋势图刷新
4. 预算消耗率正确计算
5. 理财盈亏单列展示（不计入结余）
6. Solo/Wolo 切换
7. 空数据场景

### 10.3 代码质量

```bash
uv run ruff check solo/ onboard/
cd onboard/frontend && npx tsc --noEmit
uv run pytest -q tests/test_solo/
```

### 10.4 CHANGELOG 更新

```markdown
### Added
- **Finance module (solo-only)**: Two structured tables (`finance_transactions`, `finance_budgets`) for lightweight daily spending, income, and investment gain/loss tracking plus monthly budgets. Not available in wolo.
- **5 transaction types**: expense / income / transfer / invest_gain / invest_loss. Investment records only the gain/loss RESULT (not buy/sell actions) — matching a "track cashflow, not positions" use case.
- **`solo_finance_transaction` / `solo_finance_budget` / `solo_finance_summary` tools**: Agent auto-extracts money flows, budgets, and queries history from user messages (same-turn pattern). Amounts extracted EXACTLY as stated — no estimation/splitting.
- **No currency conversion**: `currency` is a label only; aggregation groups by currency (defaults to CNY). Conversion deferred to v1.2.
- **Onboard Finance page (solo-only)**: Visual-first dashboard with 6 chart zones: overview stats (count-up animation), ComposedChart cashflow trend, donut category breakdown, RadialBar budget rings, horizontal bar category ranking, AreaChart invest gain/loss, spending calendar heatmap, transaction timeline. SciFiBackground aurora ambient. Time range selector (3M/6M/12M/all). 8 components (4 reusing existing patterns + 2 new chart types).
- **Finance record editing (restricted)**: DELETE / PATCH endpoints; PATCH cannot change identity fields (type/amount for transactions, category/period for budgets).
- **Deferred to v1.1+**: holdings/net-worth tracking, asset allocation, bill import, multi-currency conversion, investment buy/sell actions.
```

### 验证清单

- [ ] 两张表正确创建（schema v8 幂等迁移，含全部索引）
- [ ] Store CRUD 工作，`list_*` 过滤全部下推 SQL
- [ ] `find_budget()` upsert 正确
- [ ] 三个 finance 工具可被 agent 调用
- [ ] 金额精确提取（不估算不拆分，AA 按人均）
- [ ] 理财只记盈亏结果（invest_gain/loss），不记买卖动作
- [ ] 提示词优化后 agent 能识别财务信息
- [ ] Finance API 所有端点返回正确，支持过滤
- [ ] `overview` 返回本月收支 + 理财净盈亏
- [ ] `transactions/summary` 类别排行
- [ ] `transactions/trend` 月度趋势
- [ ] `transactions/daily?month=YYYY-MM` 每日支出（热力图数据）
- [ ] `invest/trend?days=180` 理财盈亏趋势
- [ ] `budgets` 含消耗率
- [ ] DELETE / PATCH 写操作可用，PATCH 禁改身份字段
- [ ] 前端 Finance 页面正确渲染（6 Zone / 8 组件）
- [ ] ComposedChart 月度收支趋势正确（柱+折线）
- [ ] RadialBar 预算环形颜色正确（绿/黄/橙/红）
- [ ] 理财盈亏 AreaChart 0 基线 + 盈绿亏红
- [ ] 消费日历热力图按月切换
- [ ] 极光背景 SciFiBackground 渲染
- [ ] 时间范围选择器切换后趋势图刷新
- [ ] 多币种原始金额带币种符号（不折算）
- [ ] 空数据优雅降级
- [ ] **Solo-only 验证**：wolo 模式侧边栏无 Finance 入口，`/finance` 不可访问
- [ ] `ruff check` 通过
- [ ] `tsc --noEmit` 通过
- [ ] finance 测试用例全部新增并通过（Store/Tool/集成/API 四层）
- [ ] 现有测试全部通过

---

## 文件变更总览

### 新增文件

| 文件 | 说明 |
|------|------|
| `onboard/api/finance.py` | Finance REST API（solo 前缀，只读 + 受限写） |
| `onboard/frontend/src/pages/Finance.tsx` | Finance 页面主组件 |
| `onboard/frontend/src/components/finance/SpendingCards.tsx` | 月度总览卡片(4×StatsCard + 环比箭头) |
| `onboard/frontend/src/components/finance/CashflowTrend.tsx` | 月度收支趋势(ComposedChart 柱+折线) |
| `onboard/frontend/src/components/finance/CategoryDonut.tsx` | 消费类别构成(Donut) |
| `onboard/frontend/src/components/finance/BudgetRings.tsx` | 预算追踪(RadialBarChart 环形进度) |
| `onboard/frontend/src/components/finance/CategoryRanking.tsx` | 消费类别排行(横向条形) |
| `onboard/frontend/src/components/finance/InvestTrend.tsx` | 理财盈亏趋势(AreaChart 0基线) |
| `onboard/frontend/src/components/finance/SpendingHeatmap.tsx` | 消费日历热力图 |
| `onboard/frontend/src/components/finance/TransactionTimeline.tsx` | 流水时间线 + type 筛选 chip |
| `tests/test_solo/test_finance.py` | Store/Tool/集成层 finance 测试 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `solo/core/models.py` | 新增两个 finance dataclass（含 metrics property） |
| `solo/core/store.py` | 两张表 DDL + schema v8 幂等迁移 + CRUD（含 `find_budget`） |
| `solo/tools.py` | 三个 finance 工具定义+处理器 + category 校验；更新 `_tool_record` 的 SIDE-EFFECT CHECK；`post_turn_backfill` 增加 finance 回填 |
| `solo/prompts.py` | 新增"财务记录提取原则"章节，更新路由决策表 |
| `onboard/server.py` | 注册 `finance.router` |
| `onboard/services/solo_service.py` | finance 查询方法（含 overview/trend/summary 聚合）+ 受限写操作 |
| `onboard/frontend/src/components/Sidebar.tsx` | 添加 Finance 侧边栏项（仅 solo） |
| `onboard/frontend/src/App.tsx` | 添加 Finance 路由（solo-only） |
| `onboard/frontend/src/api/types.ts` | Finance 类型定义 |
| `onboard/frontend/src/api/client.ts` | finance API 方法（含写操作） |

---

## 实施时间估算

| Phase | 预计时间 | 依赖 |
|-------|----------|------|
| Phase 1: 数据模型与两张表 | 1h | 无 |
| Phase 2: Store CRUD（含 find_budget upsert） | 1h | Phase 1 |
| Phase 3: solo_finance_transaction 工具 + backfill | 1h | Phase 2 |
| Phase 4: solo_finance_budget 工具 | 0.5h | Phase 2 |
| Phase 5: solo_finance_summary 工具 | 0.5h | Phase 2 |
| Phase 6: 提示词优化 | 0.75h | Phase 3-5 |
| Phase 7: Onboard 后端 API（含受限写） | 1.5h | Phase 2 |
| Phase 8: 前端类型 + API 客户端 | 0.25h | Phase 7 |
| Phase 9: 视觉仪表盘（6 Zone / 8 组件） | 3.5h | Phase 8 |
| Phase 10: 测试 + 收尾 | 1.5h | Phase 1-9 |
| **总计** | **~11.5h** | |

> 相比旧版（~18h，三表四工具九组件）减少约 6.5h，主要来自：去掉 holdings 表与 latest_holdings SQL、去掉多币种折算、type 从 7 种压到 5 种。视觉仪表盘（8 组件 + 日历热力图）是额外投入，但大幅提升用户体验——人与图表的交互远优于人与文字的交互。

---

## 关键风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 金额被 LLM 估算/拆分（AA 算成总价） | 数据失真 | prompt 强调"只提取明确金额不拆分"，集成测试覆盖 AA 场景 |
| 理财买卖被误记成盈亏 | 类型混乱 | prompt 强调"只记盈亏结果不记买卖动作"，type 校验拒绝 `investment_buy` 等旧值 |
| 外币记录混入 CNY 聚合 | 统计偏高 | 聚合层显式过滤 `currency=='CNY'`，外币单列展示带币种符号 |
| budget upsert 误判（同 category 不同 period 重复） | 预算重复 | `find_budget(period, category)` 联合键，测试覆盖 |
| 稳定事实被误记成交易（"月薪1万8"进 transactions） | 数据冗余 | prompt 强调稳定事实走 solo_remember，集成测试覆盖 |
| 买卖动作未提盈亏被强行入库 | 持仓语义混乱 | prompt 明确"只说买卖未提盈亏不调 finance 工具"，集成测试覆盖 |
| wolo 模式泄露财务数据 | 隐私问题 | 命名空间隔离 + 前端条件渲染 |
