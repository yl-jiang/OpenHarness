# Health & Finance 智能洞察报告 -- 设计方案

> 为 solo onboard 的 **Health** 和 **Finance** 模块增加**周/月/年智能分析报告**。
> 核心价值：利用 LLM 基于历史数据发现用户可能忽视的习惯、模式与倾向——不是统计搬运，而是**盲点挖掘**。
> 输出为**结构化 JSON + Markdown 双格式**，前端渲染富 UI（洞察卡片 + 盲点区 + 内嵌图表）。
>
> 仅适用于 **solo**，不适用于 wolo。

---

## 0. 核心理念

| 原则 | 说明 |
|------|------|
| **盲点优先** | LLM 的职责不是复述数据，而是从交叉统计中提炼人眼看不出的模式（"周五支出高 47%""睡眠<6h 次日 mood 负面率 68%"） |
| **证据驱动** | 每个洞察必须引用具体日期/数字/事件，不允许空洞鼓励 |
| **富展示** | 结构化 JSON 输出驱动前端卡片/mini 图表/severity 分色，观感远超纯 Markdown |
| **向后兼容** | 复用现有 `SoloReport` 模型，通过 `metadata.domain` 区分领域，零 schema 迁移 |

---

## 1. 架构概述

### 1.1 整体数据流

```
┌────────────────────────────────────────────────────────────────┐
│  用户点击 Health/Finance 页面「✨ 生成洞察报告」              │
└────────────────────────┬───────────────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────────────┐
│  POST /api/solo/insight-reports/generate                       │
│  body: { domain: "health"|"finance", report_type, period }     │
└────────────────────────┬───────────────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────────────┐
│  ① 证据层 EvidencePack                                        │
│     - 调用现有 service 聚合方法（sleep_trend, mental_trend,   │
│       transactions_summary, budgets...）                       │
│     - 补充交叉统计：day-of-week 分布、类别环比漂移、          │
│       睡眠↔mood 相关、z-score 异常点检测...                   │
│     - 输出：结构化 evidence dict（纯 Python，无 LLM）         │
└────────────────────────┬───────────────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────────────┐
│  ② LLM 洞察层                                                 │
│     - 领域专属 system prompt（health / finance 各一）         │
│     - user_prompt = profile_context + evidence_pack            │
│     - 输出：结构化 JSON（InsightReportSchema）                │
└────────────────────────┬───────────────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────────────┐
│  ③ 存储层                                                     │
│     - SoloReport(report_type, content=markdown渲染,           │
│       metadata={domain, insight_json, evidence_summary})       │
└────────────────────────┬───────────────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────────────┐
│  ④ 前端富 UI                                                  │
│     - InsightReportView.tsx 解析 metadata.insight_json        │
│     - Hero band + Blind Spots + Insight Cards + Patterns +    │
│       Mini Charts + Recommendations                           │
│     - 降级：insight_json 缺失时 fallback 到 MarkdownView     │
└────────────────────────────────────────────────────────────────┘
```

### 1.2 复用关系

| 现有模块 | 复用方式 |
|----------|----------|
| `SoloReport` 模型 | 直接使用，metadata 字段存 domain + insight JSON |
| `SoloStore.add_report / list_reports / get_report` | 直接使用 |
| `SoloService.health_*` / `finance_*` 方法 | 证据层调用 |
| `report_runner.py` / `report_cron.py` | 未来 cron 接入，本期不改 |
| 前端 `Reports.tsx` 列表 | 扩展 domain 过滤 tab |
| 前端 `ReportView.tsx` | Markdown 降级渲染 |
| 图表组件（`CashflowTrend`, `CategoryDonut`, `HealthTimeline` 等） | InsightReportView 内嵌复用 |

---

## 2. 数据模型

### 2.1 SoloReport 扩展（零迁移）

现有 `SoloReport` 已有 `metadata: dict | None`，无需改表。约定：

```python
metadata = {
    "domain": "health" | "finance" | "journal",  # 新增
    "insight_json": { ... },    # 结构化洞察（InsightReportSchema）
    "evidence_summary": "...",  # 证据层摘要（可选，调试用）
    "model": "...",             # 生成时使用的模型
    "tokens_used": 1234,        # token 消耗
}
```

`report_type` 仍为 `"weekly" | "monthly" | "yearly"`，周期逻辑完全复用。

### 2.2 InsightReportSchema（LLM 输出 JSON）

```jsonc
{
  // ── 头部 ──
  "headline": "本月你在'用消费缓解压力'",
  "narrative": "2-4 句核心叙事...",
  "period_comparison": [
    {
      "metric": "总支出",
      "current": 8200,
      "previous": 6100,
      "delta_pct": 34.4,
      "direction": "up",    // up | down | flat
      "unit": "¥"
    }
  ],

  // ── 盲点（头牌功能）──
  "blind_spots": [
    {
      "title": "周五是消费黑洞",
      "why": "周五均值 ¥420 vs 工作日 ¥285，连续 4 周如此",
      "evidence": "6/7 ¥530、6/14 ¥380、6/21 ¥490...",
      "severity": "watch"   // info | watch | alert
    }
  ],

  // ── 深度洞察 ──
  "insights": [
    {
      "icon": "🔍",
      "title": "外卖依赖度上升",
      "analysis": "...",
      "evidence": ["6/12 外卖 ¥58", "6/14 外卖 ¥72", ...],
      "severity": "info",
      "tags": ["dining", "habit"]
    }
  ],

  // ── 模式识别 ──
  "patterns": [
    {
      "name": "睡眠↔情绪",
      "strength": "strong",  // strong | moderate | weak
      "detail": "睡眠 <6h 的 7 天中，5 天 mood 为 negative"
    }
  ],

  // ── 行动建议 ──
  "recommendations": [
    {
      "action": "周五设置 ¥300 日限额提醒",
      "rationale": "过去 4 周五平均超出日常 47%",
      "expected_signal": "下周五消费<¥350 即为有效"
    }
  ],

  // ── mini 图表数据 ──
  "metrics": [
    {
      "label": "日均支出",
      "value": 273.5,
      "unit": "¥",
      "trend": [210, 280, 310, 250, 290, 260, 330],  // 近 7 天
      "comparison_value": 245.0,
      "comparison_label": "上期"
    }
  ]
}
```

### 2.3 Health 领域特有 metrics 示例

```jsonc
{
  "metrics": [
    { "label": "睡眠均值", "value": 6.8, "unit": "h", "trend": [...] },
    { "label": "运动天数", "value": 12, "unit": "天/月", "trend": [...] },
    { "label": "平均心率", "value": 72, "unit": "bpm", "trend": [...] }
  ],
  "patterns": [
    { "name": "运动↔睡眠质量", "strength": "strong", "detail": "..." },
    { "name": "周末补觉现象", "strength": "moderate", "detail": "..." }
  ],
  "blind_spots": [
    { "title": "服药遗漏集中在周末", "why": "...", "evidence": "...", "severity": "alert" },
    { "title": "步数持续下降但无感", "why": "...", "evidence": "...", "severity": "watch" }
  ]
}
```

---

## 3. 证据层 EvidencePack

证据层是整个功能的**质量上限**——LLM 能发现的盲点取决于你喂什么。核心原则：
- **预计算交叉统计**（LLM 擅长叙事，不擅长数学）
- **标注异常**（z-score > 1.5 的数据点直接标 `⚠️`）
- **提供对比基线**（vs 上周期、vs 总体均值）

### 3.1 Finance Evidence

| 证据维度 | 计算逻辑 | 产生的盲点类型 |
|----------|----------|---------------|
| 日均支出 + day-of-week 分布 | 按星期聚合取均值，标注最高/最低 | "周五消费黑洞" |
| 类别环比漂移 | 本期 vs 上期各类别占比差 > 10% 标出 | "外卖占比从 15% 涨到 28%" |
| 高频商户/counterparty | 同一 counterparty 出现 > 5 次 | "瑞幸本月 18 次" |
| 订阅检测 | 连续 2+ 月相同金额/同一商户 | "某订阅连续扣费但可能未使用" |
| 预算突破 | utilization > 0.8 的类别 | "餐饮预算已用 92%" |
| 单笔异常 | 金额 > 均值 + 2σ | "单笔 ¥3200（均值 ¥280）" |
| 收入波动 | 月收入 vs 滑动均值偏离 | "本月收入低于 3 月均值 15%" |

### 3.2 Health Evidence

| 证据维度 | 计算逻辑 | 产生的盲点类型 |
|----------|----------|---------------|
| 睡眠均值 + 标准差 + 趋势 | 日粒度，7日/30日窗口 | "睡眠持续缩短" |
| 睡眠↔mood 相关 | 低睡眠日(< μ-σ)的次日 mood 负面率 | "短睡后情绪负面率 68%" |
| 运动频率 + 连续不运动天数 | 统计 exercise 类别记录间隔 | "已连续 8 天无运动记录" |
| 用药规律性 | medication 记录的 day-of-week 覆盖率 | "周末服药遗漏率 40%" |
| 症状复发 | 同一 item 在 N 天内出现 > 2 次 | "头痛本周第 3 次" |
| 生命体征趋势 | 心率/血氧 30 天滑动均值 + 异常点 | "静息心率上升 5bpm" |
| 压力↔运动 | stress_level 高的周 vs 运动频次 | "高压周运动量下降 50%" |

### 3.3 跨领域（可选，Phase 2）

- Finance 支出 ↔ Health mood/stress（"压力大的周消费增加"）
- 运动天数 ↔ 睡眠质量 ↔ 次日工作产出（关联 journal records）

---

## 4. LLM Prompt 设计

### 4.1 Finance System Prompt（核心摘要）

```
你是一位个人财务洞察分析师。你的任务是从预计算的统计证据中：
1. 发现用户自己可能忽视的消费习惯和盲点
2. 识别异常模式和潜在风险
3. 给出可量化验证的具体建议

## 核心原则
- **盲点优先**：你的价值不是重复数据，而是发现"用户看了数据也未必注意到"的模式
- **引用证据**：每个结论必须引用具体日期/金额/百分比
- **不空洞**：禁止"注意消费""量入为出"等通用建议
- **结构化输出**：严格输出 JSON（InsightReportSchema）

## 输出格式
严格输出 JSON，schema 如下：{schema}
```

### 4.2 Health System Prompt（核心摘要）

```
你是一位个人健康趋势分析师。你的任务是从预计算的健康统计证据中：
1. 发现用户自己可能忽视的健康习惯和模式
2. 识别跨维度相关性（睡眠↔情绪、运动↔精力、用药↔症状）
3. 标记需要关注的趋势恶化信号

## 核心原则
- **模式优先**：重点识别跨维度关联，而非单一指标复述
- **时间序列敏感**：关注趋势方向（连续恶化 vs 波动 vs 改善）
- **不做医疗诊断**：只观察行为模式和趋势，不给医学建议
- **引用证据**：每个结论引用具体日期/数值

## 输出格式
严格输出 JSON，schema 如下：{schema}
```

### 4.3 Markdown 降级渲染

LLM 返回 JSON 后，后端将其确定性渲染为 Markdown 存入 `SoloReport.content`，保证：
- 前端无 `insight_json` 时 fallback 到 `MarkdownView`
- 导出/IM 推送仍可用
- 现有 `ReportView.tsx` 无需改动即可展示

---

## 5. API 设计

### 5.1 新增端点

| Method | Path | 描述 |
|--------|------|------|
| POST | `/api/solo/insight-reports/generate` | 生成洞察报告 |
| GET | `/api/solo/insight-reports` | 列出洞察报告（支持 domain 过滤） |
| GET | `/api/solo/insight-reports/{id}` | 获取单个报告详情 |
| DELETE | `/api/solo/insight-reports/{id}` | 删除报告 |

### 5.2 请求/响应

**POST /api/solo/insight-reports/generate**

```jsonc
// Request
{
  "domain": "health" | "finance",
  "report_type": "weekly" | "monthly" | "yearly",
  "start_date": "2026-06-16",   // 可选，不填自动推算
  "end_date": "2026-06-22"      // 可选
}

// Response
{
  "id": "rpt_xxxx",
  "report_type": "weekly",
  "content": "# 健康周报洞察\n...",           // Markdown
  "metadata": {
    "domain": "health",
    "insight_json": { ... },                    // InsightReportSchema
    "evidence_summary": "records=42, ..."
  },
  "period_start": "2026-06-16",
  "period_end": "2026-06-22",
  "created_at": "2026-06-23T01:31:00Z"
}
```

**GET /api/solo/insight-reports?domain=finance**

```jsonc
// Response: Report[]（现有 Report 类型，按 metadata.domain 过滤）
[{ "id": "...", "report_type": "weekly", "metadata": {"domain": "finance", ...}, ... }]
```

### 5.3 与现有 Reports API 的关系

洞察报告使用**独立前缀** `/api/solo/insight-reports`，与现有 `/api/solo/reports`（日记报告）隔离。原因：
1. 避免日记报告列表混入领域报告
2. 前端可独立演进
3. 底层仍共享 `SoloStore.add_report / list_reports`，通过 `metadata.domain` 区分

---

## 6. 前端 UI 设计

### 6.1 入口

- **Health 页面**顶部：增加 `✨ 洞察报告` 按钮 → 展开报告列表 + 生成入口
- **Finance 页面**顶部：同上
- **Reports 页面**（可选）：增加 domain tab（All / Journal / Health / Finance）

### 6.2 InsightReportView 组件结构

```
┌───────────────────────────────────────────────────────────┐
│  Hero Band                                                │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ 📊 headline                                         │ │
│  │ narrative (2-4 句)                                  │ │
│  │ period chips: [总支出 ¥8200 ↑34%] [日均 ¥273 ↑12%] │ │
│  └─────────────────────────────────────────────────────┘ │
├───────────────────────────────────────────────────────────┤
│  🕳️ Blind Spots 盲点区（最醒目）                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐     │
│  │ ⚠️ severity  │ │ ⚠️ severity  │ │ ℹ️ severity  │     │
│  │ title        │ │ title        │ │ title        │     │
│  │ why/evidence │ │ why/evidence │ │ why/evidence │     │
│  └──────────────┘ └──────────────┘ └──────────────┘     │
├───────────────────────────────────────────────────────────┤
│  📈 Metrics（mini sparklines）                           │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐           │
│  │label   │ │label   │ │label   │ │label   │           │
│  │value   │ │value   │ │value   │ │value   │           │
│  │~~trend~│ │~~trend~│ │~~trend~│ │~~trend~│           │
│  │vs 上期 │ │vs 上期 │ │vs 上期 │ │vs 上期 │           │
│  └────────┘ └────────┘ └────────┘ └────────┘           │
├───────────────────────────────────────────────────────────┤
│  🔍 Insights 洞察卡片（grid）                            │
│  ┌──────────────────────────┐ ┌─────────────────────┐   │
│  │ icon + title             │ │ icon + title        │   │
│  │ analysis (2-3 句)        │ │ analysis            │   │
│  │ evidence chips           │ │ evidence chips      │   │
│  │ [severity pill] [tags]   │ │ [severity] [tags]   │   │
│  └──────────────────────────┘ └─────────────────────┘   │
├───────────────────────────────────────────────────────────┤
│  🔗 Patterns 模式徽章                                    │
│  [睡眠↔情绪 strong] [运动↔睡眠 moderate] [...]          │
├───────────────────────────────────────────────────────────┤
│  💡 Recommendations 建议                                 │
│  □ action 1 — rationale — expected signal                │
│  □ action 2 — rationale — expected signal                │
├───────────────────────────────────────────────────────────┤
│  ▶ 展开原始数据/Markdown（可折叠）                       │
└───────────────────────────────────────────────────────────┘
```

### 6.3 设计语言

- 沿用现有 Tailwind 变量体系：`surface-1`/`surface-2`/`border`/`text`/`text-muted`/`accent-solo`
- severity 颜色：`alert` → `danger`，`watch` → `warning`，`info` → `accent-solo`
- 盲点卡片：加粗左边框（`border-l-4`），背景微亮（`bg-warning/5`）
- Sparkline：CSS-only 或 recharts `<Sparkline>` 极简实现
- Hero band：渐变背景 + `SciFiBackground` accent 调色
- 动画：卡片 stagger 淡入（`animation-delay`，已有 pattern）

### 6.4 响应式

- Desktop (lg+)：metrics 4 列、insights 2 列、blind spots 3 列
- Tablet (md)：metrics 2 列、insights 1 列
- Mobile：全部堆叠

---

## 7. 安全与边界

| 约束 | 处理 |
|------|------|
| 数据不足 | 记录 < 5 条时返回提示"数据不足"而非空洞报告 |
| LLM 幻觉 | 证据层预计算 + prompt 要求引用数据，JSON schema 验证 |
| 敏感数据 | 报告存本地 workspace，不上传第三方 |
| Token 控制 | Evidence pack 控制在 3000 token 以内，max_tokens 设 4096 |
| 生成耗时 | 前端 loading 态（复用已有 shimmer + spinning），超时 60s |

---

## 8. 未来演进（不在本期范围）

| 方向 | 说明 |
|------|------|
| Cron 自动生成 | `report_runner.py --domain health --report-type weekly` |
| IM 推送 | 周/月报自动推飞书 |
| 跨领域关联 | finance ↔ health ↔ journal 三域联合分析 |
| 报告对比 | 横向对比两期报告变化 |
| 用户反馈闭环 | 对 recommendation 标记"已执行/无效"反馈下期 |
| 自定义 prompt | 用户可微调关注点（"重点看睡眠""忽略通勤支出"） |

---

## 9. 工时估算

| 模块 | 工时 |
|------|------|
| 证据层（2 领域 × 7 维交叉统计） | 4h |
| LLM 层（prompt + schema + JSON 解析 + markdown 渲染） | 3h |
| API 层（4 端点 + service 方法） | 2h |
| 前端 InsightReportView 组件 | 5h |
| 前端 Health/Finance 页面入口 + 报告列表 | 2h |
| 测试 + 集成验证 | 2h |
| **合计** | **~18h** |
