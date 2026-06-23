# Health & Finance 智能洞察报告 -- 实施计划

> 基于 [report-design.md](./report-design.md) 的分步实施计划。
> 核心架构：**证据层 EvidencePack + LLM 结构化输出 + SoloReport metadata.domain 扩展 + 独立 API 端点 + 前端富 UI 视图**。
> **仅适用于 solo，不适用于 wolo。**
>
> 本计划假设当前代码库已实现 health 模块（schema v7）和 finance 模块（schema v8），报告模块从现有 `SoloReport` 基础上扩展。
> 整体工时约 **18h**，分 6 个 Phase，每个 Phase 含验证步骤。

---

## Phase 1: 证据层 EvidencePack

### 目标

实现 `solo/core/evidence.py`，为 health 和 finance 两个领域各提供一组预计算交叉统计函数，输出结构化 dict 供 LLM 消费。**这是整个功能的质量上限**——LLM 能发现的盲点取决于喂什么。

### 步骤

#### 1.1 创建证据模块骨架

**新建文件**: `solo/core/evidence.py`

```python
"""Evidence pack builders for insight reports.

Pre-computed cross-tabulations and anomaly detection — the quality ceiling
for what the LLM can discover.  No LLM calls here; pure Python statistics.
"""
from __future__ import annotations

from typing import Any

from solo.core.store import SoloStore


def build_finance_evidence(
    store: SoloStore,
    *,
    start_date: str,
    end_date: str,
    prev_start: str | None = None,
    prev_end: str | None = None,
) -> dict[str, Any]:
    ...


def build_health_evidence(
    store: SoloStore,
    *,
    start_date: str,
    end_date: str,
    prev_start: str | None = None,
    prev_end: str | None = None,
) -> dict[str, Any]:
    ...
```

#### 1.2 Finance Evidence 实现

**文件**: `solo/core/evidence.py` → `build_finance_evidence()`

调用 `store.list_finance_transactions(date_from=..., date_to=...)` 获取当前周期交易，获取上周期交易做对比。计算以下维度：

| 维度 | 实现要点 |
|------|----------|
| 日均支出 + day-of-week 分布 | `collections.Counter` 按 `datetime.weekday()` 聚合，标注最高/最低日 |
| 类别环比漂移 | 本期 vs 上期各类别占比差 > 10% 标出 |
| 高频商户/counterparty | 同一 `counterparty` 出现 > 5 次列出 |
| 订阅检测 | 连续 2+ 月相同金额 + 同一商户 |
| 预算突破 | `store.list_finance_budgets(active=True)` + 当期已花 vs budget.amount |
| 单笔异常 | 金额 > 均值 + 2σ 标记 |
| 收入波动 | 本期收入 vs 上期收入 delta_pct |

输出结构示例：

```python
{
    "period": {"start": "2026-06-16", "end": "2026-06-22"},
    "total_expense": 8200.0,
    "total_income": 15000.0,
    "daily_avg_expense": 273.5,
    "day_of_week_distribution": {
        "Mon": 210, "Tue": 250, "Wed": 230, "Thu": 280,
        "Fri": 420, "Sat": 310, "Sun": 290,
        "peak_day": "Fri", "peak_avg": 420, "weekday_avg": 242.5,
    },
    "category_shift": [
        {"category": "dining", "current_pct": 28, "prev_pct": 15, "delta": 13},
    ],
    "frequent_merchants": [
        {"counterparty": "瑞幸", "count": 18, "total": 540},
    ],
    "subscriptions": [
        {"counterparty": "iCloud", "amount": 21, "months": 6},
    ],
    "budget_breaches": [
        {"category": "dining", "budget": 2000, "spent": 1840, "utilization": 0.92},
    ],
    "anomalies": [
        {"date": "2026-06-18", "amount": 3200, "mean": 280, "z_score": 3.1},
    ],
    "income_change": {"current": 15000, "previous": 17600, "delta_pct": -14.8},
    "prev_period": {"start": prev_start, "end": prev_end},
}
```

#### 1.3 Health Evidence 实现

**文件**: `solo/core/evidence.py` → `build_health_evidence()`

调用 `store.list_health_records(date_from=..., date_to=...)` 获取当前周期记录。计算以下维度：

| 维度 | 实现要点 |
|------|----------|
| 睡眠均值 + 标准差 + 趋势 | 筛选 `category="sleep"` 或 `sleep_hours > 0`，7日滑动均值 |
| 睡眠↔mood 相关 | 低睡眠日(< μ-σ)的次日 mood 负面率 |
| 运动频率 + 连续不运动天数 | 筛选 `category="exercise"` 或 `exercise_type != ""`，计算间隔 |
| 用药规律性 | 筛选 `category="medication"`，day-of-week 覆盖率 |
| 症状复发 | 同一 `item` 在 N 天内出现 > 2 次 |
| 生命体征趋势 | 筛选 `category="vital"`，心率/血氧滑动均值 + 异常点 |
| 压力↔运动 | `stress_level` 高的周 vs 运动频次 |

输出结构示例：

```python
{
    "period": {"start": "2026-06-16", "end": "2026-06-22"},
    "sleep": {
        "mean": 6.8, "std": 1.2, "trend": [7, 6.5, 6, 7.5, 6.2, 5.8, 7],
        "low_sleep_days": 3,
    },
    "sleep_mood_correlation": {
        "low_sleep_negative_mood_rate": 0.68,
        "normal_sleep_negative_mood_rate": 0.22,
    },
    "exercise": {
        "days_with_exercise": 3, "total_days": 7,
        "max_gap_days": 4, "avg_gap_days": 2.3,
    },
    "medication_adherence": {
        "expected_days": 7, "actual_days": 5,
        "missed_days_of_week": ["Sat", "Sun"],
        "adherence_rate": 0.71,
    },
    "symptom_recurrence": [
        {"item": "头痛", "occurrences": 3, "dates": ["6/17", "6/20", "6/22"]},
    ],
    "vitals": {
        "resting_hr_trend": [72, 73, 71, 74, 75, 73, 76],
        "hr_delta": "+4 bpm",
        "spo2_anomalies": [],
    },
    "stress_exercise": {
        "high_stress_weeks_exercise_avg": 1.5,
        "low_stress_weeks_exercise_avg": 3.2,
    },
}
```

#### 1.4 辅助函数

**文件**: `solo/core/evidence.py`

```python
def _z_score(value: float, mean: float, std: float) -> float:
    """Compute z-score; returns 0 if std is 0."""
    return (value - mean) / std if std else 0.0

def _compute_prev_period(
    report_type: str, start_date: str, end_date: str,
) -> tuple[str, str]:
    """Compute the previous period boundaries for comparison."""
    from datetime import datetime, timedelta
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    span = (end_dt - start_dt).days + 1
    prev_end = (start_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_start = (start_dt - timedelta(days=span)).strftime("%Y-%m-%d")
    return prev_start, prev_end
```

### 验证

```bash
uv run pytest tests/test_solo/ -v -k "evidence"  # 新建测试
# 手动验证：
uv run python -c "
from solo.core.store import SoloStore
from solo.core.evidence import build_finance_evidence, build_health_evidence
store = SoloStore()
print(build_finance_evidence(store, start_date='2026-05-01', end_date='2026-05-31'))
print(build_health_evidence(store, start_date='2026-05-01', end_date='2026-05-31'))
"
```

---

## Phase 2: LLM 层 -- Prompt + 结构化输出

### 目标

实现领域专属 system prompt、JSON schema 定义、LLM 调用与解析、Markdown 降级渲染。

### 步骤

#### 2.1 定义 InsightReportSchema

**新建文件**: `solo/core/insight_schema.py`

```python
"""JSON schema for LLM structured output — InsightReport."""
from __future__ import annotations

INSIGHT_REPORT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "narrative": {"type": "string"},
        "period_comparison": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "current": {"type": "number"},
                    "previous": {"type": "number"},
                    "delta_pct": {"type": "number"},
                    "direction": {"type": "string", "enum": ["up", "down", "flat"]},
                    "unit": {"type": "string"},
                },
                "required": ["metric", "current", "previous", "delta_pct", "direction"],
            },
        },
        "blind_spots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "evidence": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "watch", "alert"]},
                },
                "required": ["title", "why", "evidence", "severity"],
            },
        },
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "icon": {"type": "string"},
                    "title": {"type": "string"},
                    "analysis": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "severity": {"type": "string", "enum": ["info", "watch", "alert"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "analysis", "evidence", "severity"],
            },
        },
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "strength": {"type": "string", "enum": ["strong", "moderate", "weak"]},
                    "detail": {"type": "string"},
                },
                "required": ["name", "strength", "detail"],
            },
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                    "expected_signal": {"type": "string"},
                },
                "required": ["action", "rationale", "expected_signal"],
            },
        },
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                    "trend": {"type": "array", "items": {"type": "number"}},
                    "comparison_value": {"type": "number"},
                    "comparison_label": {"type": "string"},
                },
                "required": ["label", "value", "unit"],
            },
        },
    },
    "required": ["headline", "narrative", "blind_spots", "insights", "recommendations"],
}
```

#### 2.2 领域专属 System Prompt

**文件**: `solo/prompts.py` — 新增两个函数

```python
def insight_report_system_prompt(domain: str) -> str:
    """Return the system prompt for domain-specific insight report generation."""
    if domain == "finance":
        return _FINANCE_INSIGHT_SYSTEM_PROMPT
    if domain == "health":
        return _HEALTH_INSIGHT_SYSTEM_PROMPT
    raise ValueError(f"unsupported domain: {domain}")
```

Finance prompt 核心要点：
- 你是个人财务洞察分析师
- 盲点优先：发现用户看了数据也未必注意到的模式
- 引用证据：每个结论必须引用具体日期/金额/百分比
- 不空洞：禁止"注意消费""量入为出"等通用建议
- 严格输出 JSON（InsightReportSchema）

Health prompt 核心要点：
- 你是个人健康趋势分析师
- 模式优先：重点识别跨维度关联
- 时间序列敏感：关注趋势方向
- 不做医疗诊断：只观察行为模式和趋势
- 严格输出 JSON（InsightReportSchema）

两个 prompt 都在末尾附上 `INSIGHT_REPORT_SCHEMA` 的 JSON schema 定义，要求 LLM 严格按此输出。

#### 2.3 LLM 调用与 JSON 解析

**文件**: `solo/agent.py` — 新增方法

```python
async def generate_insight_report(
    self,
    domain: str,
    evidence_pack: dict[str, Any],
    profile_context: str,
    *,
    report_type: str,
) -> dict[str, Any]:
    """Generate a structured insight report via LLM. Returns parsed JSON."""
    import json
    from solo.core.insight_schema import INSIGHT_REPORT_SCHEMA
    from solo.prompts import insight_report_system_prompt

    schema_str = json.dumps(INSIGHT_REPORT_SCHEMA, ensure_ascii=False, indent=2)
    system_prompt = insight_report_system_prompt(domain)
    user_prompt = (
        f"{profile_context}\n\n"
        f"## 预计算统计证据\n\n"
        f"```json\n{json.dumps(evidence_pack, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 输出要求\n\n"
        f"严格输出 JSON，schema 如下：\n```json\n{schema_str}\n```"
    )
    content = await self._complete(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=4096,
    )
    # Parse JSON from LLM response
    result = _safe_parse_json(content)
    if not result or "headline" not in result:
        raise RuntimeError(f"LLM did not return valid insight JSON: {content[:200]}")
    return result
```

#### 2.4 Markdown 降级渲染

**新建文件**: `solo/core/insight_render.py`

```python
"""Deterministic Markdown rendering from InsightReport JSON."""
from __future__ import annotations

from typing import Any


def render_insight_markdown(insight: dict[str, Any], domain: str, report_type: str) -> str:
    """Render InsightReport JSON to Markdown for fallback / export / IM push."""
    lines: list[str] = []

    period_label = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}.get(report_type, report_type)
    domain_label = {"health": "健康", "finance": "财务"}.get(domain, domain)
    headline = insight.get("headline", "")
    narrative = insight.get("narrative", "")

    lines.append(f"# 🌱 {domain_label}{period_label}洞察\n")
    if headline:
        lines.append(f"**{headline}**\n")
    if narrative:
        lines.append(f"{narrative}\n")

    # Period comparison
    comparisons = insight.get("period_comparison", [])
    if comparisons:
        lines.append("## 📊 周期对比\n")
        lines.append("| 指标 | 本期 | 上期 | 变化 |")
        lines.append("|------|------|------|------|")
        for c in comparisons:
            arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(c.get("direction", ""), "")
            unit = c.get("unit", "")
            lines.append(f"| {c['metric']} | {c['current']}{unit} | {c['previous']}{unit} | {arrow}{abs(c.get('delta_pct', 0)):.1f}% |")
        lines.append("")

    # Blind spots
    blind_spots = insight.get("blind_spots", [])
    if blind_spots:
        lines.append("## 🕳️ 你可能忽视的\n")
        for bs in blind_spots:
            icon = {"alert": "🔴", "watch": "🟡", "info": "ℹ️"}.get(bs.get("severity", "info"), "ℹ️")
            lines.append(f"**{icon} {bs['title']}**\n{bs['why']}\n> 证据：{bs['evidence']}\n")
        lines.append("")

    # Insights
    insights = insight.get("insights", [])
    if insights:
        lines.append("## 🔍 深度洞察\n")
        for ins in insights:
            icon = ins.get("icon", "🔍")
            lines.append(f"### {icon} {ins['title']}\n{ins['analysis']}\n")
            evidence = ins.get("evidence", [])
            if evidence:
                lines.append("证据：" + " | ".join(str(e) for e in evidence) + "\n")
        lines.append("")

    # Patterns
    patterns = insight.get("patterns", [])
    if patterns:
        lines.append("## 🔗 模式识别\n")
        for p in patterns:
            strength_icon = {"strong": "●●●", "moderate": "●●○", "weak": "●○○"}.get(p["strength"], "●○○")
            lines.append(f"- **{p['name']}** [{strength_icon}] {p['detail']}")
        lines.append("")

    # Recommendations
    recs = insight.get("recommendations", [])
    if recs:
        lines.append("## 💡 行动建议\n")
        for i, r in enumerate(recs, 1):
            lines.append(f"{i}. **{r['action']}** — {r['rationale']} — 验证信号：{r.get('expected_signal', '—')}")
        lines.append("")

    return "\n".join(lines)
```

### 验证

```bash
# 单元测试
uv run pytest tests/test_solo/ -v -k "insight_render or insight_schema"
# 手动验证 Markdown 渲染
uv run python -c "
from solo.core.insight_render import render_insight_markdown
test = {'headline': 'test', 'narrative': 'n', 'blind_spots': [], 'insights': [], 'recommendations': []}
print(render_insight_markdown(test, 'finance', 'weekly'))
"
```

---

## Phase 3: 报告生成集成

### 目标

在 `SoloProcessor` 中新增 `generate_insight_report()` 方法，串联证据层 → LLM → 存储，并在 `SoloService` 中暴露。

### 步骤

#### 3.1 SoloProcessor 新增方法

**文件**: `solo/processor.py`

```python
async def generate_insight_report(
    self,
    domain: str,          # "health" | "finance"
    report_type: str,     # "weekly" | "monthly" | "yearly"
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> SoloReport:
    """Generate a domain-specific insight report (health or finance)."""
    from solo.core.evidence import build_finance_evidence, build_health_evidence, _compute_prev_period
    from solo.core.insight_render import render_insight_markdown

    # 1. Resolve period
    now = datetime.now(timezone.utc)
    if start_date and end_date:
        start, end = start_date, end_date
    elif start_date:
        start, end = start_date, now.strftime("%Y-%m-%d")
    else:
        _window = REPORT_WINDOW_DAYS
        start = (now - timedelta(days=_window.get(report_type, 30))).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")

    # 2. Build evidence pack
    prev_start, prev_end = _compute_prev_period(report_type, start, end)
    if domain == "finance":
        evidence = build_finance_evidence(
            self.store, start_date=start, end_date=end,
            prev_start=prev_start, prev_end=prev_end,
        )
    elif domain == "health":
        evidence = build_health_evidence(
            self.store, start_date=start, end_date=end,
            prev_start=prev_start, prev_end=prev_end,
        )
    else:
        raise ValueError(f"unsupported domain: {domain}")

    # 3. Check data sufficiency
    has_data = bool(evidence.get("total_expense") or evidence.get("sleep") or evidence.get("exercise"))
    if not has_data:
        content = (
            f"# {domain.capitalize()} {report_type.capitalize()} Insight\n\n"
            f"> 📅 {start} ~ {end}\n\n"
            f"该时间段内数据不足，无法生成洞察报告。建议积累更多记录后再试。"
        )
        report = SoloReport(
            id=uuid4().hex[:12], report_type=report_type, content=content,
            created_at=_now(), period_start=start, period_end=end,
            metadata={"domain": domain},
        )
        self.store.add_report(report)
        return report

    # 4. LLM generation
    profile_context = self._profile_context()
    insight_json = await self.agent.generate_insight_report(
        domain, evidence, profile_context, report_type=report_type,
    )

    # 5. Render Markdown fallback
    markdown_content = render_insight_markdown(insight_json, domain, report_type)

    # 6. Store
    report = SoloReport(
        id=uuid4().hex[:12],
        report_type=report_type,
        content=markdown_content,
        created_at=_now(),
        period_start=start,
        period_end=end,
        metadata={
            "domain": domain,
            "insight_json": insight_json,
            "evidence_summary": f"records_in_period={...}",
        },
    )
    self.store.add_report(report)
    return report
```

#### 3.2 SoloService 新增方法

**文件**: `onboard/services/solo_service.py`

```python
async def generate_insight_report(
    self,
    domain: str,
    report_type: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Generate a domain-specific insight report."""
    config = load_config(self.workspace)
    agent = OpenHarnessSoloAgent(
        profile=config.provider_profile,
        record_model_call=self.store.record_llm_call,
    )
    report = await SoloProcessor(self.store, agent).generate_insight_report(
        domain, report_type, start_date=start_date, end_date=end_date,
    )
    return to_jsonable(report)

def list_insight_reports(
    self, domain: str | None = None, report_type: str | None = None,
) -> list[dict[str, Any]]:
    """List insight reports, optionally filtered by domain and type."""
    reports = self.store.list_reports()
    # Filter: only reports with metadata.domain
    filtered = [r for r in reports if (r.metadata or {}).get("domain")]
    if domain:
        filtered = [r for r in filtered if (r.metadata or {}).get("domain") == domain]
    if report_type:
        filtered = [r for r in filtered if r.report_type == report_type]
    filtered.sort(key=lambda r: r.period_start or r.created_at, reverse=True)
    return [to_jsonable(r) for r in filtered]

def get_insight_report(self, report_id: str) -> dict[str, Any] | None:
    """Get a single insight report by ID."""
    report = find_by_id(self.store.list_reports(), report_id)
    if report is None or not (report.metadata or {}).get("domain"):
        return None
    return to_jsonable(report)

def delete_insight_report(self, report_id: str) -> bool:
    """Delete an insight report."""
    report = find_by_id(self.store.list_reports(), report_id)
    if report is None or not (report.metadata or {}).get("domain"):
        return False
    return self.store.delete_report(report_id)
```

### 验证

```bash
# 通过 onboard API 手动测试
uv run python -c "
import asyncio
from onboard.services.solo_service import SoloService
svc = SoloService()
result = asyncio.run(svc.generate_insight_report('finance', 'weekly'))
print(result['id'], result['metadata']['domain'])
"
```

---

## Phase 4: API 端点

### 目标

新增独立前缀的 insight-reports API 端点，与现有 `/api/solo/reports` 隔离。

### 步骤

#### 4.1 新建 API 路由文件

**新建文件**: `onboard/api/insight_reports.py`

```python
"""Insight report API routes (solo-only, domain-specific)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from onboard.services.solo_service import SoloService


router = APIRouter(prefix="/api/solo/insight-reports", tags=["insight-reports"])


class InsightReportGenerateRequest(BaseModel):
    domain: str          # "health" | "finance"
    report_type: str     # "weekly" | "monthly" | "yearly"
    start_date: str | None = None
    end_date: str | None = None


def _service(workspace: str | None = None) -> SoloService:
    return SoloService(workspace)


@router.post("/generate")
async def generate_insight_report(
    request: InsightReportGenerateRequest,
    workspace: str | None = None,
) -> dict[str, Any]:
    if request.domain not in ("health", "finance"):
        raise HTTPException(status_code=400, detail="domain must be 'health' or 'finance'")
    if request.report_type not in ("weekly", "monthly", "yearly"):
        raise HTTPException(status_code=400, detail="report_type must be weekly/monthly/yearly")
    return await _service(workspace).generate_insight_report(
        domain=request.domain,
        report_type=request.report_type,
        start_date=request.start_date,
        end_date=request.end_date,
    )


@router.get("")
def list_insight_reports(
    domain: str | None = None,
    report_type: str | None = None,
    workspace: str | None = None,
) -> list[dict[str, Any]]:
    return _service(workspace).list_insight_reports(domain=domain, report_type=report_type)


@router.get("/{report_id}")
def get_insight_report(
    report_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    result = _service(workspace).get_insight_report(report_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Insight report not found")
    return result


@router.delete("/{report_id}")
def delete_insight_report(
    report_id: str,
    workspace: str | None = None,
) -> dict[str, bool]:
    ok = _service(workspace).delete_insight_report(report_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Insight report not found")
    return {"ok": True}
```

#### 4.2 注册路由

**文件**: `onboard/server.py`

在 FastAPI app 中注册新路由：

```python
from onboard.api.insight_reports import router as insight_reports_router
app.include_router(insight_reports_router)
```

查找现有 `include_router` 调用位置，在其附近添加。

### 验证

```bash
# 启动 onboard 服务
uv run solo onboard run
# 测试端点
curl -X POST http://localhost:8787/api/solo/insight-reports/generate \
  -H "Content-Type: application/json" \
  -d '{"domain": "finance", "report_type": "weekly"}'
curl http://localhost:8787/api/solo/insight-reports?domain=finance
```

---

## Phase 5: 前端 API Client + 类型

### 目标

在前端 api client 中新增 insight-reports 相关调用方法和类型定义。

### 步骤

#### 5.1 新增类型定义

**文件**: `onboard/frontend/src/api/types.ts`

```typescript
export type InsightDomain = 'health' | 'finance';

export interface InsightBlindSpot {
  title: string;
  why: string;
  evidence: string;
  severity: 'info' | 'watch' | 'alert';
}

export interface InsightItem {
  icon?: string;
  title: string;
  analysis: string;
  evidence: string[];
  severity: 'info' | 'watch' | 'alert';
  tags?: string[];
}

export interface InsightPattern {
  name: string;
  strength: 'strong' | 'moderate' | 'weak';
  detail: string;
}

export interface InsightRecommendation {
  action: string;
  rationale: string;
  expected_signal: string;
}

export interface InsightMetric {
  label: string;
  value: number;
  unit: string;
  trend?: number[];
  comparison_value?: number;
  comparison_label?: string;
}

export interface InsightPeriodComparison {
  metric: string;
  current: number;
  previous: number;
  delta_pct: number;
  direction: 'up' | 'down' | 'flat';
  unit?: string;
}

export interface InsightReportJSON {
  headline: string;
  narrative: string;
  period_comparison?: InsightPeriodComparison[];
  blind_spots: InsightBlindSpot[];
  insights: InsightItem[];
  patterns?: InsightPattern[];
  recommendations: InsightRecommendation[];
  metrics?: InsightMetric[];
}

// Extend existing Report type — metadata may contain insight data
// Report.metadata is already Record<string, unknown> | null
```

#### 5.2 新增 API Client 方法

**文件**: `onboard/frontend/src/api/client.ts`

在 `api` 对象中新增 `insightReports` 命名空间：

```typescript
insightReports: {
  list: (params: { domain?: string; report_type?: string } = {}) =>
    request<Report[]>(`/api/solo/insight-reports${query(params)}`),
  get: (id: string) =>
    request<Report>(`/api/solo/insight-reports/${id}`),
  generate: (domain: string, report_type: string) =>
    request<Report>(`/api/solo/insight-reports/generate`, {
      method: 'POST',
      body: JSON.stringify({ domain, report_type }),
    }),
  delete: (id: string) =>
    request<{ ok: boolean }>(`/api/solo/insight-reports/${id}`, { method: 'DELETE' }),
},
```

### 验证

```bash
cd onboard/frontend && npx tsc --noEmit
```

---

## Phase 6: 前端富 UI

### 目标

实现 `InsightReportView.tsx` 富视图组件，在 Health/Finance 页面添加入口，在 App 路由中注册。

### 步骤

#### 6.1 InsightReportView 主组件

**新建文件**: `onboard/frontend/src/pages/InsightReportView.tsx`

组件结构（对应 design §6.2）：

```
InsightReportView
├── HeroBand          — headline + narrative + period_comparison chips
├── BlindSpotsSection — 盲点卡片网格（severity 分色 + 左边框）
├── MetricsSection    — mini sparkline 卡片（4 列 grid）
├── InsightsSection   — 洞察卡片网格（2 列 grid）
├── PatternsSection   — 模式徽章条
├── RecommendationsSection — 建议清单
└── RawDataCollapsible — 可折叠原始 Markdown
```

关键实现要点：

1. **数据获取**：`useApi(() => api.insightReports.get(id), [id])`
2. **JSON 解析**：从 `report.metadata?.insight_json` 提取结构化数据
3. **降级**：`insight_json` 缺失时 fallback 到 `<MarkdownView content={report.content} />`
4. **Severity 颜色映射**：
   - `alert` → `border-danger bg-danger/5 text-danger`
   - `watch` → `border-warning bg-warning/5 text-warning`
   - `info` → `border-accent-solo bg-accent-solo/5 text-accent-solo`
5. **Sparkline**：使用 recharts `<Sparkline>` 或 CSS-only 极简实现（`▁▂▃▅▆▇` 字符）
6. **动画**：卡片 stagger 淡入（`animation-delay`，复用已有 pattern）
7. **响应式**：lg 4/2/3 列，md 2/1/2 列，mobile 堆叠

#### 6.2 InsightReportList 组件

**新建文件**: `onboard/frontend/src/components/InsightReportList.tsx`

轻量列表组件，用于嵌入 Health/Finance 页面：

```tsx
interface InsightReportListProps {
  domain: 'health' | 'finance';
}
```

- 调用 `api.insightReports.list({ domain })`
- 显示最近 5 条报告（period + created_at + 链接）
- 底部「查看全部」链接到 `/insight-reports?domain=...`
- 顶部「✨ 生成洞察报告」按钮（选择 weekly/monthly/yearly）

#### 6.3 Health 页面入口

**文件**: `onboard/frontend/src/pages/Health.tsx`

在页面顶部（header 下方）插入：

```tsx
<InsightReportList domain="health" />
```

#### 6.4 Finance 页面入口

**文件**: `onboard/frontend/src/pages/Finance.tsx`

在页面顶部（header 下方）插入：

```tsx
<InsightReportList domain="finance" />
```

#### 6.5 App 路由注册

**文件**: `onboard/frontend/src/App.tsx`

新增路由：

```tsx
const InsightReportView = lazyWithRetry(() =>
  import('./pages/InsightReportView').then((m) => ({ default: m.InsightReportView }))
);

// 在 children 中添加：
{ path: 'insight-reports/:id', element: <SuspenseLoader><InsightReportView /></SuspenseLoader> },
```

#### 6.6 Sidebar 导航（可选）

**文件**: `onboard/frontend/src/components/Sidebar.tsx`

如果 sidebar 有 Reports 入口，可考虑增加 Insight Reports 子项或让 Health/Finance 页面的洞察入口足够显眼。

### 验证

```bash
# 前端类型检查
cd onboard/frontend && npx tsc --noEmit

# 手动验证
# 1. 启动 onboard: uv run solo onboard run
# 2. 打开 Health 页面 → 点击「生成洞察报告」→ 选择 weekly
# 3. 等待生成完成 → 点击报告 → 验证富 UI 渲染
# 4. 验证 Finance 页面同流程
# 5. 验证降级：手动删除 metadata.insight_json → 应 fallback 到 MarkdownView
```

---

## 测试策略

### 单元测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_solo/test_evidence.py` | 证据层交叉统计、z-score、prev_period 计算 |
| `tests/test_solo/test_insight_render.py` | Markdown 渲染：空数据、完整数据、部分字段缺失 |
| `tests/test_solo/test_insight_schema.py` | JSON schema 验证 |
| `tests/test_solo/test_insight_report_api.py` | API 端点：generate/list/get/delete |

### 集成测试

- 端到端：从 API 调用到报告生成再到前端渲染
- 降级测试：`insight_json` 缺失时 Markdown fallback
- 数据不足：记录 < 5 条时的提示信息

### 手动验证清单

- [ ] Health 页面「生成洞察报告」按钮可用
- [ ] Finance 页面「生成洞察报告」按钮可用
- [ ] weekly/monthly/yearly 三种周期均可生成
- [ ] 报告列表按 domain 正确过滤
- [ ] InsightReportView 富 UI 正确渲染各区块
- [ ] severity 颜色正确（alert 红 / watch 黄 / info 蓝）
- [ ] Sparkline mini 图表正确显示趋势
- [ ] 降级：无 insight_json 时 MarkdownView 正常渲染
- [ ] 删除报告功能正常
- [ ] 现有日记报告不受影响（metadata.domain 为空或 "journal"）

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新建** | `solo/core/evidence.py` | 证据层：finance + health 交叉统计 |
| **新建** | `solo/core/insight_schema.py` | InsightReport JSON schema 定义 |
| **新建** | `solo/core/insight_render.py` | 结构化 JSON → Markdown 降级渲染 |
| **新建** | `onboard/api/insight_reports.py` | API 路由：4 个端点 |
| **新建** | `onboard/frontend/src/pages/InsightReportView.tsx` | 富 UI 视图组件 |
| **新建** | `onboard/frontend/src/components/InsightReportList.tsx` | 报告列表 + 生成入口 |
| **新建** | `tests/test_solo/test_evidence.py` | 证据层测试 |
| **新建** | `tests/test_solo/test_insight_render.py` | Markdown 渲染测试 |
| **新建** | `tests/test_solo/test_insight_report_api.py` | API 测试 |
| **修改** | `solo/prompts.py` | 新增 `insight_report_system_prompt()` + 两个领域 prompt |
| **修改** | `solo/agent.py` | 新增 `generate_insight_report()` 方法 |
| **修改** | `solo/processor.py` | 新增 `generate_insight_report()` 方法 |
| **修改** | `onboard/services/solo_service.py` | 新增 4 个 insight report 方法 |
| **修改** | `onboard/server.py` | 注册 insight_reports 路由 |
| **修改** | `onboard/frontend/src/api/types.ts` | 新增 Insight* 类型定义 |
| **修改** | `onboard/frontend/src/api/client.ts` | 新增 insightReports API 方法 |
| **修改** | `onboard/frontend/src/pages/Health.tsx` | 插入 InsightReportList 入口 |
| **修改** | `onboard/frontend/src/pages/Finance.tsx` | 插入 InsightReportList 入口 |
| **修改** | `onboard/frontend/src/App.tsx` | 新增 InsightReportView 路由 |

---

## 工时估算

| Phase | 内容 | 工时 |
|-------|------|------|
| 1 | 证据层 EvidencePack（2 领域 × 7 维交叉统计） | 4h |
| 2 | LLM 层（prompt + schema + JSON 解析 + markdown 渲染） | 3h |
| 3 | 报告生成集成（processor + service） | 2h |
| 4 | API 端点（4 端点 + 路由注册） | 1h |
| 5 | 前端 API Client + 类型 | 1h |
| 6 | 前端富 UI（InsightReportView + 入口 + 路由） | 5h |
| — | 测试 + 集成验证 | 2h |
| **合计** | | **~18h** |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM 不严格输出 JSON | `_safe_parse_json` 容错 + 必要时重试一次 + 降级到 Markdown |
| 证据层数据不足导致空洞报告 | < 5 条记录时返回提示而非生成 |
| 前端 Sparkline 实现复杂 | 先用 CSS 字符 sparkline（`▁▂▃▅▆▇`），后续可换 recharts |
| 生成耗时过长 | 前端 loading 态 + 60s 超时提示 |
| 现有日记报告被误过滤 | `list_insight_reports` 只返回 `metadata.domain` 存在的报告，现有报告不受影响 |

---

## 后续演进（不在本期范围）

| 方向 | 说明 | 预估工时 |
|------|------|----------|
| Cron 自动生成 | `report_runner.py --domain health --report-type weekly` | 2h |
| IM 推送 | 周/月报自动推飞书 | 1h |
| 跨领域关联 | finance ↔ health ↔ journal 三域联合分析 | 4h |
| 报告对比 | 横向对比两期报告变化 | 3h |
| 用户反馈闭环 | 对 recommendation 标记"已执行/无效" | 2h |
| 自定义 prompt | 用户可微调关注点 | 2h |
