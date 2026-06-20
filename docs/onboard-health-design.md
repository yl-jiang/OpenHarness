# Onboard Health Page -- 设计方案

> 为 Onboard 应用新增 **Health（身心健康）** 模块（**仅适用于 solo**，不适用于 wolo），包含：结构化健康数据库、专用 agent 工具、提示词优化、以及 Onboard 可视化页面。

---

## 1. 架构概述

### 1.1 核心理念：结构化入库，而非后提取

旧方案依赖关键词匹配从已有 Records/Memory 中"打捞"健康数据，存在两个问题：
1. **数据质量不可控** -- 非结构化文本匹配精度低，漏检多
2. **滞后性** -- 只能在数据入库后才能提取，无法实时聚合

新方案采用 **写时结构化** 策略：当用户发送包含健康信息的消息时，agent 在调用 `solo_record` 记录日常日志的**同一轮**中，同时调用 `solo_health_record` 将健康信息写入专用的结构化数据库表。这与 `solo_remember` 的"伴随写入"模式完全一致。

### 1.2 数据流全貌

```
示例 1: 用户自己的健康事件
用户消息: "今天跑了5km，膝盖有点疼，吃了颗布洛芬"
    │
    ▼
Solo Agent (LLM)
    │
    ├─① solo_record(date=今天, tags="运动,健身", emotion="积极", ...)
    │     → 写入 records 表（日常日志）
    │
    ├─② solo_health_record(
    │       subject="self",
    │       category="fitness",
    │       item="跑步 5km",
    │       body_part="膝盖",
    │       severity="mild",
    │       date=今天
    │   )
    │     → 写入 health_records 表
    │
    └─③ solo_health_record(
            subject="self",
            category="medication",
            item="布洛芬",
            dosage="1颗",
            date=今天
        )
          → 写入 health_records 表

示例 2: 家庭成员的健康事件
用户消息: "今天带图图去新华医院做发育评估，Gesell结果正常"
    │
    ▼
Solo Agent (LLM)
    │
    ├─① solo_record(date=今天, tags="家庭,图图,健康,医院", ...)
    │     → 写入 records 表（日常日志）
    │
    └─② solo_health_record(
            subject="图图",          ← 关键：识别出是孩子的健康事件
            category="medical",
            item="Gesell发育评估",
            description="评估结果：正常",
            status="resolved",
            date=今天
        )
          → 写入 health_records 表

示例 3: 日常记录中隐含的健康信息（顺嘴提到）
用户消息: "今天带图图去游乐场玩了，他很开心，明月没有去，因为她去体检了"
    │
    ▼
Solo Agent (LLM)
    │
    ├─① solo_record(date=今天, tags="家庭,图图,游乐场,明月", ...)
    │     → 写入 records 表（日常日志，主要记录游乐场）
    │
    └─② solo_health_record(
            subject="明月",          ← 关键：从附带信息中识别出健康事件
            category="medical",
            item="体检",
            description="明月去体检",
            date=今天
        )
          → 写入 health_records 表
```

### 1.3 与现有模式的关系

| 模式 | 工具 | 写入目标 | 触发时机 |
|------|------|----------|----------|
| 日常记录 | `solo_record` | `records` 表 | 每次用户发送日常内容 |
| 长效记忆 | `solo_remember` | `memory/` 文件 | 检测到稳定个人事实时（同一轮） |
| **健康记录** | **`solo_health_record`** | **`health_records` 表** | **检测到健康相关信息时（同一轮）** |
| 待办提取 | `solo_add_todo` | `todos` 表 | 检测到待办/计划时 |
| 行为实验 | `solo_add_experiment` | `experiments` 表 | 检测到行为改变意图时 |

---

## 2. 结构化健康数据库

### 2.1 SQLite 表设计

在 `SoloStore` 的 `_SCHEMA_SQL` 中新增 `health_records` 表：

```sql
CREATE TABLE IF NOT EXISTS health_records (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT 'self',
    -- 健康记录的主体：'self'（用户自己）或家庭成员名称（如'图图'、'明月'）
    category TEXT NOT NULL,
    -- 健康类别，见下方"类别体系"说明
    item TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    body_part TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    -- mild | moderate | severe | '' (不适用时)
    status TEXT NOT NULL DEFAULT 'active',
    -- active | resolved | chronic | recurring
    medication_name TEXT NOT NULL DEFAULT '',
    dosage TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    duration TEXT NOT NULL DEFAULT '',
    exercise_type TEXT NOT NULL DEFAULT '',
    exercise_duration_min INTEGER NOT NULL DEFAULT 0,
    exercise_intensity TEXT NOT NULL DEFAULT '',
    -- low | moderate | high | '' (不适用时)
    sleep_hours REAL NOT NULL DEFAULT 0,
    sleep_quality TEXT NOT NULL DEFAULT '',
    -- good | fair | poor | '' (不适用时)
    mood TEXT NOT NULL DEFAULT '',
    stress_level TEXT NOT NULL DEFAULT '',
    -- low | moderate | high | '' (不适用时)
    metrics_json TEXT NOT NULL DEFAULT '{}',
    -- 灵活扩展字段，如 {"weight_kg": 72.5, "heart_rate": 68, "steps": 8000}
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent',
    -- agent | user | import
    linked_memory_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_health_records_date ON health_records(date);
CREATE INDEX IF NOT EXISTS idx_health_records_subject ON health_records(subject);
CREATE INDEX IF NOT EXISTS idx_health_records_category ON health_records(category);
CREATE INDEX IF NOT EXISTS idx_health_records_status ON health_records(status);
CREATE INDEX IF NOT EXISTS idx_health_records_record_id ON health_records(record_id);
```

> 说明：迁移采用 `CREATE TABLE IF NOT EXISTS` 的幂等写法，挂在 `_apply_migrations()` 的 v7 分支即可，**不需要** `_table_exists()` 这类探测方法（项目其他迁移也都是幂等建表）。

### 2.2 字段设计说明

#### 通用字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | 唯一标识（`uuid4().hex[:12]`，与 records/todos 一致） |
| `record_id` | TEXT | 关联的 `records` 表 ID（**必填**，见 §2.6 关联策略） |
| `date` | TEXT | 日期 YYYY-MM-DD（与 `solo_record` 同源，见 §7.4） |
| `subject` | TEXT | 健康记录的主体：`'self'`（用户自己）或家庭成员名称（如`'图图'`、`'明月'`）。用于区分用户本人和家人（尤其是子女）的健康数据 |
| `category` | TEXT | 健康类别，见下方"类别体系"说明。优先使用推荐类别，允许模型在必要时创建新类别（受约束） |
| `item` | TEXT | 主要条目名称（如"跑步"、"布洛芬"、"头疼"） |
| `description` | TEXT | 详细描述 |
| `body_part` | TEXT | 涉及的身体部位 |
| `severity` | TEXT | 严重程度 |
| `status` | TEXT | 当前状态 |
| `tags` | TEXT | 逗号分隔标签 |
| `source` | TEXT | 数据来源 |
| `linked_memory_id` | TEXT | 关联的 Memory 文件 ID（如关联到 medical_history.md） |
| `metrics_json` | TEXT | JSON 格式的量化指标扩展字段 |

#### 类别专属字段

根据 `category` 的不同，使用不同的字段组合：

| category | 使用的专属字段 | 示例 |
|----------|---------------|------|
| `medical` | description, body_part, status | "年度体检，血常规正常" |
| `symptom` | body_part, severity, status, duration | "头疼，中度，持续2小时" |
| `medication` | medication_name, dosage, frequency, duration, reason | "布洛芬，1颗，饭后，止痛" |
| `fitness` | exercise_type, exercise_duration_min, exercise_intensity | "跑步，30分钟，中等强度" |
| `sleep` | sleep_hours, sleep_quality | "7.5小时，质量良好" |
| `nutrition` | description, metrics_json | "控制碳水摄入" |
| `mental` | mood, stress_level, description | "情绪低落，压力中等" |
| `vital` | metrics_json | {"weight_kg": 72.5, "heart_rate": 68} |

#### 类别体系（Category System）

`category` 字段采用**推荐类别 + 约束规则**的设计，而非硬编码枚举。这样既保持灵活性，又防止模型发散创建过多任意类别。

**推荐类别（优先使用）：**

| category | 中文含义 | 适用场景 | 典型专属字段 |
|----------|----------|----------|-------------|
| `medical` | 医疗就诊 | 医院就诊、体检、复查、手术、诊断 | description, status |
| `symptom` | 身体症状 | 头疼、鼻炎、感冒、过敏、疼痛、疲劳 | body_part, severity, duration |
| `medication` | 用药记录 | 服药、处方、保健品、疫苗接种 | medication_name, dosage, frequency |
| `fitness` | 运动健身 | 跑步、游泳、骑行、力量训练、瑜伽 | exercise_type, exercise_duration_min, exercise_intensity |
| `sleep` | 睡眠记录 | 入睡时间、睡眠时长、睡眠质量、失眠 | sleep_hours, sleep_quality |
| `nutrition` | 饮食营养 | 饮食习惯、节食、营养补充、戒糖 | description, metrics_json |
| `mental` | 心理健康 | 情绪波动、压力、焦虑、抑郁、冥想 | mood, stress_level |
| `vital` | 体征数据 | 体重、心率、血压、血氧、体温等量化指标 | metrics_json |

**约束规则（防止发散）：**

1. **优先匹配**：模型必须首先尝试将健康事件归类到上述推荐类别中
2. **新类别条件**：仅当健康事件**明确不属于任何推荐类别**时，才允许创建新类别
3. **命名规范**：新类别必须使用**单个英文小写单词**（如 `dental`、`dermatology`、`rehabilitation`）
4. **语义明确**：新类别名称必须语义清晰，不得使用模糊词（如 `other`、`misc`、`general`）
5. **去重检查**：创建新类别前，应先查询 `health_record_categories()` 确认是否已有语义相近的类别
6. **数量限制**：系统中的总类别数建议不超过 15 个，超出时应考虑合并到现有类别

**工具层面的约束：**

在 `solo_health_record` 工具的 `category` 参数描述中，明确列出推荐类别和约束规则：

```
"category": "Health category. PREFERRED: Use one of these standard categories if applicable:
medical (doctor visits, checkups, surgery),
symptom (headache, allergy, pain, fatigue),
medication (drugs, prescriptions, supplements),
fitness (running, swimming, gym, yoga),
sleep (sleep duration, quality, insomnia),
nutrition (diet habits, supplements, fasting),
mental (mood, stress, anxiety, meditation),
vital (weight, heart rate, blood pressure, temperature).
If NONE of the above fit, you may create a new category using a single lowercase English word
(e.g. 'dental', 'dermatology'). Do NOT use vague names like 'other' or 'misc'."
```

**Store 层面的支持：**

`health_record_categories()` 方法返回所有已存在的类别及其计数，供模型和前端参考：

```python
def health_record_categories(self) -> dict[str, int]:
    """返回 {category: count} 字典，用于去重检查和前端展示。"""
```

同理，`health_record_subjects()` 返回所有主体（含计数），供前端 SubjectFilter 与 `/subjects` 端点使用：

```python
def health_record_subjects(self) -> dict[str, int]:
    """返回 {subject: count} 字典，如 {'self': 31, '图图': 13, '明月': 2}。"""
```

### 2.3 Dataclass 模型

在 `solo/core/models.py` 中新增：

```python
@dataclass(frozen=True)
class SoloHealthRecord:
    """One structured health record."""

    id: str
    record_id: str = ""
    date: str = ""
    subject: str = "self"       # self|图图|明月|... (健康记录主体)
    category: str = ""          # medical|symptom|medication|fitness|sleep|nutrition|mental|vital
    item: str = ""
    description: str = ""
    body_part: str = ""
    severity: str = ""          # mild|moderate|severe|''
    status: str = "active"      # active|resolved|chronic|recurring
    medication_name: str = ""
    dosage: str = ""
    frequency: str = ""
    duration: str = ""
    exercise_type: str = ""
    exercise_duration_min: int = 0
    exercise_intensity: str = ""  # low|moderate|high|''
    sleep_hours: float = 0
    sleep_quality: str = ""      # good|fair|poor|''
    mood: str = ""
    stress_level: str = ""       # low|moderate|high|''
    metrics_json: str = "{}"
    tags: str = ""
    source: str = "agent"
    linked_memory_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def metrics(self) -> dict[str, Any]:
        """统一解析 metrics_json，避免每个调用点重复 try/except。解析失败返回 {}。"""
        if not self.metrics_json:
            return {}
        try:
            result = json.loads(self.metrics_json)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @classmethod
    def from_json(cls, line: str) -> "SoloHealthRecord":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "record_id": self.record_id, "date": self.date,
            "subject": self.subject,
            "category": self.category, "item": self.item,
            "description": self.description, "body_part": self.body_part,
            "severity": self.severity, "status": self.status,
            "medication_name": self.medication_name, "dosage": self.dosage,
            "frequency": self.frequency, "duration": self.duration,
            "exercise_type": self.exercise_type,
            "exercise_duration_min": self.exercise_duration_min,
            "exercise_intensity": self.exercise_intensity,
            "sleep_hours": self.sleep_hours, "sleep_quality": self.sleep_quality,
            "mood": self.mood, "stress_level": self.stress_level,
            "metrics_json": self.metrics_json, "tags": self.tags,
            "source": self.source, "linked_memory_id": self.linked_memory_id,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
```

> `metrics` property 统一了 `metrics_json` 的解析入口；后端聚合方法（vitals/overview 等）统一走 `r.metrics`，不再各自 try/except。

### 2.4 Store 方法

在 `SoloStore` 中新增（**注意 `list_health_records` 必须支持 `subject` 过滤，这是多主体统计链路的基础**）：

```python
def add_health_record(self, record: SoloHealthRecord) -> None: ...
def list_health_records(
    self, *,
    subject: str | None = None,        # ← 必需：多主体过滤下推到 SQL
    category: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
) -> list[SoloHealthRecord]: ...
def get_health_record(self, record_id: str) -> SoloHealthRecord | None: ...
def update_health_record(self, record_id: str, **fields) -> bool: ...
def delete_health_record(self, record_id: str) -> bool: ...
def health_record_categories(self) -> dict[str, int]: ...
def health_record_subjects(self) -> dict[str, int]: ...
```

> `subject` 过滤必须下推到 SQL（走 `idx_health_records_subject` 索引），而不是在 service 层拉全量再 Counter，否则在数据量增长后 `/health?subject=图图` 会变成全表扫描后内存过滤。

### 2.5 Schema 迁移

将 `_SCHEMA_VERSION` 从 6 提升到 7，在 `_apply_migrations()` 中添加（幂等写法）：

```python
# v7: health_records table（幂等：CREATE TABLE IF NOT EXISTS）
self._conn.executescript(_HEALTH_RECORDS_DDL)
self._conn.commit()
```

> 不需要 `_table_exists()` 探测。若库里已存在同名表，`IF NOT EXISTS` 自动跳过，安全可重入。

### 2.6 数据完整性与关联策略

#### record_id 关联（强制：每条健康记录必须有源可溯）

**核心原则：每条 `health_record` 必须有对应的 `record_id`，不允许存在"无源记录"。**

设计初稿曾将 `record_id` 设计为"尽力而为"的可空字段，但这会导致用户回溯时无法追溯健康记录的来源，造成困惑。修正后的策略如下：

1. **LLM 不传 record_id**：`solo_health_record` 的参数列表里**不含** `record_id`（LLM 并发调用时无法获知同轮 `solo_record` 的返回 id，强行让 LLM 传值注定失效）。
2. **所有写入路径都先经过 `solo_record`**：无论是 agent 对话路径还是 UI 手动录入路径，都必须先创建一条 `solo_record`，再创建 `solo_health_record`。这保证了每条健康记录都有对应的日志来源。
3. **Agent 路径——编排层轮后回填**：`solo_record` 和 `solo_health_record` 在同一轮通过 `asyncio.gather()` 并发执行。执行完成后，编排层（`SoloToolRegistry.post_turn_backfill()`）从 `_created_record_ids` 取出本轮创建的 record_id，回填到同轮创建的所有 health_record 中。具体机制：
   - `solo_record` handler 执行完成后，将 `record.id` 推入 `self._created_record_ids`（已有机制）
   - `solo_health_record` handler 执行完成后，将 `health_record.id` 推入 `self._pending_health_ids`（新增）
   - 本轮所有工具执行完毕后，runner 调用 `registry.post_turn_backfill()`：取 `_created_record_ids` 中最新的 record_id，通过 `store.update_health_record(health_id, record_id=record_id)` 回填到每条 pending 的 health_record
4. **UI 手动录入路径**：用户在 Onboard Health 页面手动添加健康记录时，系统先创建一条 `solo_record`（`source="manual"`），拿到 `record_id` 后再创建 `solo_health_record`（`record_id` 直接填入）。两步顺序执行，不存在并发问题。
5. **数据溯源链路**：`health_record → record → entry → 用户原始输入`，每一环都有明确 ID 关联，用户可随时追溯。

> 即：**record_id 是必填的强关联，不是可选的**。这确保了健康数据的完整可溯性。

#### subject 归一化

- 默认 `'self'`（小写固定），表示用户本人。
- 家庭成员名称使用用户在消息中的原始称呼（如"图图"、"明月"、"老妈"），**不做别名归一化**（"老妈"和"妈妈"视为不同 subject，由用户自己在前端合并或重命名，见后续演进）。
- subject 区分大小写吗？**不区分**——查询时在 service 层做 `casefold` 归一化（避免"Self"和"self"分裂）。

#### category 校验

- 推荐类别外的新类别，必须满足：单个英文小写单词、`isalpha()`、长度 ≤ 20、不在 VAGUE 黑名单（`other/misc/general/unknown/custom/test`）。
- 校验在工具处理器层完成，校验失败返回统一错误格式（见 §3.1）。

---

## 3. Agent 工具设计

### 3.1 `solo_health_record` 工具

#### 工具定义

```python
def _tool_health_record() -> ToolDefinition:
    return _definition(
        "solo_health_record",
        (
            "Record a STRUCTURED health-related event into the dedicated health database. "
            "Call this tool whenever the user's message contains information about: "
            "physical symptoms, medical visits, medications, exercise/fitness activities, "
            "sleep patterns, nutrition/diet, mental health/mood changes, or vital signs. "
            "IMPORTANT: Health info may be mentioned INCIDENTALLY as a side note in a daily record "
            "(e.g. '明月没去游乐场，因为她去体检了' → extract 明月's medical visit). "
            "Scan the ENTIRE message for health signals, not just the main topic. "
            "Call this in the SAME TURN as solo_record when the user's message "
            "contains both daily events AND health information. "
            "You may call this tool MULTIPLE TIMES per turn if the message contains "
            "different types of health events (e.g. exercise + medication). "
            "For STABLE health facts (chronic conditions, allergies), use solo_remember instead."
        ),
        [
            ("category", "string",
             "Health category. PREFERRED: Use one of these standard categories if applicable: "
             "medical (doctor visits, checkups, surgery), "
             "symptom (headache, allergy, pain, fatigue), "
             "medication (drugs, prescriptions, supplements), "
             "fitness (running, swimming, gym, yoga), "
             "sleep (sleep duration, quality, insomnia), "
             "nutrition (diet habits, supplements, fasting), "
             "mental (mood, stress, anxiety, meditation), "
             "vital (weight, heart rate, blood pressure, temperature). "
             "If NONE of the above fit, you may create a new category using a single lowercase English word "
             "(e.g. 'dental', 'dermatology'). Do NOT use vague names like 'other' or 'misc'.",
             True),
            ("item", "string",
             "Primary item name (e.g. '跑步', '布洛芬', '头疼', '年度体检').",
             True),
            ("date", "string", "Date in YYYY-MM-DD format. Defaults to today.", False),
            ("subject", "string",
             "Who this health record is about: 'self' (the user), or a family member name (e.g. '图图', '明月'). "
             "Default: 'self'. Set this when the health event is about a family member, especially children.",
             False),
            ("description", "string", "Detailed description of the health event.", False),
            ("body_part", "string", "Affected body part (e.g. '膝盖', '头', '腰').", False),
            ("severity", "string", "Severity: mild, moderate, severe. Leave empty if N/A.", False),
            ("status", "string", "Status: active, resolved, chronic, recurring. Default: active.", False),
            ("medication_name", "string", "Medication name (for category=medication).", False),
            ("dosage", "string", "Dosage (e.g. '1颗', '5ml').", False),
            ("frequency", "string", "Frequency (e.g. '每日两次', '按需').", False),
            ("duration", "string", "Duration (e.g. '2小时', '3天').", False),
            ("exercise_type", "string", "Exercise type (for category=fitness).", False),
            ("exercise_duration_min", "integer", "Exercise duration in minutes.", False),
            ("exercise_intensity", "string", "Exercise intensity: low, moderate, high.", False),
            ("sleep_hours", "number", "Hours of sleep (for category=sleep).", False),
            ("sleep_quality", "string", "Sleep quality: good, fair, poor.", False),
            ("mood", "string", "Mood description (for category=mental).", False),
            ("stress_level", "string", "Stress level: low, moderate, high.", False),
            ("metrics_json", "string",
             "JSON string for extra metrics (e.g. '{\"weight_kg\": 72.5, \"steps\": 8000}').",
             False),
            ("tags", "string", "Comma-separated tags.", False),
            # 注意：record_id 不暴露给 LLM，由编排层 post_turn_backfill() 强制回填（见 §2.6）
        ],
    )
```

#### 工具处理器

```python
async def _handle_health_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
    category = _required_text(arguments, "category")
    item = _required_text(arguments, "item")

    # Validate category: prefer standard categories, allow new ones with constraints
    STANDARD_CATEGORIES = {"medical", "symptom", "medication", "fitness", "sleep", "nutrition", "mental", "vital"}
    VAGUE_NAMES = {"other", "misc", "general", "unknown", "custom", "test"}

    if category not in STANDARD_CATEGORIES:
        # New category: must be single lowercase English word, no vague names
        if not category.isalpha() or not category.islower() or len(category) > 20:
            return {"ok": False, "error": f"Invalid category '{category}'. Use a standard category or a single lowercase English word."}
        if category in VAGUE_NAMES:
            return {"ok": False, "error": f"Category '{category}' is too vague. Use a descriptive name."}

    local_today = _now()[:10]  # 与 solo_record 同源的本地日期

    record = SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id="",  # 由编排层 post_turn_backfill() 回填，见 §2.6
        date=str(arguments.get("date") or local_today),
        subject=str(arguments.get("subject") or "self").strip() or "self",
        category=category,
        item=item,
        description=str(arguments.get("description") or ""),
        body_part=str(arguments.get("body_part") or ""),
        severity=str(arguments.get("severity") or ""),
        status=str(arguments.get("status") or "active"),
        medication_name=str(arguments.get("medication_name") or ""),
        dosage=str(arguments.get("dosage") or ""),
        frequency=str(arguments.get("frequency") or ""),
        duration=str(arguments.get("duration") or ""),
        exercise_type=str(arguments.get("exercise_type") or ""),
        exercise_duration_min=int(arguments.get("exercise_duration_min") or 0),
        exercise_intensity=str(arguments.get("exercise_intensity") or ""),
        sleep_hours=float(arguments.get("sleep_hours") or 0),
        sleep_quality=str(arguments.get("sleep_quality") or ""),
        mood=str(arguments.get("mood") or ""),
        stress_level=str(arguments.get("stress_level") or ""),
        metrics_json=str(arguments.get("metrics_json") or "{}"),
        tags=str(arguments.get("tags") or ""),
        source="agent",
        linked_memory_id=str(arguments.get("linked_memory_id") or ""),
        created_at=_now(),
        updated_at=_now(),
    )
    self.store.add_health_record(record)
    # 记录 pending health id，供编排层 post_turn_backfill() 回填 record_id（见 §2.6）
    self._pending_health_ids.append(record.id)
    # 返回格式与 _handle_remember 等对齐：ok + message（不返回 id，避免泄露内部 id）
    return {"ok": True, "message": f"健康记录已入库：{category}/{item} ({record.date})"}
```

#### 返回契约（与项目惯例对齐）

- 成功：`{"ok": True, "message": "..."}`，**不返回 `id`**（与 `_handle_remember`、`_handle_add_todo` 的成功返回风格一致；id 是内部标识，无需暴露给 LLM）。
- 失败：`{"ok": False, "error": "..."}`（与项目其他工具的校验失败返回一致）。
- 这一约定在 plan 的 Phase 3 测试用例中固化。

#### 注册

在 `SoloToolRegistry.tools()` 中添加：

```python
SoloDomainTool(_tool_health_record(), self._handle_health_record),
```

### 3.2 辅助查询工具 `solo_health_summary`

为 agent 提供查询健康历史的能力（用于回答用户的健康相关问题）。**必须支持 `subject` 参数**，否则 agent 无法回答"图图最近吃药情况"这类多主体问题：

```python
def _tool_health_summary() -> ToolDefinition:
    return _definition(
        "solo_health_summary",
        (
            "Query structured health records for a given time range, subject, and/or category. "
            "Use when the user asks about their own or a family member's health history, medication usage, "
            "exercise patterns, sleep quality, etc. "
            "Returns aggregated statistics and recent records."
        ),
        [
            ("subject", "string",
             "Filter by subject: 'self' (the user) or a family member name (e.g. '图图', '明月'). "
             "Leave empty to query all subjects.",
             False),
            ("category", "string", "Filter by health category: medical, symptom, medication, fitness, sleep, nutrition, mental, vital.", False),
            ("days", "integer", "Look back N days (default 30).", False),
            ("status", "string", "Filter by status: active, resolved, chronic, recurring.", False),
        ],
    )
```

---

### 3.3 编排层轮后回填机制 `post_turn_backfill()`

Agent 对话路径中，`solo_record` 和 `solo_health_record` 通过 `asyncio.gather()` 并发执行，handler 内部无法获知对方的返回值。因此采用**轮后回填**策略（详见 §2.6）：

#### SoloToolRegistry 新增成员

```python
class SoloToolRegistry:
    def __init__(self, ...):
        # ... existing init ...
        self._pending_health_ids: list[str] = []  # 本轮创建的 health_record id，待回填 record_id
```

#### post_turn_backfill() 方法

```python
def post_turn_backfill(self) -> None:
    """本轮所有工具执行完毕后调用。
    将本轮 solo_record 创建的 record_id 回填到同轮创建的 health_record 中。
    """
    if not self._pending_health_ids or not self._created_record_ids:
        self._pending_health_ids.clear()
        return

    # 取本轮最新创建的 record_id（solo_record 每次只创建一条）
    record_id = list(self._created_record_ids)[-1]

    for health_id in self._pending_health_ids:
        self.store.update_health_record(health_id, record_id=record_id)

    self._pending_health_ids.clear()
```

#### Runner 挂载点

在 `solo/runner.py` 的 `_process_stream` 中，当检测到一轮所有 `ToolExecutionCompleted` 事件都已到达时（即下一个事件不是工具结果），调用 `registry.post_turn_backfill()`。

```python
# 在 runner 的 stream processing loop 中:
# 当一轮工具执行全部完成后（AgentTurnComplete 或新一轮 AssistantMessage 之前）
registry.post_turn_backfill()
```

---

## 4. 提示词优化

### 4.1 在 solo system prompt 中新增健康记录指引

在 `solo/prompts.py` 的"工具路由决策表"中新增一行：

```
| 用户提到身体健康相关内容（症状、用药、运动、睡眠、饮食、心理状态、体检、体征数据） | → solo_health_record（同一轮与 solo_record 并行调用，category 优先使用推荐类别） |
```

### 4.2 新增独立章节：健康记录提取原则

在 prompt 的"长效事实提取原则"之后，新增一个平级章节：

```markdown
## 健康记录提取原则

用户的日常记录中经常包含**健康相关信息**——症状、用药、运动、睡眠、饮食、心理变化等。
这些信息需要写入专用的 `health_records` 表，以便后续统计和趋势分析。

### 判断标准：这条信息是否与身体健康直接相关？
问自己：**如果用户未来想回顾自己的健康历史，这条信息是否有参考价值？** 如果是，调用 `solo_health_record`。

### 隐含健康信息识别（重要）

用户的日常记录中经常**顺嘴提到**自己或家人的健康信息，这些信息虽然不是记录的主角，但同样需要识别并记录到 health_records 表。

**识别要点：**
- 不要只看记录的主题/主要内容，要**逐句扫描**是否有健康相关的附带信息
- 特别注意"**因为…所以…**"、"**没有去，因为…**"、"**顺便…**"等因果/附带结构
- 即使整条记录的主题是游乐场/购物/工作，其中提到的体检、看病、吃药等仍需提取

**典型隐含场景：**

| 用户说的（日常记录） | 隐含的健康信息 | 提取结果 |
|---------------------|---------------|----------|
| "今天带图图去游乐场玩了，明月没去，因为她去体检了" | 明月去体检 | subject=明月, category=medical, item=体检 |
| "周末陪爸妈去了一趟医院，老爸做了个胃镜" | 老爸做胃镜 | subject=老爸, category=medical, item=胃镜 |
| "今天加班到很晚，吃了颗维生素C" | 吃维生素C | subject=self, category=medication, item=维生素C |
| "图图在幼儿园被小朋友传染了，开始流鼻涕" | 图图流鼻涕 | subject=图图, category=symptom, item=流鼻涕 |
| "明月最近一直在吃中药调理身体" | 明月吃中药 | subject=明月, category=medication, item=中药 |
| "下午开会头疼，喝了杯咖啡就好了" | 头疼 | subject=self, category=symptom, item=头疼 |

### 类别选择（category）

优先使用以下推荐类别，仅在明确不属于任何推荐类别时才创建新类别（单个英文小写单词）：

| category | 适用场景 |
|----------|----------|
| `medical` | 医院就诊、体检、复查、手术、诊断 |
| `symptom` | 头疼、鼻炎、感冒、过敏、疼痛、疲劳 |
| `medication` | 服药、处方、保健品、疫苗接种 |
| `fitness` | 跑步、游泳、骑行、力量训练、瑜伽 |
| `sleep` | 入睡时间、睡眠时长、睡眠质量、失眠 |
| `nutrition` | 饮食习惯、节食、营养补充、戒糖 |
| `mental` | 情绪波动、压力、焦虑、抑郁、冥想 |
| `vital` | 体重、心率、血压、血氧、体温等量化指标 |

**约束**：新类别必须是单个英文小写单词（如 `dental`），禁止使用 `other`、`misc` 等模糊名称。

### 典型场景

| 用户说的 | subject | category | item | 关键参数 |
|---------|---------|----------|------|---------|
| "今天跑了5公里" | self | fitness | 跑步 | exercise_type=跑步, exercise_duration_min=30, exercise_intensity=moderate |
| "头疼了一整天" | self | symptom | 头疼 | body_part=头, severity=moderate, duration=一整天 |
| "吃了布洛芬止痛" | self | medication | 布洛芬 | medication_name=布洛芬, dosage=1颗, reason=止痛 |
| "昨晚睡了8小时，质量不错" | self | sleep | 睡眠 | sleep_hours=8, sleep_quality=good |
| "今天情绪很低落" | self | mental | 情绪低落 | mood=低落, stress_level 根据上下文判断 |
| "去医院做了体检" | self | medical | 年度体检 | description=体检结果 |
| "体重72.5kg" | self | vital | 体重 | metrics_json={"weight_kg": 72.5} |
| "控制碳水摄入" | self | nutrition | 低碳饮食 | description=控制碳水 |
| "带图图去新华医院做发育评估" | **图图** | medical | 发育评估 | description=评估结果 |
| "图图鼻炎又犯了" | **图图** | symptom | 过敏性鼻炎 | body_part=鼻, severity=mild |
| "图图确诊过敏体质，开了眼药水" | **图图** | medication | 氨卓斯汀滴眼液 | medication_name=氨卓斯汀滴眼液, frequency=早晚各一次 |
| "明月去医院复查皮肤" | **明月** | medical | 复查 | description=复查结果 |
| "带图图去游乐场，明月没去因为她去体检了" | **明月**（隐含） | medical | 体检 | description=明月去体检（从日常记录附带信息中提取） |

**subject 判断规则：**
- 默认 `self`（用户自己）
- 当消息明确提到家庭成员（如"图图"、"明月"、"老婆"）的健康事件时，设为对应名称
- 特别注意儿童健康记录：用户经常记录子女的就诊、症状、用药，必须正确识别 subject

### 操作要求

1. 每次调用 `solo_record` 时，同步检查消息是否包含健康信息。若有，**同一轮**调用 `solo_health_record`
2. **逐句扫描整条消息**，不要只看主题。健康信息可能作为附带信息出现（如"明月没去，因为她去体检了"）
3. 一条消息可能包含**多种健康事件**（如运动+用药），此时**分别调用**多次 `solo_health_record`
4. `item` 使用中文，简明扼要（如"跑步"、"布洛芬"、"头疼"）
5. 只填与 category 相关的字段，不要填无关字段
6. `metrics_json` 仅用于无法被其他字段覆盖的量化数据（体重、心率、步数等）
7. **`subject` 必须正确识别**：默认 `self`，但当健康事件是关于家庭成员（尤其是子女）时，必须设为对应名称（如"图图"、"明月"）
8. **稳定的健康事实**（如"我有过敏性鼻炎"）仍然用 `solo_remember`，`solo_health_record` 记录的是**事件级别**的健康信息（如"今天鼻炎发作了"）
9. 如果用户只是泛泛提到健康但不包含具体事件（如"要注意健康了"），不需要调用

### 不提取的情况
- 纯粹是工作计划中的运动安排描述，不含实际执行
- 已经通过 solo_remember 存入的稳定健康事实
- 无法提取出具体 item 的模糊表述
```

### 4.3 在 `_handle_record` 的 side-effect 提示中补充

在 `solo_record` 工具的 description 的 `SIDE-EFFECT CHECK` 中追加 `solo_health_record`：

```
SIDE-EFFECT CHECK: If this message reveals persistent personal facts (chronic health conditions,
new relationships, life structure changes, long-term preferences), also call solo_remember in the
SAME turn. If this message contains health-related events (symptoms, medication, exercise, sleep,
mood changes, medical visits, vital signs), also call solo_health_record in the SAME turn —
once per distinct health event. IMPORTANT: Health info may be mentioned INCIDENTALLY as a side
note in a daily record (e.g. "明月没去游乐场，因为她去体检了" → extract 明月's medical visit).
Scan the ENTIRE message, not just the main topic. Set the `subject` parameter correctly — default is
"self" (the user), but if the health event is about a family member (e.g. child, spouse),
set subject to their name (e.g. "图图", "明月").
```

---

## 5. Onboard Health 页面设计

### 5.1 数据来源（全部来自结构化表）

所有数据直接从 `health_records` 表查询，无需关键词匹配。所有查询均支持 `subject` 过滤（下推到 SQL）：

| 页面区域 | 查询方式 |
|----------|----------|
| **主体概览** | `health_record_categories()` + `health_record_subjects()` 各自计数 |
| 统计概览 | `list_health_records(subject=X)` 按类别计数（受主体过滤影响） |
| 情绪/心理趋势 | `list_health_records(subject=X, category="mental")` 按日期聚合 |
| 运动统计 | `list_health_records(subject=X, category="fitness")` 按时长/强度聚合 |
| 症状追踪 | `list_health_records(subject=X, category="symptom")` 按部位/频率聚合 |
| 用药记录 | `list_health_records(subject=X, category="medication")` 按药名聚合 |
| 睡眠分析 | `list_health_records(subject=X, category="sleep")` 按时长/质量趋势 |
| 体检/就诊 | `list_health_records(subject=X, category="medical")` 按时间线展示 |
| 体征数据 | `list_health_records(subject=X, category="vital")` 按指标趋势图 |

### 5.2 页面布局

```
┌─────────────────────────────────────────────────────────────┐
│  Health                                                     │
│  身心健康统计与趋势                                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  [全部] [自己] [图图] [明月]       (主体选择器)       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ 总记录数  │ │ 运动次数  │ │ 症状记录  │ │ 用药记录  │      │
│  │   128    │ │   45次   │ │   23条   │ │   15条   │      │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘      │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  类别分布 (Category Breakdown)            [30d|90d|all]│   │
│  │  堆叠柱状图: 每日各类别记录数                            │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────────────────┐ ┌──────────────────────┐        │
│  │  运动趋势             │ │  睡眠趋势              │        │
│  │  (fitness)            │ │  (sleep)              │        │
│  │  面积图: 每周运动      │ │  折线图: 睡眠时长       │        │
│  │  时长 + 强度分布       │ │  趋势 + 质量标记       │        │
│  └──────────────────────┘ └──────────────────────┘        │
│                                                             │
│  ┌──────────────────────┐ ┌──────────────────────┐        │
│  │  症状追踪             │ │  用药记录              │        │
│  │  (symptom)            │ │  (medication)         │        │
│  │  按部位+频次排行       │ │  按药名+使用频次       │        │
│  └──────────────────────┘ └──────────────────────┘        │
│                                                             │
│  ┌──────────────────────┐ ┌──────────────────────┐        │
│  │  心理状态             │ │  体征数据              │        │
│  │  (mental)             │ │  (vital)              │        │
│  │  情绪+压力趋势        │ │  体重/心率折线图       │        │
│  └──────────────────────┘ └──────────────────────┘        │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  健康时间线 (All Events)                  [按周|按月]  │   │
│  │  2025-06-17 👶图图 medical/Gesell发育评估, 正常     │   │
│  │  2025-06-17 👤自己 fitness/跑步 5km, 30min         │   │
│  │  2025-06-15 👤自己 😴 sleep/7.5h, good            │   │
│  │  2025-06-13 👤自己 🤧 鼻炎发作, moderate           │   │
│  │  2025-06-13 👤自己 💊 色甘奈甲那敏鼻喷雾剂          │   │
│  │  ...                                               │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 后端 API 设计

所有端点直接查询 `health_records` 表（**仅 solo**，wolo 不提供健康 API；solo-only 的强制由 §5.5 描述）：

```
GET  /api/solo/health/overview?subject=
     → 各类别计数 + 各主体计数(by_subject) + 近期趋势概览
       （subject 可选：传入时统计卡片按主体过滤；by_subject 始终返回全量主体列表）

GET  /api/solo/health/subjects
     → 返回所有出现过的 subject 列表及各自记录数（如 {"self": 31, "图图": 13, "明月": 2}）
       供前端 SubjectFilter 渲染。独立端点，避免与 overview 耦合。

GET  /api/solo/health/categories
     → 按类别聚合统计

GET  /api/solo/health/records?subject=&category=&status=&date_from=&date_to=&limit=&offset=
     → 分页查询健康记录（subject 过滤下推 SQL：subject=self 只看自己，subject=图图 只看孩子）

GET  /api/solo/health/fitness?subject=&days=30
     → 运动趋势：每周运动时长、强度分布、常见运动类型
GET  /api/solo/health/sleep?subject=&days=30
     → 睡眠趋势：平均时长、质量分布、趋势线
GET  /api/solo/health/symptoms?subject=&days=90
     → 症状排行：按部位/名称聚合、发作频率、严重程度分布
GET  /api/solo/health/medications?subject=&days=90
     → 用药记录：按药名聚合、使用频次、当前用药列表
GET  /api/solo/health/mental?subject=&days=30
     → 心理状态：情绪趋势、压力水平分布
GET  /api/solo/health/vitals?subject=&days=90
     → 体征数据：体重/心率等指标的时序数据
GET  /api/solo/health/timeline?subject=&limit=30&offset=0
     → 全类型混合时间线（按日期倒序）

# ── 写操作（可选，见 §5.6 隐私权衡） ─────────────────────
DELETE /api/solo/health/records/{id}
     → 删除单条健康记录（用户主动删除）
PATCH  /api/solo/health/records/{id}
     → 更新单条记录的字段（如修正 severity、补充 description、改 status）
```

> 所有细分趋势端点（fitness/sleep/symptoms/medications/mental/vitals/timeline）**统一接受 `subject` 查询参数**，未传则返回全部主体聚合，传了则按主体下推过滤。这是前端 SubjectFilter 能正常工作的前提。

### 5.4 前端组件

```
onboard/frontend/src/
├── pages/
│   └── Health.tsx                       # 主页面
├── components/
│   └── health/
│       ├── SubjectFilter.tsx            # 主体选择器（全部/自己/图图/明月...）
│       ├── HealthStatsCards.tsx          # 顶部统计卡片
│       ├── CategoryBreakdown.tsx        # 类别分布图
│       ├── FitnessTrend.tsx             # 运动趋势
│       ├── SleepTrend.tsx               # 睡眠趋势
│       ├── SymptomTracker.tsx           # 症状追踪
│       ├── MedicationList.tsx           # 用药记录
│       ├── MentalHealthPanel.tsx        # 心理状态
│       ├── VitalSignsChart.tsx          # 体征图表
│       └── HealthTimeline.tsx           # 健康时间线
```

**前端数据流（SubjectFilter 联动）：**

```
Health.tsx
  │
  ├─ mount → api.health.subjects()            ← 拿到主体列表 [全部/自己/图图/明月]
  │         → api.health.overview({subject})  ← 顶部统计卡片（受 subject 过滤）
  │
  ├─ selectedSubject 状态（默认"全部"）
  │
  └─ 每当 selectedSubject 变化 → 并行调用各细分端点，全部带上 subject：
       api.health.fitness({subject, days})
       api.health.sleep({subject, days})
       api.health.symptoms({subject, days})
       api.health.medications({subject, days})
       api.health.mental({subject, days})
       api.health.vitals({subject, days})
       api.health.timeline({subject, limit})
```

> 关键：**SubjectFilter 的值必须贯穿到每一个子组件的 API 调用**。前端 client 的每个 health 方法签名都必须接受 `subject?: string`（见 plan Phase 7.2），否则切主体后图表不刷新，违背设计。

### 5.5 Solo-only 强制（wolo 隔离）

Health 模块**仅在 solo 下可用**，wolo 模式下必须完全不可访问。隔离分两层：

1. **后端**：Health 路由挂在 `/api/solo/health/*`（solo 前缀），与 wolo 路由物理隔离。参考现有 `solo_routes.router`（prefix `/api/solo`）的组织方式，新建 `onboard/api/health.py` 时**沿用 `/api/solo` 前缀**，使其天然只属于 solo 命名空间。wolo 模式下前端不会请求这些路径。
2. **前端**：侧边栏 Health 入口**仅在 `appName === 'solo'` 时渲染**（见 plan Phase 8.1）；`/health` 路由懒加载，在 wolo 模式下导航到 `/health` 时重定向回 Dashboard（或显示"该页面仅 solo 可用"）。

> 不需要额外的 token gate 逻辑——`TokenGateMiddleware` 已全局保护所有 `/api/*` 端点，health 路由自动受保护。solo-only 是通过**命名空间隔离 + 前端条件渲染**实现的，而非运行时鉴权。

### 5.6 写操作的隐私权衡（DELETE / PATCH）

设计 §6 承诺"用户可编辑/删除健康记录"，因此提供 `DELETE` / `PATCH` 端点。但健康数据敏感，需做如下约束：

- **软删除优先**：DELETE 物理删除（health_records 表数据量小、且为本地库，物理删除可接受）。若后续需要审计，可加 `deleted_at` 软删字段（v1.1 演进）。
- **不可批量删**：只支持按 `{id}` 删单条，不提供"清空全部"端点，防误操作。
- **PATCH 受限字段**：只允许更新业务字段（severity/status/description/dosage/frequency 等），不允许改 `id`/`subject`（改 subject 等于换人，应由删除+新建完成）。`update_health_record` 已用 `allowed = COLUMNS - {"id"}` 实现，PATCH handler 需在此基础上再排除 `subject`。
- **前端二次确认**：删除前弹确认框（与删除 record/todo 的交互一致）。

---

## 6. 数据安全与隐私

### 6.1 原则

健康数据属于敏感个人信息：

1. **仅本地存储** -- `health_records` 表在用户本地 SQLite 中，不上传任何远程服务器
2. **仅本地访问** -- Onboard 仅监听 localhost
3. **无额外采集** -- 所有数据来自用户主动发送的消息，由 agent 结构化入库
4. **用户可控** -- 用户可通过 Onboard 页面删除或编辑任何健康记录（§5.6）

### 6.2 实现措施

- Health API 不引入任何外部 API 调用
- 不在前端 localStorage 中缓存健康数据
- Token Gate 中间件已保护所有 API 端点（`TokenGateMiddleware` 全局生效）
- Solo-only 隔离见 §5.5（命名空间 + 前端条件渲染）

---

## 7. 实现与演进注意事项

### 7.1 后续演进路线

#### v1.1
- **健康报告**：基于结构化数据生成月度健康报告（复用 Reports 模块），支持按主体生成
- **健康目标追踪**：设定运动/睡眠目标并追踪达成率（可按主体分别设定）
- **家庭成员档案**：从 health_records 中的 subject 自动构建家庭成员列表，关联 memory 中的 family_members.md
- **软删除**：health_records 增加 `deleted_at` 字段，DELETE 改为软删，支持审计与恢复

#### v1.2
- **AI 健康洞察**：agent 定期分析健康记录，主动发现模式（如"你最近两周睡眠明显变差"、"图图这个月去医院次数比上月多"）
- **健康数据导入**：Apple Health / Google Fit 数据批量导入到 `health_records` 表（subject=self）
- **家庭健康对比**：在同一图表中对比不同主体的同类指标趋势（如全家睡眠质量对比）
- **subject 别名合并**：支持把"妈妈"和"老妈"合并为同一主体

#### v2.0
- **医疗知识图谱**：关联症状→可能原因→常用药物→就诊建议
- **健康预警**：当指标异常时（如连续失眠、症状加重），通过 gateway 推送提醒（区分主体推送）
- **儿童成长曲线**：基于 subject=图图 的 vital 类记录，绘制身高/体重/发育里程碑成长曲线

### 7.2 性能与扩展性

- 所有统计查询走索引（date / subject / category / status / record_id）。
- `list_health_records` 的 subject 过滤必须下推 SQL，service 层不做全量拉取后内存过滤。
- `health_overview` 中"近期 7 天/30 天"统计应直接用 `date_from` 参数下推，而非 `limit=500` 后 Python 筛选。

### 7.3 兼容性

- Schema 迁移幂等（`CREATE TABLE IF NOT EXISTS`），对已有库安全。
- `metrics_json` 解析失败时通过 `SoloHealthRecord.metrics` property 兜底为 `{}`，不影响聚合。
- 自定义 category（非标准 8 类）在图表上 fallback 到默认 icon/color（不报错）。

### 7.4 时间一致性

- `health_records.date` 与 `solo_record` 的 date **同源**：都用本地日期（`_now()[:10]`），用户不传 date 时默认今天本地日期。
- 后端聚合的"近 7 天/30 天"边界用本地日期计算（`datetime.now()`，与服务进程时区一致），与 records 模块现有统计口径保持一致。
- 若未来 Onboard 服务部署在非用户时区的服务器，需统一改为带时区的 now（参见 records 模块的同类问题），本版本沿用现有口径。

---

## 8. 设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 数据入库方式 | 后提取 vs 写时结构化 | 写时结构化 | 数据质量高、实时可查、无需后处理 |
| 存储位置 | 独立数据库 vs SoloStore 新表 | SoloStore 新表 | 复用现有连接/迁移机制、与 records 表通过 record_id 强关联 |
| 工具模式 | 独立工具 vs solo_record 扩展字段 | 独立工具 `solo_health_record` | 关注点分离、可独立查询、不影响 records 表结构 |
| 调用时机 | 后处理 vs 同一轮伴随调用 | 同一轮伴随调用 | 与 solo_remember 模式一致、无额外 LLM 调用开销 |
| 类别体系 | 硬编码枚举 vs 推荐类别+约束 | 推荐 8 类 + 受约束的新类别 | 硬编码缺乏灵活性，完全自由又容易发散；推荐类别+命名约束+去重检查兼顾灵活与可控 |
| 扩展字段 | 多列 vs JSON 扩展 | 专属列 + metrics_json 兜底 | 高频字段直接查询、低频/多变字段用 JSON |
| 与 Memory 关系 | 替代 vs 互补 | 互补 | Memory 存长效事实（"我有鼻炎"），health_records 存事件（"今天鼻炎发作了"） |
| 多主体支持 | 仅记录用户自己 vs 支持家庭成员 | 支持多主体（subject 字段） | 用户经常记录子女/配偶的健康事件（就诊、症状、用药），必须区分主体才能按人过滤统计 |
| subject 过滤位置 | service 层内存过滤 vs SQL 下推 | **SQL 下推** | 数据量增长后内存过滤性能差，且 subject 索引已建好 |
| record_id 关联 | LLM 传值 vs 系统回填 vs 不关联 | **系统强制回填（必填）** | LLM 无法获知同轮 record id；编排层 `post_turn_backfill()` 在工具并发执行完毕后自动回填；所有写入路径（含 UI 手动录入）都先经过 `solo_record`，确保每条 health_record 有源可溯 |
| 适用范围 | solo+wolo vs 仅 solo | **仅 solo** | 健康数据属于个人隐私范畴，wolo 是工作日志，不涉及身体健康；solo 是个人生活日志，健康数据天然属于 solo |
| 写操作 | 只读 vs 可删改 | 可删改（受限） | 用户需能修正/删除健康记录；但限制单条操作、禁止改 subject、前端二次确认，平衡可控性与安全性 |
| 工具返回格式 | 返回 id vs 仅 message | **仅 message** | 与 `_handle_remember` 等现有工具的成功返回风格一致，id 是内部标识无需暴露给 LLM |
