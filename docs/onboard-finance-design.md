# Onboard Finance Page -- 设计方案

> 为 Onboard 应用新增 **Finance（个人财务）** 模块（**仅适用于 solo**，不适用于 wolo），包含：结构化财务数据库、专用 agent 工具、提示词优化、以及 Onboard 可视化页面。覆盖**消费支出、收入、投资持仓、预算、账户余额**等经济活动。

---

## 1. 架构概述

### 1.1 为什么需要独立模块

现有 `solo_record` / records 表**没有任何金额字段**（无 amount / spending / cost / price / budget），无法承载量化财务数据。如果靠关键词从非结构化日志里"打捞"消费，精度低且无法做金额聚合。

因此财务模块采用与 Health 模块一致的 **写时结构化** 策略：用户在记录日常生活的同一轮中，agent 自动把消费/收入/投资等经济活动写入专用的结构化财务表。

### 1.2 三类核心实体

财务活动天然分成三类，对应三张表：

| 实体 | 表 | 语义 | 示例 |
|------|-----|------|------|
| **交易** | `finance_transactions` | 一次性、有时间戳的资金流动（收支/转账） | 午餐 ¥35、工资 ¥15000、买 100 股茅台 |
| **持仓快照** | `finance_holdings` | 某时刻持有的资产/负债的快照（投资、存款、贷款） | 沪深账户持仓 ¥230000、房贷余额 ¥1.2M |
| **预算** | `finance_budgets` | 周期性支出上限（月度/年度） | 餐饮月预算 ¥2000 |

**为什么交易和持仓要分开？**
- 交易记录的是**流量**（flow），适合做趋势/排行/预算消耗。
- 持仓记录的是**存量**（stock），适合做净值趋势/资产配置/负债追踪。
- 强行合并会导致语义混乱（一笔买入交易 vs 一个持仓状态）。这参考了 personal finance / 财务记账的标准范式（复式记账里也是流量表与资产负债表分离）。

**为什么不直接用交易累加得出持仓？**
- 实际生活中持仓值会因市价波动，用户更可能直接报"现在账户里有 23 万"，而非逐笔累加。
- 持仓快照是**用户主动对账**的结果，可信度高，作为净值基准。

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
            account="支付宝",
            merchant="海底捞",
            description="和朋友AA火锅",
            tags="朋友,火锅",
            date=今天
        )
          → finance_transactions 表

示例 2: 投资
用户消息: "今天买了100股茅台，成交价1680"
    │
    ▼
Solo Agent
    ├─① solo_record(date=今天, tags="投资,股票", ...)
    └─② solo_finance_transaction(
            type="investment_buy",       ← 投资买入：资金流出 + 证券增加
            category="stocks",
            amount=168000.00,            ← 1680 × 100
            currency="CNY",
            account="沪深A股账户",
            investment_symbol="600519",
            investment_name="贵州茅台",
            investment_quantity=100,
            investment_price=1680.00,
            description="买入100股茅台",
            date=今天
        )

示例 3: 持仓对账（用户主动报当前资产）
用户消息: "刚才查了一下，沪深账户市值23万，活期还有5万"
    │
    ▼
Solo Agent
    ├─① solo_record(date=今天, tags="对账,资产", ...)
    └─② solo_finance_holding(            ← 注意是 holding，不是 transaction
            type="investment",
            name="沪深A股账户",
            institution="华泰证券",
            value_cny=230000.00,
            currency="CNY",
            as_of_date=今天,
            description="市值23万",
            source="对账"
        )
      + solo_finance_holding(
            type="cash",
            name="活期存款",
            value_cny=50000.00,
            as_of_date=今天,
        )

示例 4: 长效财务事实（进 memory，不进 finance 表）
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
| **财务交易** | **`solo_finance_transaction`** | **`finance_transactions` 表** | 检测到一次性资金流动 |
| **财务持仓** | **`solo_finance_holding`** | **`finance_holdings` 表** | 检测到资产/负债存量对账 |
| 待办提取 | `solo_add_todo` | `todos` 表 | 检测到待办/计划 |

---

## 2. 结构化财务数据库

### 2.1 表设计：finance_transactions

```sql
CREATE TABLE IF NOT EXISTS finance_transactions (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL DEFAULT '',      -- 关联 records 表（best-effort，见 §2.7）
    date TEXT NOT NULL,                       -- YYYY-MM-DD（与 solo_record 同源，见 §7.4）
    type TEXT NOT NULL,                       -- income|expense|transfer|investment_buy|investment_sell|investment_dividend|investment_income
    category TEXT NOT NULL,                   -- 消费/收入类别，见下方体系
    amount REAL NOT NULL,                     -- 原始币种金额（正数）
    currency TEXT NOT NULL DEFAULT 'CNY',     -- ISO 货币代码
    amount_cny REAL NOT NULL DEFAULT 0,       -- 折算人民币（用 rate 计算；未知时等于 amount 且 currency=CNY）
    rate REAL NOT NULL DEFAULT 1.0,           -- 记录时 amount→CNY 的汇率（CNY 时为 1.0）
    account TEXT NOT NULL DEFAULT '',         -- 账户/支付方式：支付宝|微信|招行储蓄卡|沪深账户|...
    merchant TEXT NOT NULL DEFAULT '',        -- 商户/对方
    counterparty TEXT NOT NULL DEFAULT '',    -- 交易对方（人名/公司）
    description TEXT NOT NULL DEFAULT '',
    -- 投资专属字段（仅 type=investment_* 时有意义）
    investment_symbol TEXT NOT NULL DEFAULT '',  -- 证券代码 600519 / AAPL
    investment_name TEXT NOT NULL DEFAULT '',    -- 证券名称 贵州茅台 / Apple
    investment_quantity REAL NOT NULL DEFAULT 0, -- 数量（股/份）
    investment_price REAL NOT NULL DEFAULT 0,    -- 单价
    investment_fee REAL NOT NULL DEFAULT 0,      -- 手续费/佣金
    -- 元数据
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent',     -- agent|user|import
    metrics_json TEXT NOT NULL DEFAULT '{}',  -- 扩展字段
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_finance_txn_date ON finance_transactions(date);
CREATE INDEX IF NOT EXISTS idx_finance_txn_type ON finance_transactions(type);
CREATE INDEX IF NOT EXISTS idx_finance_txn_category ON finance_transactions(category);
CREATE INDEX IF NOT EXISTS idx_finance_txn_account ON finance_transactions(account);
CREATE INDEX IF NOT EXISTS idx_finance_txn_record_id ON finance_transactions(record_id);
CREATE INDEX IF NOT EXISTS idx_finance_txn_symbol ON finance_transactions(investment_symbol);
```

#### type（交易类型）

| type | 含义 | 对净值影响 |
|------|------|-----------|
| `income` | 收入（工资、奖金、退款、红包收入） | + |
| `expense` | 支出/消费（餐饮、交通、购物等） | − |
| `transfer` | 转账/账户间搬运（支付宝→银行卡） | 0（净值不变）） |
| `investment_buy` | 投资买入（资金流出，证券增加） | 0（资产形式转换） |
| `investment_sell` | 投资卖出（资金流入，证券减少） | 0（资产形式转换） |
| `investment_dividend` | 分红/派息（现金流入） | + |
| `investment_income` | 投资收益（利息、已实现盈亏） | + |

> **净值计算口径**（见 §5.2）：日常支出/收入直接影响净现金流；投资买卖是资产形式转换，不计入"收支"，但通过持仓快照反映净值；分红/收益计入收入。

#### category（类别体系）

同样采用 **推荐类别 + 受约束新类别**（与 Health 一致）：

**支出 expense 推荐类别：**

| category | 中文 | 典型 |
|----------|------|------|
| `dining` | 餐饮 | 午餐、外卖、聚餐、咖啡 |
| `groceries` | 生鲜日用 | 超市、菜市场、日用品 |
| `transport` | 交通出行 | 打车、地铁、加油、停车 |
| `shopping` | 购物 | 服饰、数码、家居 |
| `housing` | 居住 | 房租、物业、水电煤、宽带 |
| `health` | 医疗健康 | 看病、买药、体检（与 health 模块的事件可并存：一个是事件记录，一个是金额） |
| `education` | 教育学习 | 课程、书籍、培训 |
| `entertainment` | 娱乐 | 电影、游戏、演出、旅行 |
| `family` | 家庭育儿 | 子女教育、家庭开支 |
| `social` | 社交人情 | 礼金、请客、红包 |

**收入 income 推荐类别：**

| category | 中文 |
|----------|------|
| `salary` | 工资薪水 |
| `bonus` | 奖金提成 |
| `investment` | 投资收益（分红/利息） |
| `refund` | 退款报销 |
| `gift` | 礼金红包收入 |
| `other_income` | 其他收入 |

**投资 investment_* 类别（category 字段表示标的类型）：**

| category | 含义 |
|----------|------|
| `stocks` | 股票（含 A 股、港股、美股） |
| `fund` | 基金（公募/私募） |
| `bond` | 债券 |
| `crypto` | 加密货币 |
| `gold` | 黄金/贵金属 |
| `real_estate` | 房产/不动产 |
| `cash` | 现金/活期/定期存款 |
| `insurance` | 保险/理财型产品 |

**新类别约束**（同 Health）：单个英文小写单词、`isalpha()`、长度 ≤ 20、不在 `VAGUE_NAMES = {other, misc, general, unknown, custom, test}`（注：`other_income` 是推荐类别里的合法例外）。

### 2.2 表设计：finance_holdings（持仓快照）

```sql
CREATE TABLE IF NOT EXISTS finance_holdings (
    id TEXT PRIMARY KEY,
    as_of_date TEXT NOT NULL,                 -- 快照日期 YYYY-MM-DD
    type TEXT NOT NULL,                       -- investment|cash|debt|asset
    -- investment|cash|debt|asset
    --   investment: 股票/基金/债券/理财等投资
    --   cash: 活期/定期/余额宝等现金类
    --   debt: 房贷/车贷/信用卡欠款/花呗等负债（值为正数，表示欠款金额）
    --   asset: 房产/车辆等实物资产净值
    name TEXT NOT NULL,                       -- 账户/持仓名称
    institution TEXT NOT NULL DEFAULT '',     -- 机构：华泰证券|招商银行|蚂蚁财富|...
    category TEXT NOT NULL DEFAULT '',        -- 同 investment category 体系（stocks/fund/...）
    investment_symbol TEXT NOT NULL DEFAULT '',
    quantity REAL NOT NULL DEFAULT 0,         -- 持有数量
    cost_basis REAL NOT NULL DEFAULT 0,       -- 成本价/成本
    current_price REAL NOT NULL DEFAULT 0,    -- 当前单价（用于市价类持仓）
    value REAL NOT NULL DEFAULT 0,            -- 该持仓当前市值/余额（原币种）
    currency TEXT NOT NULL DEFAULT 'CNY',
    value_cny REAL NOT NULL DEFAULT 0,        -- 折算人民币
    rate REAL NOT NULL DEFAULT 1.0,
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent',     -- agent|user|import|reconcile
    linked_account TEXT NOT NULL DEFAULT '',  -- 关联的账户名（与 transactions.account 对应）
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_finance_hold_date ON finance_holdings(as_of_date);
CREATE INDEX IF NOT EXISTS idx_finance_hold_type ON finance_holdings(type);
CREATE INDEX IF NOT EXISTS idx_finance_hold_name ON finance_holdings(name);
CREATE INDEX IF NOT EXISTS idx_finance_hold_category ON finance_holdings(category);
CREATE INDEX IF NOT EXISTS idx_finance_hold_account ON finance_holdings(linked_account);
```

**快照语义（重要）：**
- 每次用户对账（"我查了下账户有 23 万"）会**新增一条**记录，而非更新旧记录。
- 同一账户/标的在不同日期有多条快照，构成**净值/市值趋势**。
- 最新净值 = 按 `(type, name, category)` 分组取 `as_of_date` 最大那条的价值求和（投资+现金+资产 − 负债）。
- **允许过期快照**：若某账户最近没对账，净值计算时用其最新一条快照并标注"截止 X 月 X 日"。

### 2.3 表设计：finance_budgets（预算）

```sql
CREATE TABLE IF NOT EXISTS finance_budgets (
    id TEXT PRIMARY KEY,
    period TEXT NOT NULL DEFAULT 'monthly',   -- monthly|weekly|yearly
    category TEXT NOT NULL,                   -- 该预算限定的消费类别（expense 类别），'' 表示总预算
    amount_cny REAL NOT NULL,                 -- 预算金额（CNY）
    name TEXT NOT NULL DEFAULT '',            -- 预算名称
    active INTEGER NOT NULL DEFAULT 1,        -- 1=生效中，0=已停用
    start_date TEXT NOT NULL DEFAULT '',      -- 生效起始日（可空=长期）
    end_date TEXT NOT NULL DEFAULT '',        -- 生效结束日（可空=长期）
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_finance_budget_period ON finance_budgets(period);
CREATE INDEX IF NOT EXISTS idx_finance_budget_category ON finance_budgets(category);
CREATE INDEX IF NOT EXISTS idx_finance_budget_active ON finance_budgets(active);
```

> 预算通常由用户**主动设置**（通过自然语言："餐饮预算每月 2000"），agent 提取后写入。预算消耗 = 当前周期内该 category 的 expense 交易金额累加。

### 2.4 Dataclass 模型

在 `solo/core/models.py` 新增三个 frozen dataclass（与 SoloHealthRecord 同风格，均含 `metrics` property、`from_json`/`to_dict`/`to_json`）：

```python
@dataclass(frozen=True)
class SoloFinanceTransaction:
    """One structured finance transaction (income/expense/investment)."""
    id: str
    record_id: str = ""
    date: str = ""
    type: str = ""              # income|expense|transfer|investment_buy|investment_sell|investment_dividend|investment_income
    category: str = ""
    amount: float = 0.0         # 原始币种
    currency: str = "CNY"
    amount_cny: float = 0.0     # 折算人民币
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

    # from_json / to_dict / to_json 同 HealthRecord


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
    value: float = 0.0          # 原币种
    currency: str = "CNY"
    value_cny: float = 0.0
    rate: float = 1.0
    description: str = ""
    tags: str = ""
    source: str = "agent"
    linked_account: str = ""
    created_at: str = ""
    updated_at: str = ""
    # metrics property + serialization 同上


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

### 2.5 Store 方法

在 `SoloStore` 中新增（**所有 list 方法的关键过滤字段都必须下推 SQL**，这是多账户/多类别统计性能的基础）：

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
def finance_transaction_accounts(self) -> dict[str, int]: ...

# ── Finance holdings ───────────────────────────────────────
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
    """按 (type, name, category) 分组取 as_of_date 最大的一条，用于净值计算。"""
def get_finance_holding(self, h_id: str) -> SoloFinanceHolding | None: ...
def update_finance_holding(self, h_id: str, **fields) -> bool: ...
def delete_finance_holding(self, h_id: str) -> bool: ...

# ── Finance budgets ────────────────────────────────────────
def add_finance_budget(self, b: SoloFinanceBudget) -> None: ...
def list_finance_budgets(self, *, active: bool | None = None, category: str | None = None) -> list[SoloFinanceBudget]: ...
def get_finance_budget(self, b_id: str) -> SoloFinanceBudget | None: ...
def update_finance_budget(self, b_id: str, **fields) -> bool: ...
def delete_finance_budget(self, b_id: str) -> bool: ...
```

> `latest_holdings()` 是净值计算的核心，需要在 SQL 层用窗口函数或 `GROUP BY ... MAX(as_of_date)` 实现（SQLite 支持），避免在 service 层拉全量再 Python 过滤。

### 2.6 Schema 迁移

将 `_SCHEMA_VERSION` 从 **7 提升到 8**（v7 已被 health 占用）。在 `_apply_migrations()` 中加幂等迁移（三张表 + 索引），写法与 health 的 v7 一致：

```python
# v8: finance tables（幂等 CREATE TABLE IF NOT EXISTS）
self._conn.executescript("""
    CREATE TABLE IF NOT EXISTS finance_transactions (...);
    CREATE INDEX IF NOT EXISTS idx_finance_txn_date ...;
    ... (三张表全部 DDL)
""")
self._conn.commit()
```

> **不要**用 `_table_exists()` 探测（项目无此方法，且 IF NOT EXISTS 已幂等）。

### 2.7 数据完整性与关联策略

#### record_id 关联（同 Health，best-effort）

- `solo_finance_transaction` / `solo_finance_holding` 参数列表**不含 record_id**（LLM 无法获知同轮 record id）。
- 由编排层在 `solo_record` 成功后 best-effort 回填；失败则 record_id 留空。
- 空时按 `date + account/description` 软关联到当天日志，不阻塞主流程。

#### 多币种归一化

- 所有金额统一存两份：`amount`（原始币种）+ `amount_cny`（折算人民币）+ `rate`（汇率）。
- agent 侧：若用户明确说了币种（"$200" → currency=USD），需给出 `rate`；**不确定汇率时 rate 留 0，由后端兜底**（见 §7.2 汇率兜底）。
- 后端聚合统一用 `amount_cny` 字段；若 `amount_cny == 0`（agent 没折算），后端用 `amount` 代替（假设 CNY）并在结果中标注"未折算"。
- 前端展示原始金额时带币种符号（`¥` / `$` / `€`），聚合金额统一显示 `¥`。

#### 负债符号约定

- `finance_holdings.value` 对 `debt` 类型**用正数存欠款金额**（房贷余额 1,200,000 而非 -1,200,000）。
- 净值计算时：`净值 = Σ(investment + cash + asset 的 value_cny) − Σ(debt 的 value_cny)`。
- 这样前端展示"负债 ¥1.2M"是直观的正数，而净值计算时自动减去。

#### 投资买卖与持仓的关系

- `investment_buy` / `investment_sell` 交易记录的是**动作**（流量）。
- 持仓快照 `finance_holdings` 记录的是**结果**（存量）。
- **不自动联动**：买入交易不会自动更新持仓表（避免双写不一致）。用户对账时手动报持仓，系统用快照覆盖。这是有意的设计取舍——持仓应以用户对账为准，而非靠交易累加（会有手续费、分红再投等复杂情况）。

---

## 3. Agent 工具设计

### 3.1 `solo_finance_transaction` 工具

#### 工具定义

```python
def _tool_finance_transaction() -> ToolDefinition:
    return _definition(
        "solo_finance_transaction",
        (
            "Record a STRUCTURED finance transaction into the dedicated finance database. "
            "Call this whenever the user's message contains a money flow: spending, income, "
            "transfer, or investment buy/sell. Extract the EXACT amount the user stated — do NOT "
            "estimate, infer, or split amounts the user did not specify. "
            "IMPORTANT: Finance info may appear INCIDENTALLY in a daily record "
            "(e.g. '和朋友吃饭花了120' → record expense 120). Scan the ENTIRE message. "
            "Call this in the SAME TURN as solo_record when the message contains both daily events "
            "AND money flows. You may call MULTIPLE TIMES per turn for distinct transactions. "
            "For STABLE financial facts (monthly salary, mortgage rate, recurring subscriptions), "
            "use solo_remember instead."
        ),
        [
            ("type", "string",
             "Transaction type. MUST be one of: "
             "income (salary, bonus, refund, gift received), "
             "expense (dining, transport, shopping, housing, etc.), "
             "transfer (moving money between own accounts, no net change), "
             "investment_buy (buying stocks/funds/bonds/crypto), "
             "investment_sell (selling securities), "
             "investment_dividend (cash dividend/interest received), "
             "investment_income (realized capital gain or interest income).",
             True),
            ("category", "string",
             "Category. For expense PREFER: dining, groceries, transport, shopping, housing, "
             "health, education, entertainment, family, social. "
             "For income PREFER: salary, bonus, investment, refund, gift, other_income. "
             "For investment_* PREFER: stocks, fund, bond, crypto, gold, real_estate, cash, insurance. "
             "If none fit, use a single lowercase English word. No vague names like 'other'/'misc'.",
             True),
            ("amount", "number",
             "Exact amount in the original currency (positive number). Extract ONLY what the user "
             "stated. Do not split or estimate. e.g. 'AA花了120' → 120 (per person), not 240.",
             True),
            ("currency", "string", "ISO currency code (CNY, USD, HKD, EUR, ...). Default CNY.", False),
            ("date", "string", "YYYY-MM-DD. Defaults to today.", False),
            ("account", "string",
             "Payment method / account: 支付宝, 微信, 招行储蓄卡, 沪深账户, 信用卡, ...", False),
            ("merchant", "string", "Merchant or payee name (e.g. 海底捞, 京东, 美团).", False),
            ("counterparty", "string", "Counterparty person/company (e.g. 同事老王, 房东).", False),
            ("description", "string", "Detailed description.", False),
            # ── 投资专属 ──
            ("investment_symbol", "string", "Security code (e.g. 600519, AAPL, 00700).", False),
            ("investment_name", "string", "Security name (e.g. 贵州茅台, Apple).", False),
            ("investment_quantity", "number", "Quantity (shares/units).", False),
            ("investment_price", "number", "Unit price.", False),
            ("investment_fee", "number", "Commission/fee.", False),
            ("tags", "string", "Comma-separated tags.", False),
            # rate 不让 LLM 强行填（见 §7.2 汇率兜底）；record_id 不暴露（见 §2.7）
        ],
    )
```

#### 工具处理器

```python
async def _handle_finance_transaction(self, arguments: dict[str, Any]) -> dict[str, Any]:
    txn_type = _required_text(arguments, "type")
    category = _required_text(arguments, "category")
    amount = float(arguments.get("amount") or 0)
    if amount <= 0:
        return {"ok": False, "error": f"amount must be positive, got {amount}"}

    # category 校验（推荐集合 + 受约束新类别），同 health
    if not _is_valid_finance_category(txn_type, category):
        return {"ok": False, "error": f"Invalid category '{category}' for type '{txn_type}'."}

    currency = str(arguments.get("currency") or "CNY").upper()
    # 汇率兜底：LLM 不传 rate，由后端按 currency 折算（见 §7.2）
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

#### 返回契约

成功 `{"ok": True, "message": "..."}`，失败 `{"ok": False, "error": "..."}`。不返回 id（与项目其他工具一致）。

#### 注册

```python
SoloDomainTool(_tool_finance_transaction(), self._handle_finance_transaction),
```

### 3.2 `solo_finance_holding` 工具

```python
def _tool_finance_holding() -> ToolDefinition:
    return _definition(
        "solo_finance_holding",
        (
            "Record a SNAPSHOT of an asset/debt/investment position (a stock of value, NOT a flow). "
            "Use this when the user reports their current account balance, portfolio value, or debt "
            "after checking (对账), e.g. '账户里有23万', '房贷还剩120万'. "
            "Each call adds a NEW dated snapshot (does not update old ones) so trends can be tracked. "
            "This is DIFFERENT from solo_finance_transaction which records money flows. "
            "For debt (mortgage, credit card balance), store the POSITIVE owed amount."
        ),
        [
            ("type", "string",
             "Holding type: investment (stocks/funds/bonds/crypto), cash (deposits/余额宝), "
             "debt (mortgage/credit card balance/花呗), asset (house/car net value).",
             True),
            ("name", "string", "Account/holding name (e.g. 沪深A股账户, 招行储蓄卡, 房贷).", True),
            ("value", "number",
             "Current value/balance in original currency. POSITIVE for all types including debt "
             "(store the owed amount as a positive number).", True),
            ("as_of_date", "string", "Snapshot date YYYY-MM-DD. Defaults to today.", False),
            ("currency", "string", "ISO currency code. Default CNY.", False),
            ("institution", "string", "Institution (华泰证券, 招商银行, ...).", False),
            ("category", "string", "Investment category: stocks, fund, bond, crypto, gold, real_estate, cash, insurance.", False),
            ("investment_symbol", "string", "Security code (for single-security holdings).", False),
            ("quantity", "number", "Quantity held.", False),
            ("cost_basis", "number", "Cost basis / total cost.", False),
            ("current_price", "number", "Current unit price (for market-price holdings).", False),
            ("linked_account", "string", "Linked account name (corresponds to transaction.account).", False),
            ("description", "string", "Description.", False),
            ("tags", "string", "Comma-separated tags.", False),
        ],
    )
```

> handler 同模式：校验 type、value>0、currency 兜底，写入 store，返回 `{"ok": True, "message": ...}`。

### 3.3 `solo_finance_budget` 工具

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
            ("amount_cny", "number", "Budget amount in CNY.", True),
            ("name", "string", "Budget name (optional).", False),
            ("note", "string", "Note.", False),
        ],
    )
```

> handler：若 `(period, category)` 已存在则 `update_finance_budget` 覆盖 amount，否则新增。返回 `{"ok": True, "message": ...}`。

### 3.4 辅助查询工具 `solo_finance_summary`

让 agent 能回答"这个月花了多少""最近买股票记录"等。**必须支持 category / account / date 过滤**：

```python
def _tool_finance_summary() -> ToolDefinition:
    return _definition(
        "solo_finance_summary",
        (
            "Query structured finance transactions and holdings for a time range. "
            "Use when the user asks about spending, income, investment history, account balances, "
            "or net worth. Returns aggregated statistics and recent transactions."
        ),
        [
            ("type", "string", "Filter by transaction type: income, expense, investment_buy, ...", False),
            ("category", "string", "Filter by category.", False),
            ("account", "string", "Filter by account.", False),
            ("days", "integer", "Look back N days (default 30).", False),
        ],
    )
```

> handler：用 `list_finance_transactions(type=, category=, account=, date_from=)` 取数，聚合出 total / by_category / by_account / recent，返回给 LLM。

---

## 4. 提示词优化

### 4.1 工具路由决策表新增

```
| 用户提到资金流动（消费、收入、转账、投资买卖） | → solo_finance_transaction（同一轮与 solo_record 并行调用） |
| 用户对账/报告当前账户余额、持仓市值、负债 | → solo_finance_holding（快照） |
| 用户设定消费预算 | → solo_finance_budget |
```

### 4.2 新增章节：财务记录提取原则

在 prompt 的"健康记录提取原则"之后新增平级章节：

```markdown
## 财务记录提取原则

用户的日常记录中经常包含**资金流动信息**——消费、收入、转账、投资等。
这些需要写入专用的财务数据库，以便统计和趋势分析。

### 判断标准：是否涉及金额的资金流动？
问自己：**这是一笔有金额的钱的进出吗？** 如果是，调用 `solo_finance_transaction`。
如果是**当前资产/负债的存量状态**（对账），调用 `solo_finance_holding`。
如果是**稳定的财务事实**（月薪、房贷利率），用 `solo_remember`。

### 三种财务记录的区别（重要）

| 信息类型 | 工具 | 示例 |
|---------|------|------|
| 资金流动（流量） | solo_finance_transaction | "午饭花了35"、"工资到账18000"、"买了100股茅台" |
| 资产/负债快照（存量） | solo_finance_holding | "查了下账户有23万"、"房贷还剩120万" |
| 稳定财务事实（长效） | solo_remember | "月薪到手1万8"、"房贷利率4.2%" |

### 金额提取规则（关键）

1. **只提取用户明确说的金额**，绝不估算、推断、拆分
   - "AA花了120" → 120（人均），不是 240
   - "大概花了100" → 100
   - "买了几杯咖啡"（没金额）→ 不调用，或调用 solo_clarify
2. **区分单笔和总价**：用户说"3件衣服一共花了800" → amount=800（总价）
3. **币种识别**："$200" → currency=USD；"200块"/"200元" → currency=CNY；默认 CNY
4. **AA/分摊**：用户说"AA"时按用户实际支出金额记录，不替用户算对方那份

### 隐含财务信息识别

用户的日常记录中经常顺嘴提到消费，需逐句扫描：

| 用户说的（日常记录） | 隐含的财务信息 | 提取结果 |
|---------------------|---------------|----------|
| "今天和朋友吃火锅，AA花了120" | 消费120 | type=expense, category=dining, amount=120, counterparty=朋友 |
| "打车去机场，花了80" | 消费80 | type=expense, category=transport, amount=80 |
| "工资到账了" | 收入（但无金额） | 无金额→solo_clarify 或跳过；若有金额如"工资到账1.8w"→amount=18000 |
| "充了200话费" | 消费200 | type=expense, category=...（话费归 housing 或自定义 telecom） |
| "买茅台赚了3000" | 投资收益 | type=investment_income, category=stocks, amount=3000 |
| "支付宝转了500到招行" | 转账 | type=transfer, amount=500, account=支付宝, counterparty=招行 |

### 交易类型选择（type）

| 用户说的 | type |
|---------|------|
| 消费、买东西、花钱 | expense |
| 收到钱、到账、红包收入 | income |
| 自己账户间转钱 | transfer |
| 买股票/基金/理财 | investment_buy |
| 卖股票/基金/理财 | investment_sell |
| 分红、利息到账 | investment_dividend |
| 卖出赚的钱（已实现收益） | investment_income |

### 消费类别（category）选择

支出优先：dining(餐饮) / groceries(生鲜日用) / transport(交通) / shopping(购物) /
housing(居住:房租水电物业宽带) / health(医疗) / education(教育) /
entertainment(娱乐) / family(家庭育儿) / social(社交人情)

收入优先：salary / bonus / investment / refund / gift / other_income

投资类：stocks / fund / bond / crypto / gold / real_estate / cash / insurance

### 操作要求

1. 每次 solo_record 时同步扫描是否含金额流动，有则**同一轮**调用 solo_finance_transaction
2. 一条消息含多笔交易（如"买咖啡25+打车15"）→ **分别调用多次**
3. **稳定事实用 solo_remember**：月薪、房贷月供、保险年费等固定值属于长效事实
4. **对账用 solo_finance_holding**：用户说"现在账户有X"是快照不是流量
5. **预算用 solo_finance_budget**：用户说"餐饮每月预算2000"是设定预算

### 不提取的情况
- 没有具体金额（"买了点菜"）
- 纯粹是计划/愿望（"想买辆新车"——未发生）
- 稳定的财务事实（应进 memory）
- 无法确定是消费还是转账的模糊表述（→ solo_clarify）
```

### 4.3 更新 `solo_record` 工具描述的 SIDE-EFFECT CHECK

在 `_tool_record()` 的 `SIDE-EFFECT CHECK` 中追加（保留原有的 remember / health 部分）：

```
If this message contains money flows (spending, income, transfers, investment buy/sell with
specific amounts), also call solo_finance_transaction in the SAME turn — once per distinct
transaction. Extract ONLY the EXACT amount the user stated; do NOT estimate or split. If the user
reports a current account balance / portfolio value / debt after checking (对账), call
solo_finance_holding instead. If the user sets a spending budget, call solo_finance_budget.
```

---

## 5. Onboard Finance 页面设计

### 5.1 数据来源

全部来自三张结构化表，所有查询支持 category/account/date 过滤（下推 SQL）：

| 页面区域 | 数据源 |
|----------|--------|
| 净值概览 | `latest_holdings()` 求和 |
| 收支流水 | `list_finance_transactions(type=, category=, account=, date_from=)` |
| 月度/周度收支对比 | 按 type 聚合 expense vs income |
| 消费类别排行 | `expense` 按 category 聚合 |
| 预算追踪 | `list_finance_budgets()` + 当前周期 expense 累加 |
| 投资持仓 | `latest_holdings()` 的 investment 类型 |
| 资产配置饼图 | `latest_holdings()` 按 type/category 占比 |
| 净值趋势 | `list_finance_holdings()` 历史快照按日期聚合 |
| 账户余额 | `latest_holdings()` 按 linked_account 分组 |

### 5.2 净值计算口径

```
当前净值 = Σ(latest_holdings 中 type=investment 的 value_cny)
         + Σ(latest_holdings 中 type=cash 的 value_cny)
         + Σ(latest_holdings 中 type=asset 的 value_cny)
         − Σ(latest_holdings 中 type=debt 的 value_cny)

当前净资产 = 当前净值

月度结余 = 本月 income 总额 − 本月 expense 总额
        （投资买卖/转账不计入结余，因为不改变净值）

预算消耗率 = 当前周期内某 category 的 expense 总额 / 该 category 预算 amount_cny
```

### 5.3 页面布局

```
┌─────────────────────────────────────────────────────────────┐
│  Finance                                                    │
│  个人财务统计与趋势                                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Zone 1: 净值概览（4 cards）                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ 当前净值   │ │ 本月结余   │ │ 本月支出   │ │ 本月收入   │      │
│  │ ¥186,500 │ │ +¥4,200  │ │ ¥13,800  │ │ ¥18,000  │      │
│  │ (截止6/15) │ │          │ │          │ │          │      │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
│                                                             │
│  Zone 2: 月度收支趋势（柱状+折线组合图）                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  柱：每月 expense / income     折线：月度结余          │   │
│  │  时间范围: [3M | 6M | 12M | all]                      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Zone 3: 双列布局                                            │
│  ┌──────────────────────┐ ┌──────────────────────┐        │
│  │  消费类别排行          │ │  预算追踪              │        │
│  │  (Category Ranking)   │ │  (Budget Tracking)    │        │
│  │  水平条形图：按类别支出 │ │  进度条：各预算消耗率   │        │
│  │  dining    ¥3,200 ▓▓▓ │ │  餐饮 ¥1800/¥2000 90%│        │
│  │  transport ¥1,500 ▓▓  │ │  交通 ¥500/¥1000 50%│        │
│  │  shopping  ¥1,200 ▓   │ │  总预算 ¥9000/¥12000 │        │
│  └──────────────────────┘ └──────────────────────┘        │
│                                                             │
│  Zone 4: 资产配置（饼图）                                     │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  持仓饼图：投资/现金/资产/负债 占比                       │   │
│  │  + 投资内部细分：股票/基金/债券/...                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Zone 5: 双列布局                                            │
│  ┌──────────────────────┐ ┌──────────────────────┐        │
│  │  投资持仓              │ │  净值趋势              │        │
│  │  (Holdings)           │ │  (Net Worth Trend)    │        │
│  │  表格：标的/数量/市值/  │ │  折线图：历史净值       │        │
│  │  成本/盈亏             │ │  (基于 holdings 快照) │        │
│  └──────────────────────┘ └──────────────────────┘        │
│                                                             │
│  Zone 6: 账户余额列表                                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  招行储蓄卡   ¥50,000   (截止 6/15)                  │   │
│  │  支付宝余额   ¥12,300   (截止 6/10)                  │   │
│  │  沪深A股账户  ¥230,000  (截止 6/15)                  │   │
│  │  房贷        −¥1,200,000 (截止 6/01)  ← 负债红色     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Zone 7: 流水时间线                                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  2025-06-18 💸 expense/dining 午餐 ¥35               │   │
│  │  2025-06-18 📈 investment_buy/stocks 茅台×100 ¥168k │   │
│  │  2025-06-17 💰 income/salary 工资 ¥18,000           │   │
│  │  2025-06-17 💸 expense/transport 打车 ¥80            │   │
│  │  ...                                               │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 5.4 后端 API 设计

所有端点挂在 `/api/solo/finance` 前缀（solo 命名空间隔离，wolo 不提供，见 §6）：

```
# ── 概览 ────────────────────────────────────────────────────
GET  /api/solo/finance/overview
     → 当前净值 + 本月收支结余 + 各类资产/负债汇总 + 截止日期标注

GET  /api/solo/finance/net-worth-trend?days=365
     → 历史净值趋势（基于 holdings 快照按日期聚合）
     → 返回 [{date, net_worth, assets, debts}]

# ── 交易 ────────────────────────────────────────────────────
GET  /api/solo/finance/transactions?type=&category=&account=&date_from=&date_to=&limit=&offset=
     → 分页流水（所有过滤下推 SQL）
GET  /api/solo/finance/transactions/summary?type=expense&days=30
     → 按 category/account 聚合统计（用于类别排行、账户分析）

# ── 持仓 ────────────────────────────────────────────────────
GET  /api/solo/finance/holdings?as_of_date=
     → 当前持仓列表（latest_holdings）
GET  /api/solo/finance/holdings/allocation
     → 资产配置（按 type/category 占比）

# ── 预算 ────────────────────────────────────────────────────
GET  /api/solo/finance/budgets?active=true
     → 预算列表 + 各自当前周期消耗率（关联 transactions 聚合）

# ── 写操作（受限，见 §6.2）──────────────────────────────────
DELETE /api/solo/finance/transactions/{id}
PATCH  /api/solo/finance/transactions/{id}    # 禁改 type/amount（改类型/金额应删后重建）
DELETE /api/solo/finance/holdings/{id}
DELETE /api/solo/finance/budgets/{id}
PATCH  /api/solo/finance/budgets/{id}         # 禁改 category/period（换类别应删后重建）
```

> **PATCH 限制**：交易和预算的"身份字段"（type/category/period）不可改，因为这些字段决定数据归属；修正消费应改 description/merchant/account，改类别/类型应删除重建。与 health 的 PATCH 禁改 subject 同理。

### 5.5 前端组件

```
onboard/frontend/src/
├── pages/
│   └── Finance.tsx                            # 主页面
├── components/
│   └── finance/
│       ├── NetWorthCards.tsx                  # 净值概览卡片
│       ├── CashflowTrend.tsx                  # 月度收支趋势
│       ├── CategoryRanking.tsx                # 消费类别排行
│       ├── BudgetTracking.tsx                 # 预算追踪
│       ├── AllocationPie.tsx                  # 资产配置饼图
│       ├── HoldingsTable.tsx                  # 投资持仓表
│       ├── NetWorthTrend.tsx                  # 净值趋势折线
│       ├── AccountBalances.tsx                # 账户余额列表
│       └── TransactionTimeline.tsx            # 流水时间线
```

> Finance 页面**不需要 SubjectFilter**（财务都是用户本人的，不像 health 有家庭成员多主体）。但需要 **时间范围选择器**（3M/6M/12M/all）和 **类别/账户筛选器**（可选，用于流水明细）。

### 5.6 Solo-only 强制（wolo 隔离）

与 Health 完全一致（§5.5 of health design）：

1. **后端**：路由挂在 `/api/solo/finance/*`（solo 前缀），wolo 不请求这些路径。
2. **前端**：侧边栏 Finance 入口**仅在 `appName === 'solo'` 时渲染**；`/finance` 路由在 wolo 模式下重定向回 Dashboard。
3. Token Gate 由全局 `TokenGateMiddleware` 自动保护，无需额外鉴权。

---

## 6. 数据安全与隐私

### 6.1 原则

财务数据比健康数据更敏感，隐私要求同等：

1. **仅本地存储** — 三张表在用户本地 SQLite，不上传任何远程服务器
2. **仅本地访问** — Onboard 仅监听 localhost
3. **无额外采集** — 所有数据来自用户主动发送的消息
4. **用户可控** — 用户可通过页面删除/修正任何记录（§6.2）

### 6.2 写操作的隐私权衡

- **DELETE**：物理删除单条（本地库数据量可控）。
- **不可批量删/清空**：只支持按 `{id}` 单条删，防误操作。
- **PATCH 受限**：交易禁改 `type`/`amount`（身份字段），预算禁改 `category`/`period`；其余字段（description/merchant/account/tags）可改。
- **前端二次确认**：删除前弹确认框。
- **导入型数据**（`source='import'`）：未来若支持银行账单/券商流水导入，导入的记录应标记 source，前端可按 source 过滤/批量删除。

---

## 7. 实现与演进注意事项

### 7.1 后续演进路线

#### v1.1
- **账单导入**：支付宝/微信/银行账单 CSV 导入到 `finance_transactions`
- **持仓自动刷新**：对接行情 API（A股/美股）自动更新 `current_price`（仅更新快照，不改历史）
- **周期性预算消耗提醒**：某 category 预算消耗超 80% 时 gateway 推送提醒
- **资产负债表视图**：标准化的资产=负债+净资产报表

#### v1.2
- **复式记账（可选）**：每笔交易记借贷两方，支持严格的账户余额核对
- **投资盈亏分析**：基于交易历史计算已实现/未实现盈亏、年化收益率
- **消费预测**：基于历史消费趋势预测本月支出
- **多币种自动汇率**：定时拉取汇率表，自动填充 amount_cny

#### v2.0
- **财务日历**：账单到期、工资到账、分红派息等周期性事件的日历视图
- **家庭财务**：类似 health 的 subject 字段，支持记录配偶/家庭共同账户（v1.0 是单人视角）
- **税务**：收入税务统计、投资收益税务计算（按地区）

### 7.2 汇率兜底策略

- **LLM 侧不强行填 rate**：`solo_finance_transaction` 参数不含 `rate`（避免 LLM 幻觉汇率）。LLM 只需报 `currency` 和 `amount`。
- **后端 `_resolve_currency(currency, amount, rate=None)`**：
  - CNY → `rate=1.0, amount_cny=amount`
  - 非 CNY 且 LLM 传了 rate → 直接用
  - 非 CNY 且无 rate → v1.0 用内置粗略汇率表（常币种 USD≈7.2, HKD≈0.92, EUR≈7.8，硬编码 + 注释"近似值，后续接 API"），`amount_cny = amount × rate`，并在结果标注 `fx_estimated=True`
  - v1.2 演进为接实时汇率 API
- **前端展示**：原始金额带币种符号，聚合金额统一 ¥；若 `fx_estimated` 则标注"*估算"。

### 7.3 性能与扩展性

- 所有统计查询走索引（date/type/category/account/symbol）。
- `latest_holdings()` 必须在 SQL 层用 `GROUP BY (type,name,category) + MAX(as_of_date)` 实现，不能在 service 层拉全量。
- 月度聚合用 `strftime('%Y-%m', date)` 分组，走 date 索引。
- `finance_transactions` 数据量可能较大（高频消费），列表查询必须有 limit/offset；v1.0 的 offset 在 service 层切片可接受（单用户本地库），v1.1 演进为 SQL OFFSET。

### 7.4 时间一致性

- `finance_transactions.date` 与 `solo_record` 同源（`_now()[:10]` 本地日期）。
- `finance_holdings.as_of_date` 用用户报的对账日期（LLM 传 as_of_date，默认今天）。
- 月度/周度聚合边界用本地日期，与 records/health 模块现有统计口径一致。

### 7.5 与 Health 模块的消费/医疗重叠

- 用户"看病花了 300"：health 模块记一条 `category=medical` 事件，finance 模块记一条 `type=expense, category=health` 交易。**两者并存不冲突**——health 记的是事件本身，finance 记的是金额。这是有意的设计，两个视角互补。
- prompt 中无需特殊处理，两个工具各自按职责提取。

### 7.6 兼容性

- Schema 迁移幂等（v8 CREATE TABLE IF NOT EXISTS），对已有库安全。
- `metrics_json` 解析失败通过 dataclass 的 `metrics` property 兜底为 `{}`。
- 自定义 category 在图表上 fallback 到默认 icon/color。

---

## 8. 设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 数据入库方式 | 后提取 vs 写时结构化 | 写时结构化 | 金额需精确，关键词匹配精度低；与 health 模式一致 |
| 存储位置 | 独立库 vs SoloStore 新表 | SoloStore 新表（3 张） | 复用现有连接/迁移；与 records 表可软关联（date+account） |
| 实体拆分 | 单表 vs 交易/持仓/预算三表 | **三表分离** | 流量（transaction）与存量（holding）语义不同，合并会导致净值/收支计算混乱 |
| 持仓是否自动联动交易 | 自动更新 vs 快照独立 | **快照独立（手动对账）** | 自动联动有手续费/分红再投等复杂情况，易双写不一致；持仓应以用户对账为准 |
| 工具模式 | 独立工具 vs solo_record 加字段 | 独立工具 ×4 | records 表无金额字段；关注点分离；可独立查询 |
| 调用时机 | 后处理 vs 同一轮伴随 | 同一轮伴随 | 与 solo_remember/health 模式一致，无额外 LLM 开销 |
| 类别体系 | 硬编码枚举 vs 推荐+约束 | 推荐 + 受约束新类别 | 与 health 一致，兼顾灵活与可控 |
| 多币种 | 单币种 vs 多币种折算 | 多币种（amount + amount_cny） | 用户可能有外币消费/港股美股，需统一折算才能聚合 |
| 汇率来源 | LLM 填 vs 后端兜底 | **后端兜底** | LLM 易幻觉汇率；后端用近似表 + 标注估算 |
| 负债符号 | 负数 vs 正数（净值时减） | **正数（净值计算时减）** | 前端展示"负债 ¥1.2M"直观；净值公式统一 |
| 投资买卖是否计入收支结余 | 计入 vs 不计入 | **不计入** | 买卖是资产形式转换，不改净值；只有分红/收益计入收入 |
| record_id 关联 | LLM 传 vs 系统回填 vs 不关联 | 系统 best-effort 回填 | 同 health，避免"有去无回" |
| 适用范围 | solo+wolo vs 仅 solo | **仅 solo** | 个人财务属隐私，wolo 是工作日志 |
| 写操作 | 只读 vs 可删改 | 可删改（受限） | 用户需修正/删除；禁改身份字段（type/amount/category/period）+ 单条删 + 二次确认 |
| 工具返回格式 | 返回 id vs 仅 message | **仅 message** | 与 _handle_remember/health_record 一致 |
| 是否支持多主体（家庭） | v1.0 支持 vs 演进 | **v1.0 单人，v2.0 演进** | 财务共享账户逻辑复杂（共同账户归属），v1.0 先做单人视角 |
| 净值计算 | 交易累加 vs 快照求和 | **快照求和（latest_holdings）** | 交易累加有遗漏/重复风险；快照是用户对账结果，可信度高 |
