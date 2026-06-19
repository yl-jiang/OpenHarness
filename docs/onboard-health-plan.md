# Onboard Health Page -- 实施计划

> 基于 [onboard-health-design.md](./onboard-health-design.md) 的分步实施计划。
> 核心架构：**结构化健康数据库 + 专用 Agent 工具 + 提示词优化 + Onboard 可视化页面**。
> **注意：Health 模块仅适用于 solo，不适用于 wolo。**
>
> 本计划假设当前代码库尚未实现任何 health 相关内容，从零开始按 Phase 顺序实施。

---

## Phase 1: 数据模型与数据库表

### 目标

在 `SoloStore` 中新增 `health_records` 结构化表和对应的 dataclass 模型。

### 步骤

#### 1.1 新增 SoloHealthRecord 数据模型

**文件**: `solo/core/models.py`

在文件中（`SoloExperiment` 之后、`ProcessResult` 之前）添加：

```python
@dataclass(frozen=True)
class SoloHealthRecord:
    """One structured health record."""

    id: str
    record_id: str = ""
    date: str = ""
    subject: str = "self"       # self|图图|明月|... (健康记录主体)
    category: str = ""          # 健康类别（优先使用推荐类别，允许受约束的新类别）
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
        """统一解析 metrics_json。解析失败或非 dict 时返回 {}。"""
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

> 注意：`metrics` property 统一了 metrics_json 解析，后端所有聚合方法都走 `r.metrics`，不再各自 try/except。确认 `models.py` 顶部已 `import json`（项目其他 dataclass 已用到，通常已存在）。

#### 1.2 新增数据库表 DDL

**文件**: `solo/core/store.py`

在 `_SCHEMA_SQL` 中新增 `health_records` 建表语句（幂等，挂在主 schema 里随新库一起建）：

```sql
CREATE TABLE IF NOT EXISTS health_records (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT 'self',
    category TEXT NOT NULL,
    item TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    body_part TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    medication_name TEXT NOT NULL DEFAULT '',
    dosage TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    duration TEXT NOT NULL DEFAULT '',
    exercise_type TEXT NOT NULL DEFAULT '',
    exercise_duration_min INTEGER NOT NULL DEFAULT 0,
    exercise_intensity TEXT NOT NULL DEFAULT '',
    sleep_hours REAL NOT NULL DEFAULT 0,
    sleep_quality TEXT NOT NULL DEFAULT '',
    mood TEXT NOT NULL DEFAULT '',
    stress_level TEXT NOT NULL DEFAULT '',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'agent',
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

#### 1.3 Schema 版本升级与迁移

**文件**: `solo/core/store.py`

1. 将 `_SCHEMA_VERSION` 从 `6` 提升到 `7`
2. 在 `_apply_migrations()` 末尾添加**幂等**迁移（沿用项目现有的 `executescript + CREATE TABLE IF NOT EXISTS` 模式，**不要**用 `_table_exists()` 探测）：

```python
def _apply_migrations(self) -> None:
    # ... existing migrations ...

    # v7: health_records table（幂等：CREATE TABLE IF NOT EXISTS，安全可重入）
    self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS health_records (
            id TEXT PRIMARY KEY,
            record_id TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT 'self',
            category TEXT NOT NULL,
            item TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            body_part TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            medication_name TEXT NOT NULL DEFAULT '',
            dosage TEXT NOT NULL DEFAULT '',
            frequency TEXT NOT NULL DEFAULT '',
            duration TEXT NOT NULL DEFAULT '',
            exercise_type TEXT NOT NULL DEFAULT '',
            exercise_duration_min INTEGER NOT NULL DEFAULT 0,
            exercise_intensity TEXT NOT NULL DEFAULT '',
            sleep_hours REAL NOT NULL DEFAULT 0,
            sleep_quality TEXT NOT NULL DEFAULT '',
            mood TEXT NOT NULL DEFAULT '',
            stress_level TEXT NOT NULL DEFAULT '',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            tags TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'agent',
            linked_memory_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_health_records_date ON health_records(date);
        CREATE INDEX IF NOT EXISTS idx_health_records_subject ON health_records(subject);
        CREATE INDEX IF NOT EXISTS idx_health_records_category ON health_records(category);
        CREATE INDEX IF NOT EXISTS idx_health_records_status ON health_records(status);
        CREATE INDEX IF NOT EXISTS idx_health_records_record_id ON health_records(record_id);
    """)
    self._conn.commit()
```

> **不要**照搬初稿里 `if not self._table_exists("health_records")` 的写法——项目里没有这个方法，且 `IF NOT EXISTS` 已经幂等，加探测反而多余。

### 验证

```bash
# 删除旧的测试数据库，让新 schema 从头创建
rm -f /tmp/test_solo_store.db

uv run pytest tests/test_solo/test_store.py -v -k "health or migration"
```

---

## Phase 2: Store CRUD 方法

### 目标

在 `SoloStore` 中实现 `health_records` 表的增删改查方法。**重点是 `list_health_records` 必须支持 `subject` 过滤并下推到 SQL。**

### 步骤

#### 2.1 新增 Store 方法

**文件**: `solo/core/store.py`

```python
# ── Health records ──────────────────────────────────────────

_HEALTH_RECORD_COLUMNS = [
    "id", "record_id", "date", "subject", "category", "item", "description",
    "body_part", "severity", "status", "medication_name", "dosage",
    "frequency", "duration", "exercise_type", "exercise_duration_min",
    "exercise_intensity", "sleep_hours", "sleep_quality", "mood",
    "stress_level", "metrics_json", "tags", "source", "linked_memory_id",
    "created_at", "updated_at",
]

def _health_record_to_row(self, record: SoloHealthRecord) -> tuple[list[str], list[Any]]:
    cols = list(self._HEALTH_RECORD_COLUMNS)
    vals = [getattr(record, c) for c in cols]
    return cols, vals

def _row_to_health_record(row: tuple) -> SoloHealthRecord:
    return SoloHealthRecord(
        id=row[0], record_id=row[1], date=row[2], subject=row[3],
        category=row[4], item=row[5], description=row[6], body_part=row[7],
        severity=row[8], status=row[9], medication_name=row[10],
        dosage=row[11], frequency=row[12], duration=row[13],
        exercise_type=row[14], exercise_duration_min=row[15],
        exercise_intensity=row[16], sleep_hours=row[17],
        sleep_quality=row[18], mood=row[19], stress_level=row[20],
        metrics_json=row[21], tags=row[22], source=row[23],
        linked_memory_id=row[24], created_at=row[25], updated_at=row[26],
    )

def add_health_record(self, record: SoloHealthRecord) -> None:
    cols, vals = self._health_record_to_row(record)
    placeholders = ", ".join("?" * len(vals))
    self._db.execute(
        f"INSERT INTO health_records ({', '.join(cols)}) VALUES ({placeholders})", vals
    )
    self._db.commit()

def list_health_records(
    self, *,
    subject: str | None = None,        # ← 必需：多主体过滤下推 SQL
    category: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
) -> list[SoloHealthRecord]:
    clauses: list[str] = []
    params: list[Any] = []
    if subject:
        clauses.append("subject = ?"); params.append(subject)
    if category:
        clauses.append("category = ?"); params.append(category)
    if status:
        clauses.append("status = ?"); params.append(status)
    if date_from:
        clauses.append("date >= ?"); params.append(date_from)
    if date_to:
        clauses.append("date <= ?"); params.append(date_to)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "ORDER BY date DESC, created_at DESC"
    if limit is not None:
        cur = self._db.execute(
            f"SELECT * FROM health_records{where} {order} LIMIT ?",
            params + [limit],
        )
    else:
        cur = self._db.execute(
            f"SELECT * FROM health_records{where} {order}", params
        )
    return [self._row_to_health_record(r) for r in cur.fetchall()]

def get_health_record(self, record_id: str) -> SoloHealthRecord | None:
    cur = self._db.execute("SELECT * FROM health_records WHERE id = ?", (record_id,))
    row = cur.fetchone()
    return self._row_to_health_record(row) if row else None

def update_health_record(self, record_id: str, **fields: Any) -> bool:
    # 不允许通过 update 改 id；subject 也不在此处限制（PATCH handler 层另行排除）
    allowed = set(self._HEALTH_RECORD_COLUMNS) - {"id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    sets = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [record_id]
    cursor = self._db.execute(
        f"UPDATE health_records SET {sets} WHERE id = ?", values
    )
    self._db.commit()
    return cursor.rowcount > 0

def delete_health_record(self, record_id: str) -> bool:
    cursor = self._db.execute("DELETE FROM health_records WHERE id = ?", (record_id,))
    self._db.commit()
    return cursor.rowcount > 0

def health_record_categories(self) -> dict[str, int]:
    rows = self._db.execute(
        "SELECT category, COUNT(*) FROM health_records GROUP BY category"
    ).fetchall()
    return {row[0]: row[1] for row in rows}

def health_record_subjects(self) -> dict[str, int]:
    """返回 {subject: count}，如 {'self': 31, '图图': 13, '明月': 2}。
    供前端 SubjectFilter 与 /api/solo/health/subjects 端点使用。"""
    rows = self._db.execute(
        "SELECT subject, COUNT(*) FROM health_records GROUP BY subject"
    ).fetchall()
    return {row[0]: row[1] for row in rows}
```

> **关键变更（相对初稿）**：`list_health_records` 新增 `subject` 参数并下推 SQL（走 `idx_health_records_subject` 索引）；新增 `health_record_subjects()` 方法。这两处是"多主体统计链路"能正常工作的基础。

#### 2.2 导入更新

**文件**: `solo/core/store.py`

在文件顶部的 `from solo.core.models import (...)` 中添加 `SoloHealthRecord`。

### 验证

```bash
uv run pytest tests/test_solo/test_store.py -v -k "health"
```

测试用例（Phase 10 详列）必须覆盖：subject 过滤、空表聚合、date 范围边界、update 排除 id。

---

## Phase 3: Agent 工具 -- `solo_health_record`

### 目标

实现 `solo_health_record` 工具定义和处理器，注册到 `SoloToolRegistry`。

### 步骤

#### 3.1 工具定义

**文件**: `solo/tools.py`

在 `_tool_remember()` 附近添加（参数列表**不含 record_id**，见设计 §2.6）：

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
            # record_id 不暴露给 LLM，由编排层 best-effort 回填（设计 §2.6）
        ],
    )
```

#### 3.2 工具处理器

**文件**: `solo/tools.py`

在 `SoloToolRegistry` 类中添加。**返回格式与 `_handle_remember` 对齐：成功只返回 `ok + message`，不返回 id。**

```python
async def _handle_health_record(self, arguments: dict[str, Any]) -> dict[str, Any]:
    category = _required_text(arguments, "category")
    item = _required_text(arguments, "item")

    # Validate category: prefer standard categories, allow new ones with constraints
    STANDARD_CATEGORIES = {"medical", "symptom", "medication", "fitness", "sleep", "nutrition", "mental", "vital"}
    VAGUE_NAMES = {"other", "misc", "general", "unknown", "custom", "test"}

    if category not in STANDARD_CATEGORIES:
        if not category.isalpha() or not category.islower() or len(category) > 20:
            return {"ok": False, "error": f"Invalid category '{category}'. Use a standard category or a single lowercase English word."}
        if category in VAGUE_NAMES:
            return {"ok": False, "error": f"Category '{category}' is too vague. Use a descriptive name."}

    local_today = _now()[:10]  # 与 solo_record 同源的本地日期
    subject_raw = str(arguments.get("subject") or "self").strip()
    record = SoloHealthRecord(
        id=uuid4().hex[:12],
        record_id=str(arguments.get("record_id") or ""),  # 通常为空，由编排层回填
        date=str(arguments.get("date") or local_today),
        subject=subject_raw or "self",
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
    # 返回格式与 _handle_remember 等对齐：ok + message（不返回 id）
    return {"ok": True, "message": f"健康记录已入库：{category}/{item} ({record.date})"}
```

> **返回契约**：成功 `{"ok": True, "message": ...}`，失败 `{"ok": False, "error": ...}`。与 `_handle_remember` / `_handle_add_todo` 风格一致。初稿里 `return {"ok": True, "id": record.id, ...}` 会引入与现有工具不一致的返回结构，已去掉。

#### 3.3 注册工具

**文件**: `solo/tools.py`

在 `SoloToolRegistry.tools()` 列表中添加（在 `_tool_remember` 附近）：

```python
SoloDomainTool(_tool_health_record(), self._handle_health_record),
```

#### 3.4 导入更新

**文件**: `solo/tools.py`

在顶部的 `from solo.core.models import (...)` 中添加 `SoloHealthRecord`。

### 验证

```bash
# 单元级：直接调用 handler
uv run pytest tests/test_solo/test_tools.py -v -k "health"

# 集成级：通过 agent 发送包含健康信息的消息，验证 solo_health_record 被调用
uv run pytest tests/test_solo/ -v -k "health" --timeout=120
```

---

## Phase 4: 辅助查询工具 -- `solo_health_summary`

### 目标

让 agent 能查询健康历史，用于回答用户的健康相关问题。**必须支持 `subject` 参数**，否则无法回答"图图最近吃药情况"。

### 步骤

#### 4.1 工具定义

**文件**: `solo/tools.py`

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

#### 4.2 工具处理器

```python
async def _handle_health_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timedelta

    subject = str(arguments.get("subject") or "").strip() or None
    category = str(arguments.get("category") or "").strip() or None
    days = int(arguments.get("days") or 30)
    status = str(arguments.get("status") or "").strip() or None
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    records = self.store.list_health_records(
        subject=subject, category=category, status=status, date_from=date_from,
    )

    if not records:
        subj_desc = f" ({subject}) " if subject else " "
        return {
            "ok": True,
            "total": 0,
            "message": f"过去 {days} 天{subj_desc}没有{' (' + category + ')' if category else ''}健康记录。",
        }

    from collections import Counter
    cat_counts = Counter(r.category for r in records)
    subj_counts = Counter(r.subject for r in records)
    items_summary = Counter(r.item for r in records).most_common(10)
    recent = [r.to_dict() for r in records[:10]]

    return {
        "ok": True,
        "total": len(records),
        "days": days,
        "subject_filter": subject,
        "category_filter": category,
        "by_category": dict(cat_counts),
        "by_subject": dict(subj_counts),
        "top_items": [{"item": item, "count": cnt} for item, cnt in items_summary],
        "recent_records": recent,
    }
```

#### 4.3 注册

```python
SoloDomainTool(_tool_health_summary(), self._handle_health_summary),
```

### 验证

```bash
uv run pytest tests/test_solo/test_tools.py -v -k "health_summary"
```

---

## Phase 5: 提示词优化

### 目标

更新 solo agent 的系统提示词，使其能主动检测并调用 `solo_health_record`。

### 步骤

#### 5.1 更新工具路由决策表

**文件**: `solo/prompts.py`

在工具路由决策表中新增：

```
| 用户提到身体健康相关内容（症状、用药、运动、睡眠、饮食、心理状态、体检、体征数据） | → solo_health_record（同一轮与 solo_record 并行调用） |
```

#### 5.2 新增"健康记录提取原则"章节

在 `solo/prompts.py` 的 `## 长效事实提取原则` 章节之后，新增平级章节（完整内容见设计文档 §4.2）：

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
| "今天跑了5公里" | self | fitness | 跑步 | exercise_type=跑步, exercise_duration_min≈30, exercise_intensity=moderate |
| "头疼了一整天" | self | symptom | 头疼 | body_part=头, severity=moderate, duration=一整天 |
| "吃了布洛芬止痛" | self | medication | 布洛芬 | medication_name=布洛芬, dosage=1颗 |
| "昨晚睡了8小时，质量不错" | self | sleep | 睡眠 | sleep_hours=8, sleep_quality=good |
| "今天情绪很低落" | self | mental | 情绪低落 | mood=低落 |
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
4. `item` 使用中文，简明扼要
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

#### 5.3 更新 `solo_record` 工具描述的 SIDE-EFFECT CHECK

**文件**: `solo/tools.py`

在 `_tool_record()` 的 description 末尾的 `SIDE-EFFECT CHECK` 中追加（保留原有 solo_remember 部分，在其后追加 health 部分）：

```
If this message contains health-related events (symptoms, medication, exercise, sleep,
mood changes, medical visits, vital signs), also call solo_health_record in the SAME turn —
once per distinct health event. Health info may be mentioned INCIDENTALLY as a side note
(e.g. "明月没去游乐场，因为她去体检了" → extract 明月's medical visit). Set the `subject`
parameter correctly — default "self"; use the family member's name (e.g. "图图") if the
event is about them.
```

### 验证

```bash
# 确认 prompt 加载无报错
uv run python -c "from solo.prompts import build_system_prompt; print(len(build_system_prompt()))"
```

---

## Phase 6: Onboard 后端 API

### 目标

在 Onboard 后端新增 Health API 路由，直接查询 `health_records` 结构化表。**所有端点支持 `subject` 过滤（下推 SQL），提供只读 + 受限写操作。**

### 步骤

#### 6.1 扩展 SoloService

**文件**: `onboard/services/solo_service.py`

添加健康数据查询方法（统一走 `r.metrics` 解析，subject 过滤全部下推）：

```python
# ── Health records ──────────────────────────────────────────

def health_subjects(self) -> dict[str, int]:
    """所有主体及计数，供 SubjectFilter 渲染。"""
    return self.store.health_record_subjects()

def health_overview(self, subject: str | None = None) -> dict[str, Any]:
    from collections import Counter
    from datetime import datetime, timedelta

    # by_subject / by_category 始终全量（用于主体列表和类别全景）
    by_subject = self.store.health_record_subjects()
    categories = self.store.health_record_categories()
    total = sum(categories.values())

    # 近期统计受 subject 过滤
    cutoff_7d = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = self.store.list_health_records(subject=subject, date_from=cutoff_7d)
    cutoff_30d = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    active_meds = len(self.store.list_health_records(
        subject=subject, category="medication", status="active"))
    active_symptoms = len(self.store.list_health_records(
        subject=subject, category="symptom", status="active"))

    sleep_records = self.store.list_health_records(
        subject=subject, category="sleep", date_from=cutoff_30d)
    avg_sleep = (
        sum(r.sleep_hours for r in sleep_records) / len(sleep_records)
        if sleep_records else 0
    )

    fitness_7d = len([r for r in recent if r.category == "fitness"])

    return {
        "total_records": total,
        "by_category": categories,
        "by_subject": dict(by_subject),       # 始终全量主体列表
        "subject_filter": subject,            # 当前过滤的主体（None=全部）
        "recent_7d_count": len(recent),
        "active_medications": active_meds,
        "active_symptoms": active_symptoms,
        "avg_sleep_hours_30d": round(avg_sleep, 1),
        "fitness_count_7d": fitness_7d,
    }

def list_health_records(
    self, *, subject=None, category=None, status=None, date_from=None, date_to=None,
    limit=20, offset=0,
) -> dict[str, Any]:
    # 注意：分页在 SQL 层无法直接 offset，这里先取全量受过滤结果再切片
    # （健康记录数据量小，可接受；若后续量大，应在 store 层加 OFFSET 支持）
    records = self.store.list_health_records(
        subject=subject, category=category, status=status,
        date_from=date_from, date_to=date_to,
    )
    total = len(records)
    page = records[offset:offset + limit]
    return {
        "items": [r.to_dict() for r in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }

def health_fitness_trend(self, subject: str | None = None, days: int = 30) -> dict[str, Any]:
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_health_records(
        subject=subject, category="fitness", date_from=cutoff)
    by_date: dict[str, list] = {}
    for r in records:
        by_date.setdefault(r.date, []).append(r)
    daily = []
    for d, recs in sorted(by_date.items()):
        total_min = sum(r.exercise_duration_min for r in recs)
        daily.append({
            "date": d,
            "session_count": len(recs),
            "total_minutes": total_min,
            "types": list({r.exercise_type for r in recs if r.exercise_type}),
        })
    return {"daily": daily, "total_sessions": len(records)}

def health_sleep_trend(self, subject: str | None = None, days: int = 30) -> dict[str, Any]:
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_health_records(
        subject=subject, category="sleep", date_from=cutoff)
    daily = [{"date": r.date, "hours": r.sleep_hours, "quality": r.sleep_quality} for r in records]
    avg = sum(r.sleep_hours for r in records) / len(records) if records else 0
    return {"daily": daily, "avg_hours": round(avg, 1), "total_nights": len(records)}

def health_symptom_ranking(self, subject: str | None = None, days: int = 90) -> dict[str, Any]:
    from datetime import datetime, timedelta
    from collections import Counter
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_health_records(
        subject=subject, category="symptom", date_from=cutoff)
    by_item = Counter(r.item for r in records)
    by_body_part = Counter(r.body_part for r in records if r.body_part)
    by_severity = Counter(r.severity for r in records if r.severity)
    return {
        "by_item": [{"item": k, "count": v} for k, v in by_item.most_common(20)],
        "by_body_part": [{"body_part": k, "count": v} for k, v in by_body_part.most_common(15)],
        "by_severity": dict(by_severity),
        "total": len(records),
    }

def health_medications(self, subject: str | None = None, days: int = 90) -> dict[str, Any]:
    from datetime import datetime, timedelta
    from collections import Counter
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_health_records(
        subject=subject, category="medication", date_from=cutoff)
    active = self.store.list_health_records(
        subject=subject, category="medication", status="active")
    by_name = Counter(r.medication_name or r.item for r in records)
    return {
        "active": [r.to_dict() for r in active],
        "usage": [{"name": k, "count": v} for k, v in by_name.most_common(20)],
        "total": len(records),
    }

def health_mental_trend(self, subject: str | None = None, days: int = 30) -> dict[str, Any]:
    from datetime import datetime, timedelta
    from collections import Counter
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_health_records(
        subject=subject, category="mental", date_from=cutoff)
    daily = [{"date": r.date, "mood": r.mood, "stress": r.stress_level, "description": r.description} for r in records]
    mood_dist = Counter(r.mood for r in records if r.mood)
    stress_dist = Counter(r.stress_level for r in records if r.stress_level)
    return {
        "daily": daily,
        "mood_distribution": dict(mood_dist),
        "stress_distribution": dict(stress_dist),
        "total": len(records),
    }

def health_vitals(self, subject: str | None = None, days: int = 90) -> dict[str, Any]:
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    records = self.store.list_health_records(
        subject=subject, category="vital", date_from=cutoff)
    # 统一走 r.metrics property 解析，不重复 try/except
    daily = [{"date": r.date, "metrics": r.metrics, "item": r.item} for r in records]
    return {"daily": daily, "total": len(records)}

def health_timeline(self, subject: str | None = None, limit: int = 30, offset: int = 0) -> dict[str, Any]:
    records = self.store.list_health_records(subject=subject)
    total = len(records)
    page = records[offset:offset + limit]
    icon_map = {
        "medical": "🏥", "symptom": "🤧", "medication": "💊",
        "fitness": "🏃", "sleep": "😴", "nutrition": "🥗",
        "mental": "🧠", "vital": "📊",
    }
    items = [{
        "date": r.date, "category": r.category,
        "icon": icon_map.get(r.category, "♡"),  # 自定义类别 fallback
        "subject": r.subject,
        "item": r.item, "description": r.description,
        "severity": r.severity, "status": r.status, "id": r.id,
    } for r in page]
    return {"items": items, "total": total, "limit": limit, "offset": offset}

# ── 写操作（受限，见设计 §5.6） ───────────────────────────

def delete_health_record(self, record_id: str) -> bool:
    return self.store.delete_health_record(record_id)

def update_health_record(self, record_id: str, updates: dict[str, Any]) -> bool:
    # 不允许通过 PATCH 改 subject（换人应删后新建）
    safe = {k: v for k, v in updates.items() if k != "subject"}
    return self.store.update_health_record(record_id, **safe)
```

#### 6.2 创建 Health API 路由

**文件**: `onboard/api/health.py`

路由挂在 `/api/solo/health` 前缀（**沿用 solo 命名空间，天然 wolo 隔离**，见设计 §5.5）：

```python
"""Health API routes (solo-only)."""

from fastapi import APIRouter, Query

from onboard.services.solo_service import SoloService

router = APIRouter(prefix="/api/solo/health", tags=["health"])


def _service() -> SoloService:
    return SoloService()


@router.get("/subjects")
def health_subjects():
    return {"subjects": _service().health_subjects()}


@router.get("/overview")
def health_overview(subject: str | None = None):
    return _service().health_overview(subject=subject)


@router.get("/records")
def health_records(
    subject: str | None = None,
    category: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _service().list_health_records(
        subject=subject, category=category, status=status,
        date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )


@router.get("/fitness")
def health_fitness(subject: str | None = None, days: int = Query(30, ge=1, le=365)):
    return _service().health_fitness_trend(subject=subject, days=days)


@router.get("/sleep")
def health_sleep(subject: str | None = None, days: int = Query(30, ge=1, le=365)):
    return _service().health_sleep_trend(subject=subject, days=days)


@router.get("/symptoms")
def health_symptoms(subject: str | None = None, days: int = Query(90, ge=1, le=365)):
    return _service().health_symptom_ranking(subject=subject, days=days)


@router.get("/medications")
def health_medications(subject: str | None = None, days: int = Query(90, ge=1, le=365)):
    return _service().health_medications(subject=subject, days=days)


@router.get("/mental")
def health_mental(subject: str | None = None, days: int = Query(30, ge=1, le=365)):
    return _service().health_mental_trend(subject=subject, days=days)


@router.get("/vitals")
def health_vitals(subject: str | None = None, days: int = Query(90, ge=1, le=365)):
    return _service().health_vitals(subject=subject, days=days)


@router.get("/timeline")
def health_timeline(
    subject: str | None = None,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return _service().health_timeline(subject=subject, limit=limit, offset=offset)


# ── 写操作（受限）─────────────────────────────────────────

@router.delete("/records/{record_id}")
def delete_health_record(record_id: str):
    ok = _service().delete_health_record(record_id)
    return {"ok": ok}


@router.patch("/records/{record_id}")
def update_health_record(record_id: str, updates: dict):
    ok = _service().update_health_record(record_id, updates)
    return {"ok": ok}
```

#### 6.3 注册路由

**文件**: `onboard/server.py`

在 `create_app()` 中添加（与现有 `solo_routes` 同级）：

```python
from onboard.api import chat, health, lifecycle, solo_routes, stats, wolo_routes  # 新增 health

# 在 include_router 区块中添加:
app.include_router(health.router)
```

> Token Gate 由全局 `TokenGateMiddleware` 自动保护；solo-only 通过 `/api/solo` 前缀命名空间隔离，无需额外鉴权逻辑。

### 验证

```bash
uv run onboard run --reload &
curl -s http://localhost:8090/api/solo/health/subjects | python -m json.tool
curl -s "http://localhost:8090/api/solo/health/overview?subject=self" | python -m json.tool
curl -s "http://localhost:8090/api/solo/health/records?subject=图图" | python -m json.tool
curl -s "http://localhost:8090/api/solo/health/timeline?subject=self" | python -m json.tool
# 写操作
curl -s -X PATCH http://localhost:8090/api/solo/health/records/<id> \
     -H "Content-Type: application/json" -d '{"severity":"moderate"}' | python -m json.tool
```

---

## Phase 7: Onboard 前端 -- 类型定义与 API 客户端

### 目标

在前端添加 TypeScript 类型和 API 方法。**每个 health 方法都必须接受 `subject?` 参数。**

### 步骤

#### 7.1 扩展类型定义

**文件**: `onboard/frontend/src/api/types.ts`

```typescript
// ── Health ─────────────────────────────────────────────────

export type HealthCategory = 'medical' | 'symptom' | 'medication' | 'fitness' | 'sleep' | 'nutrition' | 'mental' | 'vital';
export type HealthSeverity = 'mild' | 'moderate' | 'severe' | '';
export type HealthStatus = 'active' | 'resolved' | 'chronic' | 'recurring';

export interface SoloHealthRecord {
  id: string;
  record_id: string;
  date: string;
  subject: string;  // 'self' | '图图' | '明月' | ... (健康记录主体)
  category: HealthCategory;
  item: string;
  description: string;
  body_part: string;
  severity: HealthSeverity;
  status: HealthStatus;
  medication_name: string;
  dosage: string;
  frequency: string;
  duration: string;
  exercise_type: string;
  exercise_duration_min: number;
  exercise_intensity: string;
  sleep_hours: number;
  sleep_quality: string;
  mood: string;
  stress_level: string;
  metrics_json: string;
  tags: string;
  source: string;
  linked_memory_id: string;
  created_at: string;
  updated_at: string;
}

export interface HealthOverview {
  total_records: number;
  by_category: Record<string, number>;
  by_subject: Record<string, number>;  // 始终全量：{'self': 31, '图图': 13, '明月': 2}
  subject_filter: string | null;       // 当前过滤主体
  recent_7d_count: number;
  active_medications: number;
  active_symptoms: number;
  avg_sleep_hours_30d: number;
  fitness_count_7d: number;
}

export interface FitnessDay {
  date: string;
  session_count: number;
  total_minutes: number;
  types: string[];
}

export interface SleepDay {
  date: string;
  hours: number;
  quality: string;
}

export interface HealthTimelineItem {
  date: string;
  category: HealthCategory;
  icon: string;
  subject: string;
  item: string;
  description: string;
  severity: string;
  status: string;
  id: string;
}
```

#### 7.2 扩展 API 客户端

**文件**: `onboard/frontend/src/api/client.ts`

**每个方法都接受 `subject?: string`**，通过 query 传给后端，实现 SubjectFilter 联动：

```typescript
// ── Health (solo-only) ──────────────────────────────────────
health: {
  subjects: () =>
    request<{ subjects: Record<string, number> }>(`/api/solo/health/subjects`),
  overview: (subject?: string) =>
    request<HealthOverview>(`/api/solo/health/overview${query(subject ? { subject } : {})}`),
  records: (params: Record<string, QueryValue> = {}) =>
    request<PaginatedResponse<SoloHealthRecord>>(`/api/solo/health/records${query(params)}`),
  fitness: (subject?: string, days: number = 30) =>
    request<{ daily: FitnessDay[]; total_sessions: number }>(`/api/solo/health/fitness${query({ ...(subject ? { subject } : {}), days })}`),
  sleep: (subject?: string, days: number = 30) =>
    request<{ daily: SleepDay[]; avg_hours: number; total_nights: number }>(`/api/solo/health/sleep${query({ ...(subject ? { subject } : {}), days })}`),
  symptoms: (subject?: string, days: number = 90) =>
    request<{ by_item: { item: string; count: number }[]; by_body_part: { body_part: string; count: number }[]; by_severity: Record<string, number>; total: number }>(`/api/solo/health/symptoms${query({ ...(subject ? { subject } : {}), days })}`),
  medications: (subject?: string, days: number = 90) =>
    request<{ active: SoloHealthRecord[]; usage: { name: string; count: number }[]; total: number }>(`/api/solo/health/medications${query({ ...(subject ? { subject } : {}), days })}`),
  mental: (subject?: string, days: number = 30) =>
    request<{ daily: { date: string; mood: string; stress: string; description: string }[]; mood_distribution: Record<string, number>; stress_distribution: Record<string, number>; total: number }>(`/api/solo/health/mental${query({ ...(subject ? { subject } : {}), days })}`),
  vitals: (subject?: string, days: number = 90) =>
    request<{ daily: { date: string; metrics: Record<string, number>; item: string }[]; total: number }>(`/api/solo/health/vitals${query({ ...(subject ? { subject } : {}), days })}`),
  timeline: (subject?: string, params: Record<string, QueryValue> = {}) =>
    request<PaginatedResponse<HealthTimelineItem>>(`/api/solo/health/timeline${query({ ...(subject ? { subject } : {}), ...params })}`),
  delete: (id: string) =>
    request<{ ok: boolean }>(`/api/solo/health/records/${id}`, { method: 'DELETE' }),
  update: (id: string, updates: Partial<SoloHealthRecord>) =>
    request<{ ok: boolean }>(`/api/solo/health/records/${id}`, { method: 'PATCH', body: JSON.stringify(updates) }),
},
```

> **关键**：subject 参数贯穿所有读取方法，这是 SubjectFilter 切换主体后图表刷新的前提。`request` 函数对 `{}` query 的处理需兼容（传空对象应生成无 `?` 的 URL，确认现有 `query()` 辅助函数已支持）。

### 验证

```bash
cd onboard/frontend && npx tsc --noEmit
```

---

## Phase 8: Onboard 前端 -- 侧边栏与路由

### 步骤

#### 8.1 修改 Sidebar

**文件**: `onboard/frontend/src/components/Sidebar.tsx`

Health 仅适用于 solo，因此**仅在 `appName === 'solo'` 时渲染**。当前 Sidebar 的 items 构造方式是：

```typescript
const items = appName === 'wolo' ? [...commonItems, ...woloItems] : commonItems;
```

改为 solo 分支插入 Health 项（放在 Records 之后、Todos 之前）：

```typescript
const SOLO_HEALTH_ITEM = ['/health', '♡', 'Health'] as const;

// 在 Sidebar 组件中：
const items = appName === 'wolo'
  ? [...commonItems, ...woloItems]
  : [...commonItems.slice(0, 3), SOLO_HEALTH_ITEM, ...commonItems.slice(3)];
  // commonItems[0..2] = Dashboard, Entries, Records；插入 Health 后接 Todos 及之后
```

> `commonItems.slice(0,3)` 假设前 3 项是 Dashboard/Entries/Records，实施时按实际 `commonItems` 定义确认索引。

#### 8.2 注册路由

**文件**: `onboard/frontend/src/App.tsx`

```typescript
const Health = lazy(() => import('./pages/Health').then((m) => ({ default: m.Health })));

// 在 router children 中（与其他页面同级）:
{ path: 'health', element: <SuspenseLoader><Health /></SuspenseLoader> },
```

> wolo 模式下导航到 `/health`：由于侧边栏不显示入口，正常不会发生；若用户手动输 URL，可在 Health 页面内根据 `appName !== 'solo'` 显示"该页面仅 solo 可用"并重定向回 Dashboard（防御性处理，可选）。

### 验证

```bash
cd onboard/frontend && npx tsc --noEmit
# solo 模式下侧边栏出现 Health 入口；wolo 模式下不出现
```

---

## Phase 9: Onboard 前端 -- Health 页面

### 目标

实现完整的 Health 页面，包含统计卡片、各类别趋势图、时间线。**所有子组件接收并使用 `subject` prop。**

### 步骤

#### 9.1 主页面组件

**文件**: `onboard/frontend/src/pages/Health.tsx`

页面结构（复用 Dashboard 的 Section 模式和 StatsCard 组件）：

```
Zone 0: Subject Filter (主体选择器)
  - 水平标签栏：全部 | 自己(self) | 图图 | 明月 | ...
  - 从 api.health.subjects() 动态生成（'全部' + 各 subject）
  - selectedSubject 状态（默认 null=全部）贯穿所有 Zone

Zone 1: Stats Cards (4 cards)
  - 总记录数、本周运动次数、平均睡眠时长、活跃用药数
  - 数据来自 api.health.overview(selectedSubject)

Zone 2: Category Breakdown (堆叠柱状图)
  - 每日各类别记录数（受 selectedSubject 过滤）

Zone 3: 双列布局
  - 左: Fitness Trend  ← api.health.fitness(selectedSubject, days)
  - 右: Sleep Trend    ← api.health.sleep(selectedSubject, days)

Zone 4: 双列布局
  - 左: Symptom Tracker ← api.health.symptoms(selectedSubject, days)
  - 右: Medication List ← api.health.medications(selectedSubject, days)

Zone 5: 双列布局
  - 左: Mental Health   ← api.health.mental(selectedSubject, days)
  - 右: Vital Signs     ← api.health.vitals(selectedSubject, days)

Zone 6: Health Timeline ← api.health.timeline(selectedSubject, {limit})
  - 全类型混合时间线，支持分页
```

**数据加载策略**：`selectedSubject` 变化时，所有 Zone 的 useEffect 依赖触发重新拉取。可用 `Promise.all` 并行请求减少瀑布流。

#### 9.2 子组件

| 组件 | 文件 | 关键 props |
|------|------|-----------|
| `SubjectFilter` | `components/health/SubjectFilter.tsx` | `subjects: Record<string,num>`, `selected`, `onSelect` |
| `HealthStatsCards` | `components/health/HealthStatsCards.tsx` | `overview: HealthOverview` |
| `CategoryBreakdown` | `components/health/CategoryBreakdown.tsx` | `subject`, `days`（内部自取数据或由父组件传入数据） |
| `FitnessTrend` | `components/health/FitnessTrend.tsx` | `subject`, `days` |
| `SleepTrend` | `components/health/SleepTrend.tsx` | `subject`, `days` |
| `SymptomTracker` | `components/health/SymptomTracker.tsx` | `subject`, `days` |
| `MedicationList` | `components/health/MedicationList.tsx` | `subject`, `days` |
| `MentalHealthPanel` | `components/health/MentalHealthPanel.tsx` | `subject`, `days` |
| `VitalSignsChart` | `components/health/VitalSignsChart.tsx` | `subject`, `days` |
| `HealthTimeline` | `components/health/HealthTimeline.tsx` | `subject`, `limit` |

图表库沿用项目现有的 Recharts（与 Dashboard 一致）。自定义 category（非标准 8 类）在图表上 fallback 到默认 icon/color，不报错。

### 验证

```bash
cd onboard/frontend && npx tsc --noEmit
# 访问 /health 确认所有区域正确渲染
# 切换 SubjectFilter（全部→图图→明月）确认所有图表刷新
# 确认空数据时优雅降级（显示"暂无记录"而非空白）
# 确认 Solo/Wolo 切换时数据刷新（wolo 下不显示 Health）
```

---

## Phase 10: 测试与收尾

### 10.1 测试用例清单（必须补全）

当前 `tests/test_solo/` 下没有任何 health 测试，需新增 `tests/test_solo/test_health.py`（Store 层）并在 `test_tools.py` 中补 health 用例：

#### Store 层（test_store.py 或 test_health.py）
- `add_health_record` + `get_health_record` 往返一致
- `list_health_records` **subject 过滤**：插入 self/图图/明月 各若干条，验证 `subject="图图"` 只返回图图的
- `list_health_records` category/status/date_from/date_to/limit 组合过滤
- `list_health_records` 空表返回 `[]`
- `update_health_record`：可更新业务字段；尝试改 `id` 被忽略（`allowed - {"id"}`）
- `delete_health_record`：删除后 `get` 返回 None
- `health_record_categories` / `health_record_subjects` 计数正确
- schema 迁移幂等：对已存在 health_records 表的库重复跑迁移不报错

#### Tool handler 层（test_tools.py）
- `_handle_health_record` 合法标准类别成功，返回 `{"ok": True, "message": ...}`（**不含 id**）
- `_handle_health_record` 合法新类别（如 `dental`）成功
- `_handle_health_record` 拒绝 vague 名（`other`/`misc`）→ `{"ok": False, "error": ...}`
- `_handle_health_record` 拒绝非 alpha / 含数字 / 大写 / 超长类别
- `_handle_health_record` 非法 `metrics_json` 不崩（存入后 `r.metrics` 返回 `{}`）
- `_handle_health_summary` **subject 过滤**：传入 `subject="图图"` 只聚合图图的记录
- `_handle_health_summary` 空结果返回 `total: 0`
- `_handle_health_summary` by_category/by_subject 计数正确

#### 集成层（通过 agent）
- 消息"今天跑了5公里，膝盖疼，吃了布洛芬"触发**多次** `solo_health_record`（fitness + symptom + medication）
- 消息"带图图去游乐场，明月没去因为她去体检了"正确识别 **subject=明月**（隐含健康信息）
- 消息"我有过敏性鼻炎"走 `solo_remember` 而非 `solo_health_record`（长效事实 vs 事件）
- 消息"要注意健康了"不触发任何 health 工具（泛泛表述）

#### API 层（test_health_api.py 或手动）
- `GET /api/solo/health/subjects` 返回主体计数
- `GET /api/solo/health/records?subject=图图` 只返回图图的
- 各趋势端点带 `subject` 参数正确过滤
- `DELETE /api/solo/health/records/{id}` 删除成功
- `PATCH /api/solo/health/records/{id}` 不能改 subject（传 subject 被忽略）
- wolo 模式下 `/api/solo/health/*` 不被前端调用（命名空间隔离验证）

### 10.2 端到端验证

1. 通过 solo CLI 发送包含健康信息的消息，验证 agent 自动调用 `solo_health_record`
2. 通过 Onboard Health 页面验证数据正确展示
3. 验证 SubjectFilter 切换后所有区域刷新
4. 验证 Solo/Wolo 切换
5. 验证空数据场景

### 10.3 代码质量

```bash
uv run ruff check solo/ onboard/
cd onboard/frontend && npx tsc --noEmit
uv run pytest -q tests/test_solo/
```

### 10.4 CHANGELOG 更新

```markdown
### Added
- **Health module (solo-only)**: Structured `health_records` table in SoloStore for tracking symptoms, medications, exercise, sleep, nutrition, mental health, medical visits, and vital signs. Not available in wolo (work log doesn't cover personal health).
- **Multi-subject support**: Health records distinguish between user (self) and family members (e.g. children, spouse) via `subject` field, enabling per-person filtering and statistics (subject filter pushed down to SQL).
- **`solo_health_record` tool**: Agent automatically extracts health events from user messages into the structured health database (same-turn pattern with `solo_record`), correctly identifying subject for family member health events.
- **`solo_health_summary` tool**: Agent can query health history (with subject filter) to answer health-related questions.
- **Onboard Health page (solo-only)**: New sidebar page (visible only in solo mode) with structured health statistics, trend charts (fitness, sleep, symptoms, mental, vitals), medication tracking, health timeline, and subject filter (全部/自己/图图/明月...). All charts refresh on subject change.
- **Health record editing (restricted)**: DELETE / PATCH endpoints for single-record edit/delete; PATCH cannot change subject.
```

### 验证清单

- [ ] `health_records` 表正确创建（schema v7 幂等迁移，包含 subject 列和索引）
- [ ] Store CRUD 方法工作正常，**subject 过滤下推 SQL**
- [ ] `health_record_subjects()` 返回主体计数
- [ ] `solo_health_record` 工具可被 agent 调用（含 subject 参数）
- [ ] Agent 能正确识别 subject（自己 vs 家庭成员如"图图"、"明月"），含隐含信息提取
- [ ] `solo_health_summary` 工具支持 subject 过滤并返回正确数据
- [ ] 提示词优化后 agent 能检测健康信息并正确设置 subject
- [ ] Health API 所有端点返回正确，**所有读取端点支持 subject 过滤**
- [ ] `GET /subjects` 端点独立可用
- [ ] `HealthOverview` 包含 by_subject 字段
- [ ] DELETE / PATCH 写操作可用，PATCH 不能改 subject
- [ ] 前端 SubjectFilter 组件正确渲染和过滤
- [ ] 前端 client 每个 health 方法都接受 subject 参数
- [ ] 前端 Health 页面正确渲染（按主体过滤后数据正确，切换主体刷新所有图表）
- [ ] **Solo-only 验证**：切换到 wolo 模式时，侧边栏不显示 Health 入口，`/health` 路由不可访问
- [ ] 空数据优雅降级
- [ ] 自定义 category 在图表上 fallback 正常（不报错）
- [ ] `ruff check` 通过
- [ ] `tsc --noEmit` 通过
- [ ] health 测试用例全部新增并通过（Store/Tool/集成/API 四层）
- [ ] 现有测试全部通过

---

## 文件变更总览

### 新增文件

| 文件 | 说明 |
|------|------|
| `onboard/api/health.py` | Health REST API 路由（solo 前缀，含只读 + 受限写操作） |
| `onboard/frontend/src/pages/Health.tsx` | Health 页面主组件 |
| `onboard/frontend/src/components/health/SubjectFilter.tsx` | 主体选择器（全部/自己/图图/明月...） |
| `onboard/frontend/src/components/health/HealthStatsCards.tsx` | 统计卡片 |
| `onboard/frontend/src/components/health/CategoryBreakdown.tsx` | 类别分布图 |
| `onboard/frontend/src/components/health/FitnessTrend.tsx` | 运动趋势 |
| `onboard/frontend/src/components/health/SleepTrend.tsx` | 睡眠趋势 |
| `onboard/frontend/src/components/health/SymptomTracker.tsx` | 症状追踪 |
| `onboard/frontend/src/components/health/MedicationList.tsx` | 用药列表 |
| `onboard/frontend/src/components/health/MentalHealthPanel.tsx` | 心理状态 |
| `onboard/frontend/src/components/health/VitalSignsChart.tsx` | 体征图表 |
| `onboard/frontend/src/components/health/HealthTimeline.tsx` | 时间线 |
| `tests/test_solo/test_health.py` | Store/Tool/集成层 health 测试 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `solo/core/models.py` | 新增 `SoloHealthRecord` dataclass（含 subject 字段、metrics property） |
| `solo/core/store.py` | 新增 `health_records` DDL（含 subject 列和索引）、schema v7 幂等迁移、CRUD 方法（**含 subject 过滤**）、`health_record_subjects()` |
| `solo/tools.py` | 新增 `_tool_health_record`（含 subject 参数，不含 record_id）+ `_tool_health_summary`（含 subject 参数）定义及处理器，注册到 `tools()`；返回格式对齐项目惯例 |
| `solo/prompts.py` | 新增"健康记录提取原则"章节（含 subject 判断规则、隐含信息识别），更新路由决策表 |
| `onboard/server.py` | 注册 `health.router` |
| `onboard/services/solo_service.py` | 新增健康数据查询方法（**全部支持 subject 过滤**）+ 受限写操作 + `health_subjects()` |
| `onboard/frontend/src/components/Sidebar.tsx` | 添加 Health 侧边栏项（仅 solo 模式可见） |
| `onboard/frontend/src/App.tsx` | 添加 Health 路由 + 懒加载（solo-only） |
| `onboard/frontend/src/api/types.ts` | 添加 Health 类型定义（SoloHealthRecord 含 subject，HealthOverview 含 by_subject） |
| `onboard/frontend/src/api/client.ts` | 添加 health API 方法（**每个方法接受 subject 参数**，含 delete/update） |

---

## 实施时间估算

| Phase | 预计时间 | 依赖 |
|-------|----------|------|
| Phase 1: 数据模型与数据库表 | 1h | 无 |
| Phase 2: Store CRUD 方法（含 subject 下推） | 1h | Phase 1 |
| Phase 3: `solo_health_record` 工具 | 1.5h | Phase 2 |
| Phase 4: `solo_health_summary` 工具（含 subject） | 0.5h | Phase 2 |
| Phase 5: 提示词优化 | 1h | Phase 3 |
| Phase 6: Onboard 后端 API（含 subject + 写操作） | 1.5h | Phase 2 |
| Phase 7: 前端类型 + API 客户端（含 subject） | 0.5h | Phase 6 |
| Phase 8: 侧边栏 + 路由 | 0.25h | Phase 7 |
| Phase 9: Health 页面 + 组件 | 4h | Phase 8 |
| Phase 10: 测试 + 收尾 | 2h | Phase 1-9 |
| **总计** | **~13.5h** | |

---

## 关键风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| subject 过滤未下推 SQL | 多主体统计在数据量大时性能差 | Store 层 `list_health_records` 强制支持 subject 参数，Phase 2 测试覆盖 |
| record_id 关联回填失败 | health_record 无法强关联到 record | 降级为 date+subject 软关联，不阻塞主流程（设计 §2.6） |
| LLM 把稳定事实误判为事件 | 重复记录（鼻炎既进 memory 又进 health_records） | prompt 明确区分长效事实 vs 事件，Phase 5 集成测试覆盖 |
| wolo 模式泄露 health 数据 | 隐私问题 | 命名空间隔离 + 前端条件渲染（设计 §5.5），Phase 10 验证 |
| 自定义 category 在前端渲染异常 | 图表/图标缺失 | icon_map fallback 到 `♡`，颜色用默认（设计 §7.3） |
