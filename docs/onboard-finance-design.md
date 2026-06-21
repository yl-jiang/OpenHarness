# Onboard Finance Page -- 设计方案（精简版）

> 为 Onboard 应用新增 **Finance（个人消费追踪）** 模块（**仅适用于 solo**，不适用于 wolo）。
> 聚焦**日常开销记录、理财盈亏流水、预算控制、图表分析与消费洞察**；不追持仓净值、不追资产配置、不做多币种折算。
>
> 架构对齐已有 **Health 模块**（schema v7，单表 + 两工具 + 一页面）：本模块从 **schema v8** 起，采用 **2 张结构化表 + 3 个 Agent 工具 + 1 个 Onboard 页面**。

---

## 0. 与旧设计的差异（为什么精简）

旧版（`onboard-finance-design.md` 前身）按完整个人财务系统设计：3 张表（交易/持仓/预算）、4 个工具、多币种折算、净值快照、资产配置饼图，约 18h 工作量。经需求澄清后确认真实诉求为「轻量日常消费追踪」，故做以下收敛：

| 维度 | 旧设计 | 精简后 |
|------|--------|--------|
| 数据库表 | 3 张（含 `finance_holdings` 持仓/负债快照） | **2 张**（删 holdings） |
| 持仓/净值 | latest_holdings 分组取最新 + 净值趋势 + 资产配置饼图 | **全部不做** |
| 投资记录 | buy/sell/dividend/income 四种 + 标的/数量/成本价/单价/手续费全套字段 | **压成「盈/亏」两态**，标的信息塞进 description/tags |
| 多币种 | amount/amount_cny/rate + 汇率兜底折算 + fx_estimated | **不做折算**；currency 仅作标注，聚合时按各自币种分组 |
| 账户维度 | account 单独索引 + 按账户聚合排行 | account 降级为**可选备注**（可搜索，不专门聚合） |
| 录入 | 聊天提取 + 可能手填 | **仅聊天自动提取**，页面无录入表单 |
| 工时 | ~18h | **~9–10h** |

被砍掉的能力（holdings/净值/资产配置/账单导入/多币种折算）统一挪到 §7 演进路线，作为未来可选方向，而非 v1.0 范围。

---

## 1. 架构概述

### 1.1 为什么需要独立模块

现有 `solo_record` / records 表**没有任何金额字段**（无 amount / spending / cost / price / budget），无法承载量化财务数据。靠关键词从非结构化日志里"打捞"消费精度低、且无法做金额聚合。

因此财务模块采用与 Health 模块一致的 **写时结构化** 策略：用户记录日常生活的同一轮中，agent 自动把消费/收入/理财盈亏写入专用结构化表。

### 1.2 两类核心实体

财务活动在本方案里只分两类，对应两张表：

| 实体 | 表 | 语义 | 示例 |
|------|-----|------|------|
| **交易流水** | `finance_transactions` | 一次性、有时间戳的资金进出（支出/收入/转账/理财盈亏） | 午餐 ¥35、工资 ¥15000、基金赚了 ¥300 |
| **预算** | `finance_budgets` | 周期性支出上限（月度为主） | 餐饮月预算 ¥2000 |

> **不做持仓快照**：用户报"现在账户有 23 万"这类对账信息不进结构化表。如果将来有净值追踪需求，走 §7 v1.1 演进新增 holdings 表，不影响现有两张表。

### 1.3 数据流全貌

```
示例 1: 日常消费
用户消息: "中午和朋友吃了一顿火锅，AA制花了120块"
    │
    ▼
Solo Agent
    ├─① solo_record(date=今天, tags="餐饮,朋友", emotion="积极", ...)
    │     → records 表（日常日志）
    └─② solo_finance_transaction(
            type="expense",
            category="dining",
            amount=120.00,
            currency="CNY",
            counterparty="朋友",
            description="和朋友AA火锅",
            tags="朋友,火锅",
            date=今天
        )
          → finance_transactions 表

示例 2: 收入
用户消息: "工资到账了，到手1万8"
    │
    ▼
Solo Agent
    ├─① solo_record(date=今天, tags="收入,工资", ...)
    └─② solo_finance_transaction(
            type="income",
            category="salary",
            amount=18000.00,
            currency="CNY",
            description="工资到手",
            date=今天
        )

示例 3: 理财盈亏（只记盈亏流水，不追持仓）
用户消息: "今天基金赚了300块"
    │
    ▼
Solo Agent
    ├─① solo_record(date=今天, tags="理财,基金", ...)
    └─② solo_finance_transaction(
            type="invest_gain",
            category="fund",
            amount=300.00,
            currency="CNY",
            description="基金浮盈",
            date=今天
        )
          → 注意：不写持仓、不写买卖动作，只记"赚了300"这一笔结果

示例 4: 设定预算
用户消息: "餐饮每月预算2000"
    │
    ▼
Solo Agent
    └─ solo_finance_budget(
            period="monthly",
            category="dining",
            amount_cny=2000.00
        )
          → 若 (monthly, dining) 已存在则 upsert 覆盖金额

示例 5: 稳定财务事实（进 memory，不进 finance 表）
用户消息: "我月工资到手1万8，每月房贷6200"
    │
    ▼
Solo Agent
    └─ solo_remember → financial_profile.md
         （稳定的薪资/负债信息属于长效事实，不是事件级交易）
```

### 1.4 与现有模式的关系

| 模式 | 工具 | 写入目标 | 触发时机 |
|------|------|----------|----------|
| 日常记录 | `solo_record` | `records` 表 | 每次日常内容 |
| 长效记忆 | `solo_remember` | `memory/` 文件 | 检测到稳定个人事实（含薪资、房贷利率等） |
| 健康记录 | `solo_health_record` | `health_records` 表 | 检测到健康事件 |
| **财务交易** | **`solo_finance_transaction`** | **`finance_transactions` 表** | 检测到一次性资金进出 |
| **财务预算** | **`solo_finance_budget`** | **`finance_budgets` 表** | 用户设定消费预算 |
| 待办提取 | `solo_add_todo` | `todos` 表 | 检测到待办/计划 |

---

## 2. 结构化财务数据库

### 2.1 表设计：finance_transactions

```sql
CREATE TABLE IF NOT EXISTS finance_transactions (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL DEFAULT '',      -- 关联 records 表（best-effort，见 §2.6）
    date TEXT NOT NULL,                       -- YYYY-MM-DD（与 solo_record 同源，见 §7.4）
    type TEXT NOT NULL,                       -- expense|income|transfer|invest_gain|invest_loss
    category TEXT NOT NULL,                   -- 消费/收入类别，见下方体系
    amount REAL NOT NULL,                     -- 金额（正数；币种见 currency）
    currency TEXT NOT NULL DEFAULT 'CNY',     -- ISO 货币代码（仅标注，不做折算，见 §2.7）
    account TEXT NOT NULL DEFAULT '',         -- 可选备注：支付宝/微信/招行卡/...（可搜索，不专门聚合）
    counterparty TEXT NOT NULL DEFAULT '',    -- 交易对方（人名/公司，可选）
    description TEXT NOT NULL DEFAULT '',     -- 详细描述；标的信息（如"茅台"）也写这里
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent',     -- agent|user|import
    metrics_json TEXT NOT NULL DEFAULT '{}',  -- 扩展字段
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_finance_txn_date ON finance_transactions(date);
CREATE INDEX IF NOT EXISTS idx_finance_txn_type ON finance_transactions(type);
CREATE INDEX IF NOT EXISTS idx_finance_txn_category ON finance_transactions(category);
CREATE INDEX IF NOT EXISTS idx_finance_txn_record_id ON finance_transactions(record_id);
```

#### type（交易类型，5 种）

| type | 含义 | 典型 |
|------|------|------|
| `expense` | 支出/消费 | 餐饮、交通、购物、房租 |
| `income` | 收入 | 工资、奖金、退款、红包收入 |
| `transfer` | 账户间转账 | 支付宝→银行卡（不改变净值） |
| `invest_gain` | 理财盈利 | 基金赚了 300、股票浮盈、余额宝利息 |
| `invest_loss` | 理财亏损 | 股票亏了 500、基金回撤 |

> **理财只记「盈/亏」结果，不记买卖动作**：用户说"买了 100 股茅台"如果没有同时说盈亏，不强制入库（属于持仓事件，本方案不追）；用户说"茅台赚了 3000"才记 `invest_gain`。这样把 7 种旧 type 压成 5 种，匹配"只记盈亏流水"的真实诉求。

#### category（类别体系）

采用与 Health 一致的 **推荐类别 + 受约束新类别**：

**支出 expense 推荐类别：**

| category | 中文 | 典型 |
|----------|------|------|
| `dining` | 餐饮 | 午餐、外卖、聚餐、咖啡 |
| `groceries` | 生鲜日用 | 超市、菜市场、日用品 |
| `transport` | 交通出行 | 打车、地铁、加油、停车 |
| `shopping` | 购物 | 服饰、数码、家居 |
| `housing` | 居住 | 房租、物业、水电煤、宽带 |
| `health` | 医疗健康 | 看病、买药、体检 |
| `education` | 教育学习 | 课程、书籍、培训 |
| `entertainment` | 娱乐 | 电影、游戏、演出、旅行 |
| `family` | 家庭育儿 | 子女教育、家庭开支 |
| `social` | 社交人情 | 礼金、请客、红包 |

**收入 income 推荐类别：**

| category | 中文 |
|----------|------|
| `salary` | 工资薪水 |
| `bonus` | 奖金提成 |
| `refund` | 退款报销 |
| `gift` | 礼金红包收入 |
| `other_income` | 其他收入 |

**理财 invest_gain/loss 类别（category 表示标的类型，描述写进 description）：**

| category | 含义 |
|----------|------|
| `stocks` | 股票 |
| `fund` | 基金 |
| `bond` | 债券 |
| `crypto` | 加密货币 |
| `gold` | 黄金/贵金属 |
| `savings` | 存款/余额宝利息 |
| `insurance` | 理财型保险 |

**新类别约束**（同 Health）：单个英文小写单词、`isalpha()`、长度 ≤ 20、不在 `VAGUE_NAMES = {other, misc, general, unknown, custom, test}`（`other_income` 是推荐类别里的合法例外）。

### 2.2 表设计：finance_budgets（预算）

```sql
CREATE TABLE IF NOT EXISTS finance_budgets (
    id TEXT PRIMARY KEY,
    period TEXT NOT NULL DEFAULT 'monthly',   -- monthly|weekly|yearly
    category TEXT NOT NULL,                   -- 限定的消费类别（expense 类别）；'' 表示总预算
    amount REAL NOT NULL,                     -- 预算金额（币种默认 CNY；见 §2.7）
    currency TEXT NOT NULL DEFAULT 'CNY',
    name TEXT NOT NULL DEFAULT '',            -- 预算名称（可选）
    active INTEGER NOT NULL DEFAULT 1,        -- 1=生效中，0=已停用
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_finance_budget_period ON finance_budgets(period);
CREATE INDEX IF NOT EXISTS idx_finance_budget_category ON finance_budgets(category);
CREATE INDEX IF NOT EXISTS idx_finance_budget_active ON finance_budgets(active);
```

> 预算由用户**主动设置**（"餐饮预算每月 2000"），agent 提取后写入。预算消耗 = 当前周期内该 category 的 `expense` 交易金额累加。`(period, category)` 作为 upsert 联合键。

### 2.3 Dataclass 模型

在 `solo/core/models.py` 新增两个 frozen dataclass（与 `SoloHealthRecord` 同风格，含 `metrics` property、`from_json`/`to_dict`/`to_json`）：

```python
@dataclass(frozen=True)
class SoloFinanceTransaction:
    """One structured finance transaction (expense/income/transfer/invest gain-loss)."""
    id: str
    record_id: str = ""
    date: str = ""
    type: str = ""              # expense|income|transfer|invest_gain|invest_loss
    category: str = ""
    amount: float = 0.0         # 原始币种（不做折算）
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

    # from_json / to_dict / to_json 同 SoloHealthRecord 模式


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
    # metrics property + serialization 同上（可选；budget 一般无需 metrics_json）
```

### 2.4 Store 方法

在 `SoloStore` 中新增（**所有 list 方法的过滤字段全部下推 SQL**）：

```python
# ── Finance transactions ───────────────────────────────────
def add_finance_transaction(self, txn: SoloFinanceTransaction) -> None: ...
def list_finance_transactions(
    self, *,
    type: str | None = None,
    category: str | None = None,
    account: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
) -> list[SoloFinanceTransaction]: ...
def get_finance_transaction(self, txn_id: str) -> SoloFinanceTransaction | None: ...
def update_finance_transaction(self, txn_id: str, **fields) -> bool: ...
def delete_finance_transaction(self, txn_id: str) -> bool: ...
def finance_transaction_categories(self) -> dict[str, int]: ...

# ── Finance budgets ────────────────────────────────────────
def add_finance_budget(self, b: SoloFinanceBudget) -> None: ...
def list_finance_budgets(self, *, active: bool | None = None, category: str | None = None) -> list[SoloFinanceBudget]: ...
def get_finance_budget(self, b_id: str) -> SoloFinanceBudget | None: ...
def update_finance_budget(self, b_id: str, **fields) -> bool: ...
def delete_finance_budget(self, b_id: str) -> bool: ...
def find_budget(self, period: str, category: str) -> SoloFinanceBudget | None:
    """按 (period, category) 查找已有预算，供 upsert 用。简单等值查询，无需窗口函数。"""
```

> **没有 `latest_holdings`**：旧设计的 SQL 窗口函数分组取最新逻辑已随 holdings 表一并删除，Store 层回归纯 CRUD + 等值查询，与 health 一致。

### 2.5 Schema 迁移

将 `_SCHEMA_VERSION` 从 **7 提升到 8**（v7 已被 health 占用）。在 `_apply_migrations()` 末尾追加**幂等**迁移（沿用 health v7 的 `executescript + CREATE TABLE IF NOT EXISTS` 模式）：

```python
# v8: finance tables（幂等 CREATE TABLE IF NOT EXISTS）
self._conn.executescript("""
    CREATE TABLE IF NOT EXISTS finance_transactions (...);
    CREATE INDEX IF NOT EXISTS idx_finance_txn_date ...;
    -- ... finance_budgets 全部 DDL
""")
self._conn.commit()
```

> **不要**用 `_table_exists()` 探测（项目无此方法，IF NOT EXISTS 已幂等）。

### 2.6 数据完整性与关联策略

#### record_id 关联（同 Health，best-effort）

- `solo_finance_transaction` 参数列表**不含 record_id**（LLM 无法获知同轮 record id）。
- 由编排层在 `solo_record` 成功后 best-effort 回填（沿用 health 的 `post_turn_backfill` 机制：工具把新建 txn id 暂存 `self._pending_finance_ids`，turn 结束后 link 到 `list(self._created_record_ids)[-1]`）。
- 回填失败则 record_id 留空，不阻塞主流程。

#### 汇率 / 多币种（重要：本方案不做折算）

- **不做 amount_cny / rate 折算**。`amount` 即原始币种金额，`currency` 仅作标注。
- 聚合统计（月度支出、类别排行等）**默认只聚合 `currency='CNY'` 的记录**；若存在外币记录，API 层按 `currency` 分组各自返回，前端展示时带币种符号（`¥`/`$`/`€`），不混算。
- 这是有意的取舍：个人日常消费 99% 是 CNY，强行折算反而引入汇率幻觉与维护成本。外币净值/折算放到 §7 v1.2 演进。

#### 负债 / 净值

- **本方案不涉及净值计算**。没有 holdings 表，没有"资产 − 负债"公式。
- 理财亏损 `invest_loss` 的 amount 仍存**正数**（如"亏了 500" → amount=500，type=invest_loss），由聚合逻辑按 type 区分加减。

---

## 3. Agent 工具设计

### 3.1 `solo_finance_transaction` 工具

```python
def _tool_finance_transaction() -> ToolDefinition:
    return _definition(
        "solo_finance_transaction",
        (
            "Record a STRUCTURED finance transaction into the dedicated finance database. "
            "Call this whenever the user's message contains a money flow: spending, income, "
            "transfer, or an investment gain/loss RESULT. Extract the EXACT amount the user "
            "stated — do NOT estimate, infer, or split amounts the user did not specify. "
            "IMPORTANT: Finance info may appear INCIDENTALLY in a daily record "
            "(e.g. '和朋友吃饭花了120' → record expense 120). Scan the ENTIRE message. "
            "Call this in the SAME TURN as solo_record when the message contains both daily events "
            "AND money flows. You may call MULTIPLE TIMES per turn for distinct transactions. "
            "For investment, only record the GAIN or LOSS result (e.g. '基金赚了300' → invest_gain 300), "
            "NOT buy/sell actions. For STABLE financial facts (monthly salary, mortgage rate), "
            "use solo_remember instead."
        ),
        [
            ("type", "string",
             "Transaction type. MUST be one of: "
             "expense (dining, transport, shopping, housing, etc.), "
             "income (salary, bonus, refund, gift received), "
             "transfer (moving money between own accounts), "
             "invest_gain (realized/unrealized investment profit, interest, dividend received), "
             "invest_loss (realized/unrealized investment loss).",
             True),
            ("category", "string",
             "Category. For expense PREFER: dining, groceries, transport, shopping, housing, "
             "health, education, entertainment, family, social. "
             "For income PREFER: salary, bonus, refund, gift, other_income. "
             "For invest_gain/loss PREFER: stocks, fund, bond, crypto, gold, savings, insurance. "
             "If none fit, use a single lowercase English word. No vague names like 'other'/'misc'.",
             True),
            ("amount", "number",
             "Exact amount in the original currency (positive number). Extract ONLY what the user "
             "stated. Do not split or estimate. e.g. 'AA花了120' → 120 (per person), not 240. "
             "For invest_loss, store the POSITIVE loss amount (e.g. '亏了500' → 500).",
             True),
            ("currency", "string", "ISO currency code (CNY, USD, HKD, EUR, ...). Default CNY. Not converted, just labeled.", False),
            ("date", "string", "YYYY-MM-DD. Defaults to today.", False),
            ("account", "string", "Optional note: payment method / account (支付宝, 微信, 招行卡, ...). Searchable but not separately aggregated.", False),
            ("counterparty", "string", "Counterparty person/company (e.g. 同事老王, 房东). Optional.", False),
            ("description", "string", "Detailed description. Put investment target info (e.g. '茅台') here.", False),
            ("tags", "string", "Comma-separated tags.", False),
            # rate/amount_cny 已移除（不做折算）；record_id 不暴露（编排层回填）
        ],
    )
```

**工具处理器**（校验 type / category / amount，写库，返回 `{"ok": True, "message": ...}`，不含 id）：

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

**category 校验辅助**（推荐集合 + 受约束新类别，同 health）：

```python
_EXPENSE_CATEGORIES = {"dining", "groceries", "transport", "shopping", "housing",
                       "health", "education", "entertainment", "family", "social"}
_INCOME_CATEGORIES = {"salary", "bonus", "refund", "gift", "other_income"}
_INVEST_CATEGORIES = {"stocks", "fund", "bond", "crypto", "gold", "savings", "insurance"}
_VAGUE_NAMES = {"other", "misc", "general", "unknown", "custom", "test"}

def _is_valid_finance_category(txn_type: str, category: str) -> bool:
    preferred = _EXPENSE_CATEGORIES | _INCOME_CATEGORIES | _INVEST_CATEGORIES
    if category in preferred:
        return True
    if category in _VAGUE_NAMES:
        return False
    return category.isalpha() and category.islower() and len(category) <= 20
```

**注册**：`SoloDomainTool(_tool_finance_transaction(), self._handle_finance_transaction)`

### 3.2 `solo_finance_budget` 工具

```python
def _tool_finance_budget() -> ToolDefinition:
    return _definition(
        "solo_finance_budget",
        (
            "Set or update a recurring spending budget. Use when the user sets a spending limit, "
            "e.g. '餐饮预算每月2000', '这个月尽量控制在5000以内'. "
            "If a budget for the same period+category already exists, update its amount rather than "
            "creating a duplicate. category='' means a total budget across all categories."
        ),
        [
            ("period", "string", "Budget period: monthly, weekly, yearly. Default monthly.", False),
            ("category", "string",
             "Spending category this budget limits (dining, transport, ...). "
             "Leave empty for a total budget across all categories.", False),
            ("amount", "number", "Budget amount (in the user's main currency, default CNY).", True),
            ("currency", "string", "ISO currency code. Default CNY.", False),
            ("name", "string", "Budget name (optional).", False),
            ("note", "string", "Note.", False),
        ],
    )
```

**处理器**（`(period, category)` 存在则 upsert 覆盖，否则新增）：

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
        id=uuid4().hex[:12], period=period, category=category, amount=amount,
        currency=str(arguments.get("currency") or "CNY").upper(),
        name=str(arguments.get("name") or ""), active=1,
        created_at=_now(), updated_at=_now(),
        note=str(arguments.get("note") or ""),
    )
    self.store.add_finance_budget(b)
    return {"ok": True, "message": f"预算已设置：{period}/{category or '全部'} {amount}"}
```

**注册**：`SoloDomainTool(_tool_finance_budget(), self._handle_finance_budget)`

### 3.3 `solo_finance_summary` 工具（辅助查询）

让 agent 能回答"这个月花了多少""最近基金赚了多少"等：

```python
def _tool_finance_summary() -> ToolDefinition:
    return _definition(
        "solo_finance_summary",
        (
            "Query structured finance transactions for a time range. "
            "Use when the user asks about spending, income, or investment gains/losses history. "
            "Returns aggregated statistics and recent transactions."
        ),
        [
            ("type", "string", "Filter by transaction type: expense, income, transfer, invest_gain, invest_loss.", False),
            ("category", "string", "Filter by category.", False),
            ("account", "string", "Filter by account note.", False),
            ("days", "integer", "Look back N days (default 30).", False),
        ],
    )
```

**处理器**：用 `list_finance_transactions(type=, category=, account=, date_from=)` 取数，聚合 total / by_category / 收入支出合计 / recent（同 health 的 `_handle_health_summary` 模式）。

### 3.4 post_turn_backfill（沿用 health 模式）

新增 `self._pending_finance_ids: list[str]`，在 `post_turn_backfill` 中（与 health 的 `_pending_health_ids` 并列）把 finance transaction 的 record_id link 到本轮最后一条 record：

```python
# 在 health backfill 之后
for fid in getattr(self, "_pending_finance_ids", []):
    if self._created_record_ids:
        self.store.update_finance_transaction(fid, record_id=list(self._created_record_ids)[-1])
self._pending_finance_ids.clear()
```

---

## 4. 提示词优化

### 4.1 工具路由决策表新增

在 `solo/prompts.py` 的决策表中（health 行之后）新增：

```
| 用户提到资金流动（消费、收入、转账、理财盈亏结果） | → solo_finance_transaction（同一轮与 solo_record 并行调用） |
| 用户设定消费预算（"餐饮每月2000"、"这个月控制在5000"） | → solo_finance_budget |
```

### 4.2 新增章节：财务记录提取原则

在 prompt 的"健康记录提取原则"之后新增平级章节：

```markdown
## 财务记录提取原则

用户的日常记录中经常包含**资金流动信息**——消费、收入、理财盈亏。
这些需要写入专用的财务数据库，以便统计和趋势分析。

### 判断标准：是否涉及金额的资金进出？
问自己：**这是一笔有金额的钱的进出吗？** 如果是，调用 `solo_finance_transaction`。
如果是**稳定的财务事实**（月薪、房贷利率），用 `solo_remember`。
如果是**设定消费预算**，用 `solo_finance_budget`。

### 两种财务记录的区别

| 信息类型 | 工具 | 示例 |
|---------|------|------|
| 资金进出（流水） | solo_finance_transaction | "午饭花了35"、"工资到账18000"、"基金赚了300" |
| 消费预算（限额） | solo_finance_budget | "餐饮每月预算2000"、"这个月控制在5000以内" |
| 稳定财务事实（长效） | solo_remember | "月薪到手1万8"、"房贷利率4.2%" |

### 金额提取规则（关键）

1. **只提取用户明确说的金额**，绝不估算、推断、拆分
   - "AA花了120" → 120（人均），不是 240
   - "大概花了100" → 100
   - "买了几杯咖啡"（没金额）→ 不调用
2. **区分单笔和总价**：用户说"3件衣服一共花了800" → amount=800（总价）
3. **币种识别**："$200" → currency=USD；"200块"/"200元" → currency=CNY；默认 CNY
4. **AA/分摊**：用户说"AA"时按用户实际支出金额记录，不替用户算对方那份

### 理财记录规则（重要）

- **只记盈亏结果**：用户说"基金赚了300" → `type=invest_gain, amount=300`
- 用户说"股票亏了500" → `type=invest_loss, amount=500`（存正数）
- **不记买卖动作**：用户说"买了100股茅台"但没说盈亏 → 不调用 finance 工具（属于持仓事件，本版本不追）
- 标的信息（茅台、某基金）写进 `description`，不要新建字段

### 隐含财务信息识别（逐句扫描）

| 用户说的（日常记录） | 隐含的财务信息 | 提取结果 |
|---------------------|---------------|----------|
| "今天和朋友吃火锅，AA花了120" | 消费120 | expense/dining/120, counterparty=朋友 |
| "打车去机场，花了80" | 消费80 | expense/transport/80 |
| "工资到账1.8w" | 收入18000 | income/salary/18000 |
| "基金赚了300" | 理财盈利 | invest_gain/fund/300 |
| "股票亏了500" | 理财亏损 | invest_loss/stocks/500 |
| "支付宝转了500到招行" | 转账 | transfer/500, account=支付宝, counterparty=招行 |

### 交易类型选择（type）

| 用户说的 | type |
|---------|------|
| 消费、买东西、花钱 | expense |
| 收到钱、到账、红包收入 | income |
| 自己账户间转钱 | transfer |
| 理财赚了、分红利息到账 | invest_gain |
| 理财亏了 | invest_loss |

### 操作要求

1. 每次 solo_record 时同步扫描是否含金额流动，有则**同一轮**调用 solo_finance_transaction
2. 一条消息含多笔交易（如"买咖啡25+打车15"）→ **分别调用多次**
3. **稳定事实用 solo_remember**：月薪、房贷月供、保险年费等固定值
4. **预算用 solo_finance_budget**：用户说"餐饮每月预算2000"是设定预算
5. 理财只记盈亏结果，不记买卖动作

### 不提取的情况
- 没有具体金额（"买了点菜"）
- 纯粹是计划/愿望（"想买辆新车"——未发生）
- 稳定的财务事实（应进 memory）
- 理财的买卖动作但未提盈亏（本版本不追持仓）
```

### 4.3 更新 `solo_record` 的 SIDE-EFFECT CHECK

在 `_tool_record()` 的 SIDE-EFFECT CHECK 中追加（保留 remember / health 部分）：

```
If this message contains money flows (spending, income, transfers, or an investment
GAIN/LOSS RESULT with specific amounts), also call solo_finance_transaction in the SAME
turn — once per distinct transaction. Extract ONLY the EXACT amount the user stated; do NOT
estimate or split. For investment, record only the gain/loss result (e.g. '基金赚了300'),
NOT buy/sell actions. If the user sets a spending budget, call solo_finance_budget.
```

---

## 5. Onboard Finance 页面设计

### 5.1 数据来源

全部来自两张结构化表，所有查询支持过滤（下推 SQL）：

| 页面区域 | 数据源 |
|----------|--------|
| 月度收支总览 | 按 type 聚合本月 expense / income / invest_gain / invest_loss |
| 月度收支趋势 | 按 `strftime('%Y-%m', date)` 分组，月度收入 vs 支出柱状 + 结余折线 |
| 消费类别排行 | `expense` 按 category 聚合 |
| 预算追踪 | `list_finance_budgets()` + 当前周期 expense 累加 |
| 流水时间线 | `list_finance_transactions(type=, category=, date_from=)` 分页 |

### 5.2 聚合口径

```
月度支出 = Σ(本月 type=expense 的 amount)        # 默认只算 currency=CNY
月度收入 = Σ(本月 type∈{income} 的 amount)
月度理财净盈亏 = Σ(invest_gain) − Σ(invest_loss)    # 同月
月度结余 = 月度收入 − 月度支出                      # 理财盈亏单列，不计入结余（避免和"本金进出"混）

预算消耗率 = 当前周期内某 category 的 expense 总额 / 该 category 预算 amount
```

> 外币记录在聚合时按 currency 分组各自统计，不折算成 CNY；前端展示带币种符号。绝大多数场景只有 CNY。

### 5.3 设计原则：视觉优先，一眼读懂

> **人是视觉动物。** 页面以**图表为主、文字为辅**。打开页面，用户应当在不读任何文字的情况下，立刻看到：本月花了多少、钱主要花在哪、是否超支、理财是赚是亏、最近消费的节奏。

落地约定（全部复用现有基础设施，零新增样式系统）：

- **主题**：沿用全局深色主题（`--color-bg #0b0c0f` / `--color-surface-1 #101114` / `border-border` / `text-text-muted`），与 Health / Dashboard 完全一致。
- **极光背景**：页面顶层渲染 `<SciFiBackground accent="#d4a574" />`（solo 主色），与 Dashboard 同款氛围，zIndex 分层。
- **调色板**：复用 `Charts.tsx` 的 `palette = ['#b8956a','#6a9e8e','#8b7db8','#c4a35a','#b87070','#6a8a9e','#7eb87e','#c48a6a']`；盈/亏用语义色：盈 `--color-success #34d399`、亏 `--color-danger #f87171`。
- **Tooltip**：复用 `tooltipStyle`（bg `#1c1c21`、border `#2e2e33`、mono 字体）。
- **图表库**：recharts `^2.15.0`。本页会用到 `ComposedChart`（柱+折线组合）、`PieChart`（donut）、`BarChart layout="vertical"`（横向排名）、`RadialBarChart`（预算环形进度）、`AreaChart`（理财盈亏趋势）。其中 ComposedChart/RadialBarChart 是 recharts 2.15 已内置但项目尚未用过的类型——属于安全引入，不增依赖。
- **统计卡**：复用 `<StatsCard>`（自带 count-up 数字滚动动画 + emoji icon + accent 色覆盖）。
- **空数据**：复用共享 `<EmptyState>`（带 icon/title/description），每个图表各自优雅降级，绝不出错崩白。
- **数字格式**：金额统一 `¥` + 千分位（`value.toLocaleString()`）；结余/盈亏为负时用 `--color-danger` 红色；外币原始金额带币种符号。

### 5.4 页面布局（6 个 Zone，图表密度优先）

```
┌──────────────────────────────────────────────────────────────────┐
│  💰 Finance                            [3M][6M][12M][all]  时间范围 │
│  个人消费追踪与预算  ·  截止 06/21                                   │  ← SciFiBackground 极光底
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Zone 1: 月度总览（4 × StatsCard，数字滚动动画）                     │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐    │
│  │ 💸 本月支出  │ │ 💰 本月收入  │ │ ◆ 本月结余  │ │ 📈 理财净盈亏│    │
│  │ ¥13,800    │ │ ¥18,000    │ │ +¥4,200    │ │ +¥1,200    │    │  ← 结余/盈亏负值变红
│  │ ↓ 环比上月  │ │ ↑ 环比上月  │ │            │ │ 3 笔       │    │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘    │
│                                                                  │
│  Zone 2: 月度收支趋势（ComposedChart 柱+折线，全宽）                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 收入 ▓▓ 收支柱  ── 结余折线（带渐变面积）                       │  │
│  │     18k ┤  ▓                                              │  │
│  │     12k ┤  ▓    ▓        ╱──── 结余                        │  │
│  │      0  ┤  ▓▓   ▓  ▓   ╱                                    │  │
│  │         3月   4月  5月 6月                                    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Zone 3: 双列                                                     │
│  ┌──────────────────────────┐ ┌──────────────────────────────┐ │
│  │ 消费类别构成（Donut）       │ │ 预算追踪（RadialBar 环形进度） │ │
│  │        ╭───╮              │ │    ○ 餐饮 90%   ○ 交通 50%    │ │
│  │  dining  │ ◔ │  32%        │ │    ○ 购物 40%   ○ 总预算 75%  │ │
│  │ transport│    │            │ │   环越大=消耗越多，超 80% 变橙 │ │
│  │  环心：¥13,800 总支出       │ │   超 100% 变红（danger）       │ │
│  └──────────────────────────┘ └──────────────────────────────┘ │
│                                                                  │
│  Zone 4: 双列                                                     │
│  ┌──────────────────────────┐ ┌──────────────────────────────┐ │
│  │ 消费类别排行（横向条形）     │ │ 理财盈亏趋势（AreaChart 渐变）  │ │
│  │ dining    ████████ ¥3,200 │ │   ▲                          │ │
│  │ transport █████    ¥1,500 │ │   █▄  盈亏                    │ │
│  │ shopping  ███      ¥1,200 │ │   ░░░▁▂▃▅▆█                  │ │
│  │  最长条=占比最大            │ │  0 基线，盈绿亏红              │ │
│  └──────────────────────────┘ └──────────────────────────────┘ │
│                                                                  │
│  Zone 5: 消费日历热力图（复用 ActivityHeatmap）                     │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  ‹  2025年6月  ›                  颜色深浅=当日支出强度        │  │
│  │  一 二 三 四 五 六 日                                       │  │
│  │  ● ● ●● ●●● ●●●  ← 每格圆点大小/深浅对应当日消费              │  │
│  │  一眼看出哪天/周末花钱多、有没有"报复性消费日"                 │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Zone 6: 流水时间线（可折叠，type/category 筛选 chip）               │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ ▾ 最近流水   [全部][💸支出][💰收入][📈理财]                  │  │
│  │  06/18  💸 dining   午餐               ¥35    ◔AA           │  │
│  │  06/18  📈 fund     基金浮盈           +¥300  绿色            │  │
│  │  06/17  💰 salary   工资               +¥18,000             │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**图表选型理由**（每个 Zone 都对应一种"一眼能看懂"的信息）：

| Zone | 图表 | 解决什么问题 | 复用/新增 |
|------|------|------------|----------|
| 1 | StatsCard ×4 | 总览四数字 | **复用** `StatsCard`（count-up 动画） |
| 2 | ComposedChart 柱+折线 | 收支对比 + 结余趋势 | recharts 内置类型，新增用法 |
| 3-左 | Donut（PieChart innerRadius） | 钱花在哪类——占比一目了然 | **复用** `Charts.tsx` EmotionPieChart 模式 |
| 3-右 | RadialBarChart | 每个预算消耗到几成——环形直观 | recharts 内置类型，新增用法 |
| 4-左 | BarChart layout="vertical" | 哪类花最多——横向排名 | **复用** `Charts.tsx` TagBarChart 模式 |
| 4-右 | AreaChart（带 gradient） | 理财是赚是亏、趋势 | **复用** Health Sleep/HR AreaChart 模式 |
| 5 | 日历热力图 | 哪天花钱多、消费节奏 | **复用** `Charts.tsx` ActivityHeatmap |
| 6 | 列表 + 筛选 chip | 明细追溯 | **复用** SubjectFilter chip 模式 |

> **6 个 Zone 里 4 个直接复用现成组件，2 个用 recharts 内置新图表类型。** 不引入任何新依赖、不新建样式系统。

### 5.5 后端 API 设计

挂在 `/api/solo/finance` 前缀（solo 命名空间隔离，wolo 不提供）。相比精简版初稿，为支撑图表新增 **日历** 与 **环比** 数据：

```
# ── 概览（Zone 1）───────────────────────────────────────────
GET  /api/solo/finance/overview
     → 本月支出/收入/结余/理财净盈亏 + 上月同口径（算环比箭头）+ 截止日期 + 类别合计

# ── 交易 ────────────────────────────────────────────────────
GET  /api/solo/finance/transactions?type=&category=&account=&date_from=&date_to=&limit=&offset=
     → 分页流水（所有过滤下推 SQL）
GET  /api/solo/finance/transactions/summary?type=expense&days=30
     → 按 category 聚合统计（用于类别排行、donut）
GET  /api/solo/finance/transactions/trend?days=180
     → 月度收入/支出/结余序列（按 strftime('%Y-%m', date) 分组）→ Zone 2 ComposedChart
GET  /api/solo/finance/transactions/daily?month=YYYY-MM
     → 某月每日支出合计（用于 Zone 5 消费日历热力图）

# ── 理财 ────────────────────────────────────────────────────
GET  /api/solo/finance/invest/trend?days=180
     → 月度 invest_gain/loss 累计净值序列 → Zone 4 右 AreaChart（0 基线，盈绿亏红）

# ── 预算（Zone 3 右）────────────────────────────────────────
GET  /api/solo/finance/budgets?active=true
     → 预算列表 + 各自当前周期消耗率（utilization 0~1，前端映射环形角度/颜色）

# ── 写操作（受限，见 §6.2）──────────────────────────────────
DELETE /api/solo/finance/transactions/{id}
PATCH  /api/solo/finance/transactions/{id}    # 禁改 type/amount（改类型/金额应删后重建）
DELETE /api/solo/finance/budgets/{id}
PATCH  /api/solo/finance/budgets/{id}         # 禁改 category/period
```

**预算环形着色规则**（前端，`utilization` 来自后端）：
- `utilization < 0.6` → `--color-success` 绿
- `0.6 ≤ utilization < 0.8` → `--color-warning` 黄
- `0.8 ≤ utilization < 1.0` → 橙（`#fb923c`）
- `utilization ≥ 1.0` → `--color-danger` 红（已超支）

> **删除的端点**（相比旧设计）：`net-worth-trend`、`holdings`、`holdings/allocation`、账户余额列表。

### 5.6 前端组件（8 个，含复用）

```
onboard/frontend/src/
├── pages/
│   └── Finance.tsx                            # 主页面：时间范围选择器贯穿，顶层 SciFiBackground
├── components/
│   └── finance/
│       ├── SpendingCards.tsx                  # Zone 1：4× StatsCard 封装（含环比箭头）
│       ├── CashflowTrend.tsx                  # Zone 2：ComposedChart 月度收支柱+结余折线
│       ├── CategoryDonut.tsx                  # Zone 3 左：消费类别 Donut（复用 EmotionPieChart 模式）
│       ├── BudgetRings.tsx                    # Zone 3 右：RadialBarChart 预算环形进度
│       ├── CategoryRanking.tsx                # Zone 4 左：横向条形排名（复用 TagBarChart 模式）
│       ├── InvestTrend.tsx                    # Zone 4 右：理财盈亏 AreaChart（盈绿亏红）
│       ├── SpendingHeatmap.tsx                # Zone 5：复用 ActivityHeatmap，展示每日消费强度
│       └── TransactionTimeline.tsx            # Zone 6：流水时间线 + type 筛选 chip
```

- **直接复用现成组件**：`SpendingCards`（包 `StatsCard`）、`CategoryDonut`（套 `EmotionPieChart` 写法）、`CategoryRanking`（套 `TagBarChart` 写法）、`SpendingHeatmap`（套 `ActivityHeatmap`，改数据源为每日支出）。
- **新增图表写法**：`CashflowTrend`（ComposedChart）、`BudgetRings`（RadialBarChart）、`InvestTrend`（AreaChart + 0 基线）。
- 图表全部包在统一的 `Section`（`p-5 rounded-lg border border-border bg-surface-1`）里，与 Health 视觉完全一致。
- 金额格式化统一 `¥` + 千分位；外币带币种符号；空数据用共享 `<EmptyState>`。
### 5.7 Solo-only 强制（wolo 隔离）

与 Health 完全一致：

1. **后端**：路由挂在 `/api/solo/finance/*`（solo 前缀），wolo 不请求这些路径。
2. **前端**：侧边栏 Finance 入口**仅在 `appName !== 'wolo'` 时渲染**（插入到 Health 之后）；`/finance` 路由在 wolo 模式下重定向回 Dashboard。
3. Token Gate 由全局中间件自动保护，无需额外鉴权。

---

## 6. 数据安全与隐私

### 6.1 原则

财务数据隐私要求等同 Health：

1. **仅本地存储** — 两张表在用户本地 SQLite，不上传任何远程服务器
2. **仅本地访问** — Onboard 仅监听 localhost
3. **无额外采集** — 所有数据来自用户主动发送的消息
4. **用户可控** — 用户可通过页面删除/修正任何记录（§6.2）

### 6.2 写操作

- **DELETE**：物理删除单条。
- **不可批量删/清空**：只支持按 `{id}` 单条删，防误操作。
- **PATCH 受限**：交易禁改 `type`/`amount`（身份字段），预算禁改 `category`/`period`；其余字段（description/account/counterparty/tags）可改。
- **前端二次确认**：删除前弹确认框。

---

## 7. 实现与演进注意事项

### 7.1 演进路线

#### v1.0（本方案）
- 日常消费/收入/转账/理财盈亏记录
- 月度预算设定与消耗追踪
- 收支趋势 + 类别排行 + 预算进度 + 流水时间线
- 聊天自动提取（写时结构化）

#### v1.1（被砍掉的能力回填，可选）
- **持仓快照 + 净值追踪**：新增 `finance_holdings` 表（schema v9），引入 latest_holdings 分组取最新、净值趋势、资产配置饼图。仅当用户有"看总资产"需求时再启用。
- **账单导入**：支付宝/微信/银行账单 CSV 导入到 `finance_transactions`（标记 `source='import'`）。
- **预算超支提醒**：某 category 消耗超 80% 时 gateway 推送。

#### v1.2
- **多币种折算**：引入 amount_cny/rate + 实时汇率 API（当外币消费变多时）。
- **理财买卖动作追踪**：扩展 type（investment_buy/sell），配合持仓表做盈亏分析。
- **消费预测**：基于历史趋势预测本月支出。

#### v2.0
- **财务日历**：账单到期、工资到账、分红派息等周期性事件日历视图。
- **家庭财务**：支持记录配偶/家庭共同账户（v1.0 是单人视角）。

### 7.2 性能与扩展性

- 所有统计查询走索引（date/type/category/record_id）。
- 月度聚合用 `strftime('%Y-%m', date)` 分组，走 date 索引。
- `finance_transactions` 高频消费可能数据量较大，列表查询必须有 limit/offset。

### 7.3 时间一致性

- `finance_transactions.date` 与 `solo_record` 同源（`_now()[:10]` 本地日期）。
- 月度/周度聚合边界用本地日期，与 records/health 模块现有统计口径一致。

### 7.4 与 Health 模块的重叠

- 用户"看病花了 300"：health 模块记一条 `category=medical` 事件，finance 模块记一条 `type=expense, category=health` 交易。**两者并存不冲突**——health 记事件本身，finance 记金额，两个视角互补。

### 7.5 兼容性

- Schema 迁移幂等（v8 CREATE TABLE IF NOT EXISTS），对已有库安全。
- `metrics_json` 解析失败通过 dataclass 的 `metrics` property 兜底为 `{}`。
- 自定义 category 在图表上 fallback 到默认 icon/color。

---

## 8. 设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 数据入库方式 | 后提取 vs 写时结构化 | 写时结构化 | 金额需精确，关键词匹配精度低；与 health 一致 |
| 存储位置 | 独立库 vs SoloStore 新表 | SoloStore 新表（2 张） | 复用现有连接/迁移；与 records 表可软关联 |
| 实体拆分 | 单表 vs 交易/预算两表 vs 三表（含持仓） | **两表分离** | 流水（transaction）与限额（budget）语义不同；持仓/净值非当前诉求，挪到 v1.1 |
| 工具模式 | 独立工具 vs solo_record 加字段 | 独立工具 ×3（transaction/budget/summary） | records 表无金额字段；关注点分离；可独立查询 |
| 录入方式 | 聊天提取 vs 页面手填 vs 两者 | **仅聊天自动提取** | 与 solo 使用习惯一致；页面专注可视化，无录入表单 |
| 理财记录粒度 | 盈亏两态 vs buy/sell/dividend 全套 | **盈亏两态（invest_gain/loss）** | 用户无持仓追踪需求，只关心"赚了/亏了"结果 |
| 投资标的详情 | 专用字段（symbol/qty/price）vs 塞 description | **塞 description/tags** | 不追持仓就无需结构化标的字段 |
| 多币种 | 折算 vs 仅标注 | **仅标注不折算** | 个人消费 99% CNY，折算引入汇率幻觉与维护成本；外币按 currency 分组聚合 |
| 账户维度 | 单独索引+聚合 vs 可选备注 | **可选备注**（可搜索不专门聚合） | 用户无"哪个渠道花得多"需求 |
| 类别体系 | 硬编码枚举 vs 推荐+约束 | 推荐 + 受约束新类别 | 与 health 一致，兼顾灵活与可控 |
| 调用时机 | 后处理 vs 同一轮伴随 | 同一轮伴随 | 与 solo_remember/health 一致，无额外 LLM 开销 |
| record_id 关联 | LLM 传 vs 系统回填 vs 不关联 | 系统 best-effort 回填（post_turn_backfill） | 同 health，避免"有去无回" |
| 适用范围 | solo+wolo vs 仅 solo | **仅 solo** | 个人财务属隐私，wolo 是工作日志 |
| 写操作 | 只读 vs 可删改 | 可删改（受限） | 用户需修正/删除；禁改身份字段 + 单条删 + 二次确认 |
| 工具返回格式 | 返回 id vs 仅 message | **仅 message** | 与 _handle_remember/health_record 一致 |
| 预算工具 | 独立工具 vs 合并进 transaction | **独立工具 solo_finance_budget** | 语义清晰，与交易解耦，支持 upsert |
