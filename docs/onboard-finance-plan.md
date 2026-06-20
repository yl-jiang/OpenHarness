# Onboard Finance Page -- 实施计划

> 基于 [onboard-finance-design.md](./onboard-finance-design.md) 的分步实施计划。
> 核心架构：**结构化财务数据库（交易/持仓/预算三表）+ 专用 Agent 工具 + 提示词优化 + Onboard 可视化页面**。
> **注意：Finance 模块仅适用于 solo，不适用于 wolo。**
>
> 本计划假设当前代码库已实现 health 模块（schema v7），财务模块从 schema v8 开始。实施前若 v7 仍未落地，则财务与 health 共用推进即可。

---

## Phase 1: 数据模型与数据库表

### 目标

在 `SoloStore` 中新增 `finance_transactions` / `finance_holdings` / `finance_budgets` 三张结构化表及对应 dataclass。

### 步骤

#### 1.1 新增三个 dataclass 模型

**文件**: `solo/core/models.py`

在 `SoloHealthRecord` 之后添加 `SoloFinanceTransaction`、`SoloFinanceHolding`、`SoloFinanceBudget`（完整定义见 design §2.4）。三者均：
- `@dataclass(frozen=True)`
- 含 `metrics` property（统一解析 metrics_json，失败兜底 `{}`）
- 含 `from_json` / `to_dict` / `to_json`

```python
@dataclass(frozen=True)
class SoloFinanceTransaction:
    """One structured finance transaction (income/expense/investment)."""
    id: str
    record_id: str = ""
    date: str = ""
    type: str = ""              # income|expense|transfer|investment_buy|investment_sell|investment_dividend|investment_income
    category: str = ""
    amount: float = 0.0
    currency: str = "CNY"
    amount_cny: float = 0.0
    rate: float = 1.0
    account: str = ""
    merchant: str = ""
    counterparty: str = ""
    description: str = ""
    investment_symbol: str = ""
    investment_name: str = ""
    investment_quantity: float = 0.0
    investment_price: float = 0.0
    investment_fee: float = 0.0
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
        return {  # 所有字段
            "id": self.id, "record_id": self.record_id, "date": self.date,
            "type": self.type, "category": self.category,
            "amount": self.amount, "currency": self.currency,
            "amount_cny": self.amount_cny, "rate": self.rate,
            "account": self.account, "merchant": self.merchant,
            "counterparty": self.counterparty, "description": self.description,
            "investment_symbol": self.investment_symbol,
            "investment_name": self.investment_name,
            "investment_quantity": self.investment_quantity,
            "investment_price": self.investment_price,
            "investment_fee": self.investment_fee,
            "tags": self.tags, "source": self.source,
            "metrics_json": self.metrics_json,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class SoloFinanceHolding:
    """A snapshot of an asset/debt/investment position."""
    id: str
    as_of_date: str = ""
    type: str = ""              # investment|cash|debt|asset
    name: str = ""
    institution: str = ""
    category: str = ""
    investment_symbol: str = ""
    quantity: float = 0.0
    cost_basis: float = 0.0
    current_price: float = 0.0
    value: float = 0.0
    currency: str = "CNY"
    value_cny: float = 0.0
    rate: float = 1.0
    description: str = ""
    tags: str = ""
    source: str = "agent"
    linked_account: str = ""
    created_at: str = ""
    updated_at: str = ""
    # metrics property + from_json/to_dict/to_json 同 SoloFinanceTransaction 模式


@dataclass(frozen=True)
class SoloFinanceBudget:
    """A recurring spending budget."""
    id: str
    period: str = "monthly"
    category: str = ""
    amount_cny: float = 0.0
    name: str = ""
    active: int = 1
    start_date: str = ""
    end_date: str = ""
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
```

#### 1.2 新增三张表 DDL

**文件**: `solo/core/store.py`

在 `_SCHEMA_SQL` 中新增三张表（完整 DDL 见 design §2.1-2.3，含索引）。三张表的关键索引：
- `finance_transactions`: date / type / category / account / record_id / investment_symbol
- `finance_holdings`: as_of_date / type / name / category / linked_account
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
    -- ... finance_holdings / finance_budgets 全部 DDL
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

实现三张表的增删改查。**重点：`list_*` 的过滤字段全部下推 SQL；`latest_holdings()` 在 SQL 层分组取最新。**

### 步骤

#### 2.1 汇率兜底辅助函数

**文件**: `solo/tools.py`（或 `solo/core/store.py`，取决于复用范围）

```python
# 近似汇率（硬编码 + 注释，v1.2 接实时 API）
_FX_RATES_TO_CNY = {
    "CNY": 1.0, "USD": 7.2, "HKD": 0.92, "EUR": 7.8,
    "JPY": 0.046, "GBP": 9.0, "SGD": 5.3, "AUD": 4.7,
}

def _resolve_currency(currency: str, amount: float, rate: float | None = None) -> tuple[float, float]:
    """返回 (rate, amount_cny)。CNY 固定 1.0；非 CNY 优先用传入 rate，否则用近似表。"""
    currency = (currency or "CNY").upper()
    if currency == "CNY":
        return 1.0, amount
    if rate and rate > 0:
        return rate, amount * rate
    approx = _FX_RATES_TO_CNY.get(currency, 1.0)
    return approx, amount * approx
```

#### 2.2 Transaction Store 方法

**文件**: `solo/core/store.py`

```python
_FINANCE_TXN_COLUMNS = [
    "id", "record_id", "date", "type", "category", "amount", "currency",
    "amount_cny", "rate", "account", "merchant", "counterparty", "description",
    "investment_symbol", "investment_name", "investment_quantity",
    "investment_price", "investment_fee", "tags", "source", "metrics_json",
    "created_at", "updated_at",
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
def finance_transaction_accounts(self) -> dict[str, int]: ...
```

> `_row_to_finance_txn` 按 `_FINANCE_TXN_COLUMNS` 顺序解包（与 health 的 `_row_to_health_record` 同模式）。

#### 2.3 Holding Store 方法

**文件**: `solo/core/store.py`

```python
def add_finance_holding(self, h: SoloFinanceHolding) -> None: ...
def list_finance_holdings(
    self, *,
    type: str | None = None,
    category: str | None = None,
    name: str | None = None,
    as_of_from: str | None = None,
    as_of_to: str | None = None,
) -> list[SoloFinanceHolding]: ...

def latest_holdings(self) -> list[SoloFinanceHolding]:
    """按 (type, name, category) 分组取 as_of_date 最大的一条。
    必须在 SQL 层实现，避免 service 层拉全量。"""
    # SQLite 写法：用窗口函数或子查询
    cur = self._db.execute("""
        SELECT h.* FROM finance_holdings h
        INNER JOIN (
            SELECT type, name, category, MAX(as_of_date) AS max_date
            FROM finance_holdings
            GROUP BY type, name, category
        ) latest
        ON h.type = latest.type
           AND h.name = latest.name
           AND h.category = latest.category
           AND h.as_of_date = latest.max_date
    """)
    return [self._row_to_finance_holding(r) for r in cur.fetchall()]

def get_finance_holding(self, h_id: str) -> SoloFinanceHolding | None: ...
def update_finance_holding(self, h_id: str, **fields) -> bool: ...
def delete_finance_holding(self, h_id: str) -> bool: ...
```

> **`latest_holdings()` 是净值计算的核心**，必须在 SQL 层完成分组+取最新，否则净值趋势页面在持仓历史变多后会慢。

#### 2.4 Budget Store 方法

**文件**: `solo/core/store.py`

```python
def add_finance_budget(self, b: SoloFinanceBudget) -> None: ...
def list_finance_budgets(self, *, active: bool | None = None, category: str | None = None) -> list[SoloFinanceBudget]: ...
def get_finance_budget(self, b_id: str) -> SoloFinanceBudget | None: ...
def update_finance_budget(self, b_id: str, **fields) -> bool: ...
def delete_finance_budget(self, b_id: str) -> bool: ...

def find_budget(self, period: str, category: str) -> SoloFinanceBudget | None:
    """按 (period, category) 查找已有预算，供 upsert 用。"""
    ...
```

#### 2.5 导入更新

**文件**: `solo/core/store.py` 顶部 `from solo.core.models import (...)` 添加三个新类。

### 验证

```bash
uv run pytest tests/test_solo/test_store.py -v -k "finance"
```

测试必须覆盖：category/account/type 过滤、`latest_holdings()` 分组取最新（多条历史快照只返回每组最新）、budget upsert、空表聚合。

---

## Phase 3: Agent 工具 -- `solo_finance_transaction`

### 目标

实现交易记录工具，注册到 registry。**参数列表不含 record_id / rate**（见 design §2.7 / §7.2）。

### 步骤

#### 3.1 工具定义

**文件**: `solo/tools.py`

完整定义见 design §3.1。关键参数：
- 必填：`type`, `category`, `amount`
- 可选：`currency`, `date`, `account`, `merchant`, `counterparty`, `description`, 投资专属字段, `tags`
- **不含** `rate`（后端兜底）、`record_id`（编排层回填）

#### 3.2 category 校验辅助

```python
_EXPENSE_CATEGORIES = {"dining", "groceries", "transport", "shopping", "housing",
                       "health", "education", "entertainment", "family", "social"}
_INCOME_CATEGORIES = {"salary", "bonus", "investment", "refund", "gift", "other_income"}
_INVESTMENT_CATEGORIES = {"stocks", "fund", "bond", "crypto", "gold", "real_estate", "cash", "insurance"}
_VAGUE_NAMES = {"other", "misc", "general", "unknown", "custom", "test"}

def _is_valid_finance_category(txn_type: str, category: str) -> bool:
    """推荐类别直接通过；新类别需满足约束。"""
    preferred = _EXPENSE_CATEGORIES | _INCOME_CATEGORIES | _INVESTMENT_CATEGORIES
    if category in preferred:
        return True
    # other_income 是推荐类别里的合法例外（已在 preferred 中）
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
    if not _is_valid_finance_category(txn_type, category):
        return {"ok": False, "error": f"Invalid category '{category}' for type '{txn_type}'."}

    currency = str(arguments.get("currency") or "CNY").upper()
    rate, amount_cny = _resolve_currency(currency, amount, arguments.get("rate"))

    local_today = _now()[:10]
    txn = SoloFinanceTransaction(
        id=uuid4().hex[:12],
        record_id=str(arguments.get("record_id") or ""),
        date=str(arguments.get("date") or local_today),
        type=txn_type,
        category=category,
        amount=amount,
        currency=currency,
        amount_cny=amount_cny,
        rate=rate,
        account=str(arguments.get("account") or ""),
        merchant=str(arguments.get("merchant") or ""),
        counterparty=str(arguments.get("counterparty") or ""),
        description=str(arguments.get("description") or ""),
        investment_symbol=str(arguments.get("investment_symbol") or ""),
        investment_name=str(arguments.get("investment_name") or ""),
        investment_quantity=float(arguments.get("investment_quantity") or 0),
        investment_price=float(arguments.get("investment_price") or 0),
        investment_fee=float(arguments.get("investment_fee") or 0),
        tags=str(arguments.get("tags") or ""),
        source="agent",
        metrics_json=str(arguments.get("metrics_json") or "{}"),
        created_at=_now(),
        updated_at=_now(),
    )
    self.store.add_finance_transaction(txn)
    # 返回格式与 _handle_remember / _handle_health_record 对齐：ok + message，不返回 id
    return {"ok": True, "message": f"财务记录已入库：{txn_type}/{category} {amount} {currency} ({txn.date})"}
```

> **返回契约**：成功 `{"ok": True, "message": ...}`，失败 `{"ok": False, "error": ...}`，不返回 id。与项目其他工具一致。

#### 3.4 注册

```python
SoloDomainTool(_tool_finance_transaction(), self._handle_finance_transaction),
```

#### 3.5 导入更新

**文件**: `solo/tools.py` 顶部 `from solo.core.models import (...)` 添加 `SoloFinanceTransaction`。

### 验证

```bash
uv run pytest tests/test_solo/test_tools.py -v -k "finance_transaction"
```

---

## Phase 4: Agent 工具 -- `solo_finance_holding` & `solo_finance_budget`

### 目标

实现持仓快照工具和预算工具。

### 步骤

#### 4.1 solo_finance_holding

**文件**: `solo/tools.py`

定义见 design §3.2。handler 校验 `type ∈ {investment, cash, debt, asset}`、`value > 0`，currency 兜底折算，写入 `add_finance_holding`。返回 `{"ok": True, "message": ...}`。

```python
async def _handle_finance_holding(self, arguments: dict[str, Any]) -> dict[str, Any]:
    h_type = _required_text(arguments, "type")
    name = _required_text(arguments, "name")
    value = float(arguments.get("value") or 0)
    if h_type not in {"investment", "cash", "debt", "asset"}:
        return {"ok": False, "error": f"Invalid holding type '{h_type}'."}
    if value <= 0:
        return {"ok": False, "error": f"value must be positive, got {value}"}

    currency = str(arguments.get("currency") or "CNY").upper()
    rate, value_cny = _resolve_currency(currency, value, None)
    local_today = _now()[:10]
    h = SoloFinanceHolding(
        id=uuid4().hex[:12],
        as_of_date=str(arguments.get("as_of_date") or local_today),
        type=h_type,
        name=name,
        institution=str(arguments.get("institution") or ""),
        category=str(arguments.get("category") or ""),
        investment_symbol=str(arguments.get("investment_symbol") or ""),
        quantity=float(arguments.get("quantity") or 0),
        cost_basis=float(arguments.get("cost_basis") or 0),
        current_price=float(arguments.get("current_price") or 0),
        value=value,
        currency=currency,
        value_cny=value_cny,
        rate=rate,
        description=str(arguments.get("description") or ""),
        tags=str(arguments.get("tags") or ""),
        source="agent",
        linked_account=str(arguments.get("linked_account") or name),
        created_at=_now(),
        updated_at=_now(),
    )
    self.store.add_finance_holding(h)
    return {"ok": True, "message": f"持仓快照已入库：{h_type}/{name} {value} {currency} (截止 {h.as_of_date})"}
```

#### 4.2 solo_finance_budget（带 upsert）

**文件**: `solo/tools.py`

定义见 design §3.3。handler 检查 `(period, category)` 是否已存在，存在则 update amount，否则新增：

```python
async def _handle_finance_budget(self, arguments: dict[str, Any]) -> dict[str, Any]:
    period = str(arguments.get("period") or "monthly").lower()
    category = str(arguments.get("category") or "").strip()
    amount_cny = float(arguments.get("amount_cny") or 0)
    if amount_cny <= 0:
        return {"ok": False, "error": f"amount_cny must be positive, got {amount_cny}"}
    if period not in {"monthly", "weekly", "yearly"}:
        return {"ok": False, "error": f"Invalid period '{period}'."}

    existing = self.store.find_budget(period, category)
    if existing:
        self.store.update_finance_budget(existing.id, amount_cny=amount_cny,
                                         name=str(arguments.get("name") or existing.name),
                                         note=str(arguments.get("note") or existing.note),
                                         updated_at=_now())
        return {"ok": True, "message": f"预算已更新：{period}/{category or '全部'} ¥{amount_cny}"}
    b = SoloFinanceBudget(
        id=uuid4().hex[:12],
        period=period, category=category, amount_cny=amount_cny,
        name=str(arguments.get("name") or ""),
        active=1,
        created_at=_now(), updated_at=_now(),
        note=str(arguments.get("note") or ""),
    )
    self.store.add_finance_budget(b)
    return {"ok": True, "message": f"预算已设置：{period}/{category or '全部'} ¥{amount_cny}"}
```

#### 4.3 注册

```python
SoloDomainTool(_tool_finance_holding(), self._handle_finance_holding),
SoloDomainTool(_tool_finance_budget(), self._handle_finance_budget),
```

### 验证

```bash
uv run pytest tests/test_solo/test_tools.py -v -k "finance_holding or finance_budget"
```

---

## Phase 5: 辅助查询工具 -- `solo_finance_summary`

### 目标

让 agent 能查询财务历史回答用户问题。

### 步骤

**文件**: `solo/tools.py`

定义见 design §3.4。handler 用 `list_finance_transactions` 取数并聚合：

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

    # 收入/支出合计（用 amount_cny 统一折算）
    income = sum(r.amount_cny for r in records if r.type in ("income", "investment_dividend", "investment_income"))
    expense = sum(r.amount_cny for r in records if r.type == "expense")
    by_category = Counter(r.category for r in records)
    by_account = Counter(r.account for r in records if r.account)

    return {
        "ok": True,
        "total": len(records),
        "days": days,
        "type_filter": txn_type,
        "category_filter": category,
        "account_filter": account,
        "income_cny": round(income, 2),
        "expense_cny": round(expense, 2),
        "net_cny": round(income - expense, 2),
        "by_category": dict(by_category),
        "by_account": dict(by_account),
        "recent_records": [r.to_dict() for r in records[:10]],
    }
```

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

**文件**: `solo/prompts.py`

```
| 用户提到资金流动（消费、收入、转账、投资买卖） | → solo_finance_transaction（同一轮与 solo_record 并行调用） |
| 用户对账/报告当前账户余额、持仓市值、负债 | → solo_finance_holding（快照） |
| 用户设定消费预算 | → solo_finance_budget |
```

#### 6.2 新增"财务记录提取原则"章节

**文件**: `solo/prompts.py`

在"健康记录提取原则"之后新增平级章节（完整内容见 design §4.2）。核心要点：
- 三种财务记录的区别（流量 transaction / 存量 holding / 长效 remember）
- 金额提取规则（只提取用户明确说的金额，不估算不拆分）
- 隐含财务信息识别（逐句扫描）
- 交易类型选择（type 表）
- 消费类别选择（category 表）
- 不提取的情况（无金额、计划、稳定事实）

#### 6.3 更新 solo_record 的 SIDE-EFFECT CHECK

**文件**: `solo/tools.py`

在 `_tool_record()` 的 `SIDE-EFFECT CHECK` 中追加（保留 remember / health 部分）：

```
If this message contains money flows (spending, income, transfers, investment buy/sell with
specific amounts), also call solo_finance_transaction in the SAME turn — once per distinct
transaction. Extract ONLY the EXACT amount the user stated; do NOT estimate or split. If the
user reports a current account balance / portfolio value / debt after checking (对账), call
solo_finance_holding instead. If the user sets a spending budget, call solo_finance_budget.
```

### 验证

```bash
uv run python -c "from solo.prompts import build_system_prompt; print(len(build_system_prompt()))"
```

---

## Phase 7: Onboard 后端 API

### 目标

新增 Finance API 路由，查询三张表。**所有读取端点支持过滤（下推 SQL），提供受限写操作。**

### 步骤

#### 7.1 扩展 SoloService

**文件**: `onboard/services/solo_service.py`

添加财务数据查询方法（统一走 `r.metrics` 解析；所有过滤下推 store 层）：

```python
# ── Finance overview ───────────────────────────────────────

def finance_overview(self) -> dict[str, Any]:
    """净值概览：当前净值 + 本月收支 + 资产/负债汇总。"""
    from datetime import datetime, timedelta

    holdings = self.store.latest_holdings()
    assets = sum(h.value_cny for h in holdings if h.type in ("investment", "cash", "asset"))
    debts = sum(h.value_cny for h in holdings if h.type == "debt")
    net_worth = assets - debts

    # 截止日期：所有持仓中最旧的 as_of_date（标注数据新鲜度）
    as_of = min((h.as_of_date for h in holdings), default="")

    # 本月收支
    month_start = datetime.now().strftime("%Y-%m-01")
    month_txns = self.store.list_finance_transactions(date_from=month_start)
    income = sum(t.amount_cny for t in month_txns
                 if t.type in ("income", "investment_dividend", "investment_income"))
    expense = sum(t.amount_cny for t in month_txns if t.type == "expense")

    return {
        "net_worth": round(net_worth, 2),
        "total_assets": round(assets, 2),
        "total_debts": round(debts, 2),
        "as_of_date": as_of,
        "month_income": round(income, 2),
        "month_expense": round(expense, 2),
        "month_net": round(income - expense, 2),
    }

def finance_net_worth_trend(self, days: int = 365) -> dict[str, Any]:
    """历史净值趋势：按 as_of_date 聚合 holdings 快照。"""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    holdings = self.store.list_finance_holdings(as_of_from=cutoff)
    # 按 date 分组求净值
    by_date: dict[str, dict] = {}
    for h in holdings:
        d = by_date.setdefault(h.as_of_date, {"assets": 0, "debts": 0})
        if h.type in ("investment", "cash", "asset"):
            d["assets"] += h.value_cny
        elif h.type == "debt":
            d["debts"] += h.value_cny
    trend = [{"date": d, "net_worth": round(v["assets"] - v["debts"], 2),
              "assets": round(v["assets"], 2), "debts": round(v["debts"], 2)}
             for d, v in sorted(by_date.items())]
    return {"trend": trend}

def list_finance_transactions(self, *, type=None, category=None, account=None,
                              date_from=None, date_to=None, limit=20, offset=0) -> dict[str, Any]:
    records = self.store.list_finance_transactions(
        type=type, category=category, account=account,
        date_from=date_from, date_to=date_to)
    total = len(records)
    page = records[offset:offset + limit]
    return {"items": [r.to_dict() for r in page], "total": total, "limit": limit, "offset": offset}

def finance_transactions_summary(self, type: str | None = None, days: int = 30) -> dict[str, Any]:
    """按 category/account 聚合（用于类别排行、账户分析）。"""
    from datetime import datetime, timedelta
    from collections import defaultdict
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_finance_transactions(type=type, date_from=cutoff)
    by_cat: dict[str, float] = defaultdict(float)
    by_acc: dict[str, float] = defaultdict(float)
    for r in records:
        by_cat[r.category] += r.amount_cny
        if r.account:
            by_acc[r.account] += r.amount_cny
    return {
        "by_category": [{"category": k, "amount": round(v, 2)} for k, v in sorted(by_cat.items(), key=lambda x: -x[1])],
        "by_account": [{"account": k, "amount": round(v, 2)} for k, v in sorted(by_acc.items(), key=lambda x: -x[1])],
        "total": round(sum(by_cat.values()), 2),
        "count": len(records),
    }

def finance_holdings(self) -> dict[str, Any]:
    """当前持仓列表（latest_holdings）。"""
    holdings = self.store.latest_holdings()
    return {"items": [h.to_dict() for h in holdings], "total": len(holdings)}

def finance_allocation(self) -> dict[str, Any]:
    """资产配置：按 type/category 占比。"""
    holdings = self.store.latest_holdings()
    by_type: dict[str, float] = defaultdict(float)
    by_cat: dict[str, float] = defaultdict(float)
    for h in holdings:
        if h.type == "debt":
            continue  # 配置只看资产侧
        by_type[h.type] += h.value_cny
        if h.category:
            by_cat[h.category] += h.value_cny
    return {
        "by_type": [{"type": k, "amount": round(v, 2)} for k, v in by_type.items()],
        "by_category": [{"category": k, "amount": round(v, 2)} for k, v in by_cat.items()],
        "total_assets": round(sum(by_type.values()), 2),
    }

def finance_budgets(self, active: bool = True) -> dict[str, Any]:
    """预算列表 + 当前周期消耗率。"""
    from datetime import datetime
    budgets = self.store.list_finance_budgets(active=active if active else None)
    # 计算各预算当前周期消耗
    now = datetime.now()
    if budgets and budgets[0].period == "monthly":
        period_start = now.strftime("%Y-%m-01")
    else:
        period_start = now.strftime("%Y-%m-%d")  # 简化：weekly/yearly 待细化
    items = []
    for b in budgets:
        spent = sum(t.amount_cny for t in self.store.list_finance_transactions(
            type="expense", category=(b.category or None), date_from=period_start))
        items.append({
            **b.to_dict() if hasattr(b, 'to_dict') else {"id": b.id, "period": b.period, "category": b.category, "amount_cny": b.amount_cny},
            "spent": round(spent, 2),
            "utilization": round(spent / b.amount_cny, 3) if b.amount_cny else 0,
        })
    return {"items": items, "period_start": period_start}

# ── 写操作（受限，见 design §6.2）──────────────────────────

def delete_finance_transaction(self, txn_id: str) -> bool:
    return self.store.delete_finance_transaction(txn_id)

def update_finance_transaction(self, txn_id: str, updates: dict[str, Any]) -> bool:
    # 禁改 type/amount（身份字段）；改类型/金额应删后重建
    forbidden = {"type", "amount", "id"}
    safe = {k: v for k, v in updates.items() if k not in forbidden}
    return self.store.update_finance_transaction(txn_id, **safe)

def delete_finance_holding(self, h_id: str) -> bool:
    return self.store.delete_finance_holding(h_id)

def delete_finance_budget(self, b_id: str) -> bool:
    return self.store.delete_finance_budget(b_id)

def update_finance_budget(self, b_id: str, updates: dict[str, Any]) -> bool:
    # 禁改 category/period（身份字段）；换类别应删后重建
    forbidden = {"category", "period", "id"}
    safe = {k: v for k, v in updates.items() if k not in forbidden}
    return self.store.update_finance_budget(b_id, **safe)
```

> 注意：`SoloFinanceBudget` 若也加了 `to_dict()` 则用之；上面做了 `hasattr` 兜底。budget 的 period_start 计算对 weekly/yearly 简化了，v1.1 细化。

#### 7.2 创建 Finance API 路由

**文件**: `onboard/api/finance.py`

挂在 `/api/solo/finance` 前缀（solo 命名空间隔离）：

```python
"""Finance API routes (solo-only)."""

from fastapi import APIRouter, Query

from onboard.services.solo_service import SoloService

router = APIRouter(prefix="/api/solo/finance", tags=["finance"])


def _service() -> SoloService:
    return SoloService()


@router.get("/overview")
def finance_overview():
    return _service().finance_overview()

@router.get("/net-worth-trend")
def finance_net_worth_trend(days: int = Query(365, ge=1, le=3650)):
    return _service().finance_net_worth_trend(days=days)

@router.get("/transactions")
def finance_transactions(
    type: str | None = None,
    category: str | None = None,
    account: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return _service().list_finance_transactions(
        type=type, category=category, account=account,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset)

@router.get("/transactions/summary")
def finance_transactions_summary(type: str | None = None, days: int = Query(30, ge=1, le=365)):
    return _service().finance_transactions_summary(type=type, days=days)

@router.get("/holdings")
def finance_holdings():
    return _service().finance_holdings()

@router.get("/holdings/allocation")
def finance_allocation():
    return _service().finance_allocation()

@router.get("/budgets")
def finance_budgets(active: bool = True):
    return _service().finance_budgets(active=active)

# ── 写操作（受限）─────────────────────────────────────────

@router.delete("/transactions/{txn_id}")
def delete_finance_transaction(txn_id: str):
    return {"ok": _service().delete_finance_transaction(txn_id)}

@router.patch("/transactions/{txn_id}")
def update_finance_transaction(txn_id: str, updates: dict):
    return {"ok": _service().update_finance_transaction(txn_id, updates)}

@router.delete("/holdings/{h_id}")
def delete_finance_holding(h_id: str):
    return {"ok": _service().delete_finance_holding(h_id)}

@router.delete("/budgets/{b_id}")
def delete_finance_budget(b_id: str):
    return {"ok": _service().delete_finance_budget(b_id)}

@router.patch("/budgets/{b_id}")
def update_finance_budget(b_id: str, updates: dict):
    return {"ok": _service().update_finance_budget(b_id, updates)}
```

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
curl -s http://localhost:8090/api/solo/finance/holdings/allocation | python -m json.tool
curl -s http://localhost:8090/api/solo/finance/budgets | python -m json.tool
```

---

## Phase 8: Onboard 前端 -- 类型定义与 API 客户端

### 步骤

#### 8.1 扩展类型定义

**文件**: `onboard/frontend/src/api/types.ts`

```typescript
// ── Finance ────────────────────────────────────────────────

export type FinanceTxnType = 'income' | 'expense' | 'transfer' |
  'investment_buy' | 'investment_sell' | 'investment_dividend' | 'investment_income';
export type FinanceHoldingType = 'investment' | 'cash' | 'debt' | 'asset';

export interface SoloFinanceTransaction {
  id: string;
  record_id: string;
  date: string;
  type: FinanceTxnType;
  category: string;
  amount: number;
  currency: string;
  amount_cny: number;
  rate: number;
  account: string;
  merchant: string;
  counterparty: string;
  description: string;
  investment_symbol: string;
  investment_name: string;
  investment_quantity: number;
  investment_price: number;
  investment_fee: number;
  tags: string;
  source: string;
  metrics_json: string;
  created_at: string;
  updated_at: string;
}

export interface SoloFinanceHolding {
  id: string;
  as_of_date: string;
  type: FinanceHoldingType;
  name: string;
  institution: string;
  category: string;
  investment_symbol: string;
  quantity: number;
  cost_basis: number;
  current_price: number;
  value: number;
  currency: string;
  value_cny: number;
  rate: number;
  description: string;
  tags: string;
  source: string;
  linked_account: string;
  created_at: string;
  updated_at: string;
}

export interface SoloFinanceBudget {
  id: string;
  period: string;       // monthly|weekly|yearly
  category: string;     // '' = 总预算
  amount_cny: number;
  name: string;
  active: number;
  start_date: string;
  end_date: string;
  note: string;
  // 计算字段（来自 service）
  spent?: number;
  utilization?: number;
}

export interface FinanceOverview {
  net_worth: number;
  total_assets: number;
  total_debts: number;
  as_of_date: string;
  month_income: number;
  month_expense: number;
  month_net: number;
}
```

#### 8.2 扩展 API 客户端

**文件**: `onboard/frontend/src/api/client.ts`

```typescript
// ── Finance (solo-only) ──────────────────────────────────────
finance: {
  overview: () =>
    request<FinanceOverview>(`/api/solo/finance/overview`),
  netWorthTrend: (days: number = 365) =>
    request<{ trend: { date: string; net_worth: number; assets: number; debts: number }[] }>(`/api/solo/finance/net-worth-trend${query({ days })}`),
  transactions: (params: Record<string, QueryValue> = {}) =>
    request<PaginatedResponse<SoloFinanceTransaction>>(`/api/solo/finance/transactions${query(params)}`),
  transactionsSummary: (type?: FinanceTxnType, days: number = 30) =>
    request<{ by_category: { category: string; amount: number }[]; by_account: { account: string; amount: number }[]; total: number; count: number }>(`/api/solo/finance/transactions/summary${query({ ...(type ? { type } : {}), days })}`),
  holdings: () =>
    request<{ items: SoloFinanceHolding[]; total: number }>(`/api/solo/finance/holdings`),
  allocation: () =>
    request<{ by_type: { type: string; amount: number }[]; by_category: { category: string; amount: number }[]; total_assets: number }>(`/api/solo/finance/holdings/allocation`),
  budgets: (active: boolean = true) =>
    request<{ items: SoloFinanceBudget[]; period_start: string }>(`/api/solo/finance/budgets${query({ active })}`),
  deleteTransaction: (id: string) =>
    request<{ ok: boolean }>(`/api/solo/finance/transactions/${id}`, { method: 'DELETE' }),
  updateTransaction: (id: string, updates: Partial<SoloFinanceTransaction>) =>
    request<{ ok: boolean }>(`/api/solo/finance/transactions/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }),
  deleteHolding: (id: string) =>
    request<{ ok: boolean }>(`/api/solo/finance/holdings/${id}`, { method: 'DELETE' }),
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

## Phase 9: Onboard 前端 -- 侧边栏与路由

### 步骤

#### 9.1 修改 Sidebar

**文件**: `onboard/frontend/src/components/Sidebar.tsx`

Finance 仅 solo 可见，插入到 Health 之后：

```typescript
const SOLO_FINANCE_ITEM = ['/finance', '💰', 'Finance'] as const;
// solo 分支: [...commonItems(前3), SOLO_HEALTH_ITEM, SOLO_FINANCE_ITEM, ...commonItems(3及之后)]
```

#### 9.2 注册路由

**文件**: `onboard/frontend/src/App.tsx`

```typescript
const Finance = lazy(() => import('./pages/Finance').then((m) => ({ default: m.Finance })));
{ path: 'finance', element: <SuspenseLoader><Finance /></SuspenseLoader> },
```

> wolo 模式下 `/finance` 重定向回 Dashboard（防御性处理）。

### 验证

```bash
cd onboard/frontend && npx tsc --noEmit
# solo 模式下侧边栏出现 Finance 入口（在 Health 之后）；wolo 不出现
```

---

## Phase 10: Onboard 前端 -- Finance 页面

### 目标

实现完整的 Finance 页面。**含时间范围选择器（3M/6M/12M/all），子组件接收 days prop。**

### 步骤

#### 10.1 主页面组件

**文件**: `onboard/frontend/src/pages/Finance.tsx`

页面结构（复用 Dashboard 的 Section / StatsCard 模式）：

```
顶部: 时间范围选择器 [3M | 6M | 12M | all]（selectedDays 状态贯穿各 Zone）

Zone 1: NetWorthCards ← api.finance.overview()
Zone 2: CashflowTrend ← api.finance.transactionsSummary(undefined, selectedDays) + 月度聚合
Zone 3: 双列
  - CategoryRanking ← api.finance.transactionsSummary('expense', selectedDays)
  - BudgetTracking ← api.finance.budgets()
Zone 4: AllocationPie ← api.finance.allocation()
Zone 5: 双列
  - HoldingsTable ← api.finance.holdings()
  - NetWorthTrend ← api.finance.netWorthTrend(selectedDays)
Zone 6: AccountBalances ← api.finance.holdings()（按 linked_account 分组，负债红色）
Zone 7: TransactionTimeline ← api.finance.transactions({ limit })
```

#### 10.2 子组件

| 组件 | 文件 | 关键 props |
|------|------|-----------|
| `NetWorthCards` | `components/finance/NetWorthCards.tsx` | `overview: FinanceOverview` |
| `CashflowTrend` | `components/finance/CashflowTrend.tsx` | `days` |
| `CategoryRanking` | `components/finance/CategoryRanking.tsx` | `days` |
| `BudgetTracking` | `components/finance/BudgetTracking.tsx` | （无 days，预算是周期内累计） |
| `AllocationPie` | `components/finance/AllocationPie.tsx` | （无 days，当前配置） |
| `HoldingsTable` | `components/finance/HoldingsTable.tsx` | （无 days，当前持仓） |
| `NetWorthTrend` | `components/finance/NetWorthTrend.tsx` | `days` |
| `AccountBalances` | `components/finance/AccountBalances.tsx` | （无 days，当前余额） |
| `TransactionTimeline` | `components/finance/TransactionTimeline.tsx` | （可加 type/category 筛选） |

图表库沿用 Recharts。金额格式化统一用 `¥` + 千分位；负值/负债红色；多币种原始金额带币种符号。

**空数据降级**：无持仓时净值卡片显示"暂无对账数据"；无交易时流水显示"暂无记录"。

### 验证

```bash
cd onboard/frontend && npx tsc --noEmit
# 访问 /finance 确认所有 Zone 渲染
# 切换时间范围选择器确认趋势图刷新
# 确认空数据降级正常
# 确认负债显示为红色正数
# 确认 Solo/Wolo 切换（wolo 下不显示 Finance）
```

---

## Phase 11: 测试与收尾

### 11.1 测试用例清单（必须补全）

新增 `tests/test_solo/test_finance.py`（Store 层）并在 `test_tools.py` 中补 finance 用例：

#### Store 层
- `add_finance_transaction` + `get_finance_transaction` 往返一致
- `list_finance_transactions` type/category/account/date_from/date_to/limit 组合过滤（**全部下推 SQL**）
- `list_finance_transactions` 空表返回 `[]`
- `latest_holdings()`：同 `(type,name,category)` 多条历史快照只返回 `as_of_date` 最大那条
- `latest_holdings()` 不同分组各自取最新
- `find_budget(period, category)` upsert：已存在则 update，不存在则 add
- `finance_transaction_categories` / `finance_transaction_accounts` 计数正确
- schema 迁移 v8 幂等：对已有 finance 表的库重复跑迁移不报错
- 多币种：`_resolve_currency` 对 USD/HKD/CNY 的折算（CNY rate=1.0）

#### Tool handler 层
- `_handle_finance_transaction` 合法标准类别成功，返回 `{"ok": True, "message": ...}`（**不含 id**）
- `_handle_finance_transaction` 合法新类别（如 `telecom`）成功
- `_handle_finance_transaction` 拒绝 vague 名（`other`/`misc`）→ `{"ok": False, "error": ...}`
- `_handle_finance_transaction` 拒绝 amount ≤ 0
- `_handle_finance_transaction` 多币种：currency=USD 时 amount_cny 按 _FX_RATES_TO_CNY 折算
- `_handle_finance_holding` type 校验（拒绝非法 type）
- `_handle_finance_holding` value > 0 校验
- `_handle_finance_holding` debt 类型 value 存正数
- `_handle_finance_budget` 首次设置 vs 重复设置（同 period+category → update）
- `_handle_finance_budget` 拒绝 amount_cny ≤ 0、非法 period
- `_handle_finance_summary` type/category/account 过滤
- `_handle_finance_summary` income/expense 聚合用 amount_cny（多币种折算后）
- `_handle_finance_summary` 空结果返回 total: 0

#### 集成层（通过 agent）
- 消息"午饭花了35，打车15"触发**两次** `solo_finance_transaction`（expense/dining + expense/transport）
- 消息"和朋友AA火锅花了120"正确提取 amount=120（人均，不是240）
- 消息"工资到账1万8"触发 income/salary amount=18000
- 消息"买了100股茅台成交价1680"触发 investment_buy，amount=168000，investment_quantity=100，investment_price=1680
- 消息"查了下账户有23万"触发 `solo_finance_holding`（快照，不是 transaction）
- 消息"餐饮每月预算2000"触发 `solo_finance_budget`
- 消息"我月工资到手1万8"走 `solo_remember`（稳定事实，不进 finance 表）
- 消息"$200"识别 currency=USD
- 消息"想买辆车"不触发任何 finance 工具（计划，未发生）

#### API 层
- `GET /api/solo/finance/overview` 返回净值/收支
- `GET /api/solo/finance/transactions?type=expense&category=dining` 过滤正确
- `GET /api/solo/finance/transactions/summary?type=expense` 类别排行
- `GET /api/solo/finance/holdings/allocation` 资产配置占比
- `GET /api/solo/finance/budgets` 含消耗率
- `GET /api/solo/finance/net-worth-trend?days=365` 历史趋势
- `DELETE /api/solo/finance/transactions/{id}` 删除成功
- `PATCH /api/solo/finance/transactions/{id}` 不能改 type/amount（被忽略）
- `PATCH /api/solo/finance/budgets/{id}` 不能改 category/period（被忽略）
- wolo 模式下 `/api/solo/finance/*` 不被前端调用

### 11.2 端到端验证

1. solo CLI 发送含财务信息的消息，验证 agent 自动调用对应工具
2. Onboard Finance 页面验证数据展示
3. 时间范围选择器切换后趋势图刷新
4. 预算消耗率正确计算
5. 净值计算（资产 − 负债）正确
6. 负债显示红色正数
7. Solo/Wolo 切换
8. 空数据场景

### 11.3 代码质量

```bash
uv run ruff check solo/ onboard/
cd onboard/frontend && npx tsc --noEmit
uv run pytest -q tests/test_solo/
```

### 11.4 CHANGELOG 更新

```markdown
### Added
- **Finance module (solo-only)**: Three structured tables (`finance_transactions`, `finance_holdings`, `finance_budgets`) for tracking spending, income, investment positions, budgets, and net worth. Not available in wolo.
- **Transaction/Holding/Budget distinction**: Transactions record money flows (income/expense/transfer/investment buy-sell); Holdings record asset/debt snapshots (for net worth); Budgets set recurring spending limits. Investment buy/sell are asset-form conversions and do not count as income/expense.
- **Multi-currency support**: All amounts stored in original currency + CNY-converted (`amount_cny`) with rate; backend FX fallback for non-CNY (approximate rates, flagged).
- **`solo_finance_transaction` / `solo_finance_holding` / `solo_finance_budget` tools**: Agent auto-extracts money flows, balance snapshots, and budgets from user messages (same-turn pattern). Amounts extracted EXACTLY as stated — no estimation/splitting.
- **`solo_finance_summary` tool**: Agent can query financial history (with type/category/account filters) to answer finance questions.
- **Onboard Finance page (solo-only)**: Net worth overview, monthly cashflow trend, expense category ranking, budget tracking, asset allocation pie, holdings table, net worth trend line, account balances, transaction timeline. Time range selector (3M/6M/12M/all).
- **Finance record editing (restricted)**: DELETE / PATCH endpoints; PATCH cannot change identity fields (type/amount for transactions, category/period for budgets).
```

### 验证清单

- [ ] 三张表正确创建（schema v8 幂等迁移，含全部索引）
- [ ] Store CRUD 工作，`list_*` 过滤全部下推 SQL
- [ ] `latest_holdings()` SQL 层分组取最新
- [ ] `find_budget()` upsert 正确
- [ ] `_resolve_currency` 多币种折算（CNY/USD/HKD）
- [ ] 四个 finance 工具可被 agent 调用
- [ ] 金额精确提取（不估算不拆分，AA 按人均）
- [ ] 流量(transaction) vs 存量(holding) vs 长效(remember) 正确区分
- [ ] 提示词优化后 agent 能识别财务信息
- [ ] Finance API 所有端点返回正确，支持过滤
- [ ] `overview` 返回净值（资产−负债）+ 本月收支
- [ ] `transactions/summary` 类别/账户排行
- [ ] `holdings/allocation` 资产配置占比
- [ ] `budgets` 含消耗率
- [ ] `net-worth-trend` 历史趋势
- [ ] DELETE / PATCH 写操作可用，PATCH 禁改身份字段
- [ ] 前端 Finance 页面正确渲染
- [ ] 时间范围选择器切换后趋势图刷新
- [ ] 负债显示红色正数
- [ ] 多币种原始金额带币种符号
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
| `onboard/frontend/src/components/finance/NetWorthCards.tsx` | 净值概览卡片 |
| `onboard/frontend/src/components/finance/CashflowTrend.tsx` | 月度收支趋势 |
| `onboard/frontend/src/components/finance/CategoryRanking.tsx` | 消费类别排行 |
| `onboard/frontend/src/components/finance/BudgetTracking.tsx` | 预算追踪 |
| `onboard/frontend/src/components/finance/AllocationPie.tsx` | 资产配置饼图 |
| `onboard/frontend/src/components/finance/HoldingsTable.tsx` | 投资持仓表 |
| `onboard/frontend/src/components/finance/NetWorthTrend.tsx` | 净值趋势折线 |
| `onboard/frontend/src/components/finance/AccountBalances.tsx` | 账户余额列表 |
| `onboard/frontend/src/components/finance/TransactionTimeline.tsx` | 流水时间线 |
| `tests/test_solo/test_finance.py` | Store/Tool/集成层 finance 测试 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `solo/core/models.py` | 新增三个 finance dataclass（含 metrics property） |
| `solo/core/store.py` | 三张表 DDL + schema v8 幂等迁移 + CRUD（含 `latest_holdings`/`find_budget`） |
| `solo/tools.py` | 四个 finance 工具定义+处理器 + `_resolve_currency` + category 校验；更新 `_tool_record` 的 SIDE-EFFECT CHECK |
| `solo/prompts.py` | 新增"财务记录提取原则"章节（流量/存量/长效区分、金额规则、隐含识别），更新路由决策表 |
| `onboard/server.py` | 注册 `finance.router` |
| `onboard/services/solo_service.py` | finance 查询方法（含 `finance_overview`/`latest_holdings` 聚合）+ 受限写操作 |
| `onboard/frontend/src/components/Sidebar.tsx` | 添加 Finance 侧边栏项（仅 solo） |
| `onboard/frontend/src/App.tsx` | 添加 Finance 路由（solo-only） |
| `onboard/frontend/src/api/types.ts` | Finance 类型定义 |
| `onboard/frontend/src/api/client.ts` | finance API 方法（含写操作） |

---

## 实施时间估算

| Phase | 预计时间 | 依赖 |
|-------|----------|------|
| Phase 1: 数据模型与三张表 | 1.5h | 无 |
| Phase 2: Store CRUD（含 latest_holdings/find_budget/汇率兜底） | 2h | Phase 1 |
| Phase 3: solo_finance_transaction 工具 | 1.5h | Phase 2 |
| Phase 4: holding + budget 工具 | 1h | Phase 2 |
| Phase 5: solo_finance_summary 工具 | 0.5h | Phase 2 |
| Phase 6: 提示词优化 | 1h | Phase 3-5 |
| Phase 7: Onboard 后端 API（含受限写） | 2h | Phase 2 |
| Phase 8: 前端类型 + API 客户端 | 0.5h | Phase 7 |
| Phase 9: 侧边栏 + 路由 | 0.25h | Phase 8 |
| Phase 10: Finance 页面 + 组件 | 5h | Phase 9 |
| Phase 11: 测试 + 收尾 | 2.5h | Phase 1-10 |
| **总计** | **~17.75h** | |

> 比 health（~13.5h）多约 4h，主要来自：三张表（vs health 一张）、`latest_holdings` SQL 实现、多币种折算、净值计算口径。

---

## 关键风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 交易与持仓混用（用户说"账户有23万"被记成 transaction） | 净值/收支计算混乱 | prompt 强调流量/存量区分（design §4.2），集成测试覆盖 |
| 金额被 LLM 估算/拆分（AA 算成总价） | 数据失真 | prompt 强调"只提取明确金额不拆分"，工具层无强制校验（依赖 prompt），集成测试覆盖 AA 场景 |
| 多币种未折算导致聚合错误 | 净值/收支不准 | `amount_cny` 后端兜底折算 + `fx_estimated` 标注；聚合统一用 amount_cny |
| `latest_holdings` 在 service 层实现导致慢 | 净值页面卡顿 | 必须 SQL 层 GROUP BY + MAX(as_of_date) |
| 持仓历史快照堆积 | latest_holdings 查询变慢 | 走索引 + v1.1 演进快照归档/压缩 |
| 投资买卖被误计入收支结余 | 结余虚增/虚减 | type 区分（investment_buy/sell 不计入 income/expense 聚合），prompt 明确 |
| 负债符号混乱（正负不一） | 净值计算错误 | 统一负债存正数，净值公式减去（design §2.7），测试覆盖 |
| wolo 模式泄露财务数据 | 隐私问题 | 命名空间隔离 + 前端条件渲染（design §5.6） |
| 汇率近似值偏差大 | 外币净值不准 | 标注 `fx_estimated`，v1.2 接实时 API |
| budget upsert 误判（同 category 不同 period 重复） | 预算重复 | `find_budget(period, category)` 联合键，测试覆盖 |
