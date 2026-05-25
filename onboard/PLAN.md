# Onboard — Solo/Wolo WebUI Dashboard

> **一个统一的 Web 仪表盘**，同时服务 solo（个人日志）和 wolo（工作日志），提供数据浏览、统计分析、报告展示、实时对话、生命周期管理等功能。

## 1. 项目定位

### 1.1 解决什么问题

solo/wolo 目前只能通过 CLI 或远程 channel（Telegram/Feishu/Slack 等）交互，缺乏：
- 直观的数据浏览和搜索界面
- 统计图表和趋势分析
- 报告的富文本展示
- 本地 Web 聊天界面（不依赖第三方 channel）
- 一键管理 gateway 生命周期

### 1.2 核心设计原则

1. **复用优先**：后端直接 import solo/wolo 的 `store`、`models`、`runner`，零数据复制
2. **只读为主**：WebUI 主要是数据展示层，写操作仅限必要场景（聊天、触发 process/report）
3. **松耦合**：onboard 作为独立 package，不修改 solo/wolo 核心代码，仅通过公开 API 调用
4. **美观现代**：深色主题、glassmorphism、微动画、响应式布局

---

## 2. 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| **前端** | Vite + React 19 + TypeScript | 轻量快速，与现有 TUI（React+Ink）技术栈一致 |
| **样式** | Vanilla CSS（CSS Variables 设计系统） | 最大灵活性，项目规范要求 |
| **图表** | Chart.js 或 Recharts | 轻量级数据可视化 |
| **Markdown** | react-markdown + remark-gfm | 报告/记录的富文本渲染 |
| **后端** | FastAPI + uvicorn | Python 生态，直接复用 solo/wolo 代码 |
| **实时通信** | WebSocket（FastAPI 原生支持） | 聊天流式输出 |
| **构建** | 前端 build 产物由 FastAPI 静态托管 | 单进程部署，无需 nginx |

---

## 3. 项目结构

```
OpenHarness/
├── solo/                    # 已有
├── wolo/                    # 已有
├── onboard/                 # 新增 — WebUI 项目
│   ├── __init__.py
│   ├── __main__.py          # python -m onboard
│   ├── cli.py               # typer CLI: onboard run/start/stop
│   ├── server.py            # FastAPI app + uvicorn 启动
│   ├── api/
│   │   ├── __init__.py
│   │   ├── solo_routes.py   # Solo REST API 路由
│   │   ├── wolo_routes.py   # Wolo REST API 路由
│   │   ├── chat.py          # WebSocket 聊天端点
│   │   ├── lifecycle.py     # Gateway 生命周期管理 API
│   │   └── stats.py         # 统计聚合 API
│   ├── services/
│   │   ├── __init__.py
│   │   ├── solo_service.py  # 封装 solo store 操作
│   │   ├── wolo_service.py  # 封装 wolo store 操作
│   │   └── chat_service.py  # 聊天会话管理
│   ├── frontend/            # Vite + React 前端
│   │   ├── index.html
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   ├── vite.config.ts
│   │   ├── src/
│   │   │   ├── main.tsx
│   │   │   ├── App.tsx
│   │   │   ├── index.css         # 全局设计系统
│   │   │   ├── api/              # API 客户端层
│   │   │   │   ├── client.ts     # fetch/WebSocket 封装
│   │   │   │   └── types.ts      # 与后端对齐的 TypeScript 类型
│   │   │   ├── components/       # 通用 UI 组件
│   │   │   │   ├── Layout.tsx         # 侧边栏 + 内容区
│   │   │   │   ├── Sidebar.tsx        # 导航侧栏（solo/wolo 切换）
│   │   │   │   ├── StatsCard.tsx      # 统计数字卡片
│   │   │   │   ├── DataTable.tsx      # 通用数据表格
│   │   │   │   ├── SearchBar.tsx      # 搜索输入
│   │   │   │   ├── MarkdownView.tsx   # Markdown 渲染
│   │   │   │   ├── ChatPanel.tsx      # 聊天界面
│   │   │   │   ├── StatusBadge.tsx    # 状态标签
│   │   │   │   └── Charts.tsx         # 图表组件
│   │   │   ├── pages/            # 页面组件
│   │   │   │   ├── Dashboard.tsx      # 首页仪表盘
│   │   │   │   ├── Entries.tsx        # 原始条目浏览
│   │   │   │   ├── Records.tsx        # 结构化记录浏览
│   │   │   │   ├── RecordDetail.tsx   # 单条记录详情
│   │   │   │   ├── Todos.tsx          # Todo 管理
│   │   │   │   ├── Reports.tsx        # 报告列表 + 生成
│   │   │   │   ├── ReportView.tsx     # 单份报告渲染
│   │   │   │   ├── Search.tsx         # 搜索结果页
│   │   │   │   ├── Chat.tsx           # 聊天页面
│   │   │   │   ├── Settings.tsx       # 配置查看
│   │   │   │   ├── Decisions.tsx      # [wolo] 决策列表
│   │   │   │   └── Highlights.tsx     # [wolo] 高亮/阻塞
│   │   │   └── hooks/            # 自定义 React hooks
│   │   │       ├── useApi.ts          # 数据获取 hook
│   │   │       └── useWebSocket.ts    # WebSocket hook
│   │   └── dist/                 # 构建产物（git-ignored）
│   └── README.md
```

---

## 4. 功能模块详细设计

### 4.1 Dashboard（首页仪表盘）

| 区域 | 内容 | 数据来源 |
|------|------|----------|
| **顶部概览** | 条目总数、记录总数、本周新增、待处理数 | `store.count_*()` |
| **Gateway 状态** | 运行/停止、PID、运行时长 | `gateway_status()` |
| **近期活动时间线** | 最近 10 条记录，按时间排列 | `store.list_records(limit=10)` |
| **情绪趋势图** | 过去 30 天情绪分布折线图 | `store.list_records()` 聚合 |
| **标签云** | 高频标签可视化 | `store.list_records()` 聚合 tags |
| **Todo 进度** | 待办/进行中/已完成比例环形图 | `store.list_todos()` 聚合 |
| **[wolo] 阻塞项** | 当前未解决的 blocker 列表 | `store.list_highlights(kind="blocker")` |

### 4.2 数据浏览

#### Entries（原始条目）
- 分页列表，显示：创建时间、内容预览、来源 channel、是否已处理
- 点击展开完整内容
- 筛选：日期范围、channel

#### Records（结构化记录）
- 卡片式布局，每条显示：日期、摘要、标签（彩色 chips）、情绪图标
- 点击进入详情页：完整内容、原始条目链接、关联的 todo/决策/高亮
- 筛选：日期范围、标签、情绪

#### Todos
- 看板视图（pending / in_progress / done 三列）
- 支持标记完成
- 按优先级排序、按 project 分组（wolo）

#### [wolo 独有] Decisions & Highlights
- Decisions：列表视图，显示标题、理由、影响、关联项目
- Highlights：按 kind 分组（important/blocker/risk/prompt/tool）

### 4.3 搜索

- 全局搜索栏（始终可见）
- 复用 solo/wolo 的 BM25 + Temporal Decay 搜索引擎
- 结果高亮关键词
- 筛选面板：标签、情绪、日期范围

### 4.4 报告

- 已生成报告列表（按类型和日期）
- 报告详情页：Markdown 富文本渲染
- 一键生成新报告（weekly/monthly/yearly）
- 报告生成进度展示（通过 WebSocket 流式更新）

### 4.5 统计分析

- **时间维度**：每日/每周/每月记录量折线图
- **情绪分析**：情绪分布饼图 + 趋势折线图
- **标签热度**：Top 20 标签柱状图
- **活跃度日历**：GitHub 风格的活跃度热力图
- **[wolo]**：项目维度统计、决策/阻塞趋势

### 4.6 聊天

- WebSocket 实时流式对话
- 复用 `SoloQueryRunner` / `WoloQueryRunner`
- 支持 Markdown 渲染 AI 回复
- 显示工具调用过程（类似 TUI 的 tool_started / tool_completed）
- 会话历史持久化（复用现有 session 存储）

### 4.7 设置/配置

- 只读展示当前配置（provider_profile, channels, heartbeat）
- Gateway 状态和日志查看
- Heartbeat 状态和最近信号

---

## 5. 后端 API 设计

### 5.1 REST API 路由

```
# === 通用路由 ===
GET  /api/health                        # 健康检查

# === Solo 路由 ===
GET  /api/solo/stats                    # 统计概览
GET  /api/solo/entries?limit=&offset=&channel=
GET  /api/solo/entries/:id
GET  /api/solo/records?limit=&offset=&tag=&emotion=&date_from=&date_to=
GET  /api/solo/records/:id
GET  /api/solo/search?q=&tags=&emotions=&date_from=&date_to=
GET  /api/solo/todos?status=
PUT  /api/solo/todos/:id/done
GET  /api/solo/reports?type=
GET  /api/solo/reports/:id
POST /api/solo/reports/generate         # { type: "weekly"|"monthly"|"yearly" }
POST /api/solo/process                  # 触发 pending entries 处理
GET  /api/solo/config                   # 只读配置
GET  /api/solo/gateway/status           # Gateway 状态

# === Wolo 路由（除上述相同路由外，增加） ===
GET  /api/wolo/decisions?project=
GET  /api/wolo/highlights?kind=&project=
GET  /api/wolo/blockers

# === Gateway 生命周期 ===
POST /api/solo/gateway/start
POST /api/solo/gateway/stop
POST /api/wolo/gateway/start
POST /api/wolo/gateway/stop

# === WebSocket ===
WS   /ws/chat/{app}                     # app = "solo" | "wolo"
```

### 5.2 WebSocket 聊天协议

```jsonc
// Client → Server
{ "type": "message", "content": "今天做了什么" }
{ "type": "cancel" }    // 中断当前回复

// Server → Client（流式）
{ "type": "delta", "content": "部分回复文本..." }
{ "type": "tool_start", "tool": "wolo_search", "args": {...} }
{ "type": "tool_complete", "tool": "wolo_search", "result": "..." }
{ "type": "complete", "content": "完整回复" }
{ "type": "error", "message": "错误信息" }
```

---

## 6. CLI 集成

### 6.1 新增 `onboard` 子命令

在 `solo/cli.py` 和 `wolo/cli.py` 中各新增 `onboard` 子命令组：

```python
# solo/cli.py
onboard_app = typer.Typer(name="onboard", help="WebUI dashboard management")
app.add_typer(onboard_app)

@onboard_app.command("run")
def onboard_run(port: int = 8090):
    """Start WebUI in foreground"""
    from onboard.server import run_server
    run_server(app="solo", port=port)

@onboard_app.command("start")
def onboard_start(port: int = 8090):
    """Start WebUI in background"""
    from onboard.server import start_background
    start_background(app="solo", port=port)

@onboard_app.command("stop")
def onboard_stop():
    """Stop background WebUI"""
    from onboard.server import stop_background
    stop_background(app="solo")
```

wolo/cli.py 同理，`app="wolo"`。

### 6.2 独立 CLI 入口

同时提供独立入口 `onboard` CLI（管理统一 dashboard）：

```
onboard run [--port 8090]      # 前台启动（同时服务 solo + wolo）
onboard start [--port 8090]    # 后台启动
onboard stop                   # 停止
onboard status                 # 查看运行状态
```

### 6.3 pyproject.toml 变更

```toml
[project.scripts]
# ... 现有 ...
onboard = "onboard.cli:app"

# 打包
[tool.hatch.build.targets.wheel]
packages = ["src/openharness", "ohmo", "solo", "wolo", "onboard"]
```

---

## 7. UI 设计规范

### 7.1 设计系统 — CSS Variables

```css
:root {
  /* 深色主题 */
  --bg-primary: #0a0a0f;
  --bg-secondary: #12121a;
  --bg-tertiary: #1a1a26;
  --bg-card: rgba(255, 255, 255, 0.03);
  --bg-card-hover: rgba(255, 255, 255, 0.06);

  /* 强调色 */
  --accent-solo: #6c5ce7;      /* 紫色 — solo 个人日志 */
  --accent-wolo: #00b894;      /* 绿色 — wolo 工作日志 */
  --accent-gradient-solo: linear-gradient(135deg, #6c5ce7, #a29bfe);
  --accent-gradient-wolo: linear-gradient(135deg, #00b894, #55efc4);
  --accent-danger: #e17055;
  --accent-warning: #fdcb6e;
  --accent-info: #74b9ff;

  /* 文本 */
  --text-primary: #e8e8ed;
  --text-secondary: #8888a0;
  --text-muted: #555566;
  --text-inverse: #0a0a0f;

  /* 情绪色板 */
  --emotion-happy: #ffd93d;
  --emotion-calm: #74b9ff;
  --emotion-sad: #636e72;
  --emotion-anxious: #fd79a8;
  --emotion-angry: #e17055;
  --emotion-excited: #ff7675;
  --emotion-neutral: #8888a0;

  /* 优先级色板 */
  --priority-high: #e17055;
  --priority-medium: #fdcb6e;
  --priority-low: #55efc4;

  /* 间距（8px 基准网格） */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;
  --space-12: 48px;
  --space-16: 64px;

  /* 圆角 */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;
  --radius-xl: 24px;
  --radius-full: 9999px;

  /* 字体 */
  --font-sans: 'Inter', -apple-system, system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;

  /* 字号（Major Third 比例） */
  --text-xs: 11px;
  --text-sm: 13px;
  --text-base: 15px;
  --text-lg: 17px;
  --text-xl: 20px;
  --text-2xl: 24px;
  --text-3xl: 30px;
  --text-4xl: 36px;

  /* 字重 */
  --weight-normal: 400;
  --weight-medium: 500;
  --weight-semibold: 600;
  --weight-bold: 700;

  /* 行高 */
  --leading-tight: 1.2;
  --leading-normal: 1.5;
  --leading-relaxed: 1.7;

  /* 阴影 */
  --shadow-xs: 0 1px 4px rgba(0, 0, 0, 0.2);
  --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.25);
  --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.3);
  --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.4);
  --shadow-glow-solo: 0 0 24px rgba(108, 92, 231, 0.2);
  --shadow-glow-wolo: 0 0 24px rgba(0, 184, 148, 0.2);

  /* 玻璃效果 */
  --glass-bg: rgba(255, 255, 255, 0.04);
  --glass-border: rgba(255, 255, 255, 0.08);
  --glass-blur: blur(16px);
  --glass-bg-strong: rgba(255, 255, 255, 0.07);

  /* 过渡 */
  --transition-fast: 120ms ease;
  --transition-base: 200ms ease;
  --transition-slow: 350ms ease;
  --transition-spring: 400ms cubic-bezier(0.34, 1.56, 0.64, 1);
}
```

### 7.2 组件视觉规范

#### Glass Card（核心卡片）
```css
.glass-card {
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  transition: background var(--transition-base),
              box-shadow var(--transition-base),
              transform var(--transition-base);
}
.glass-card:hover {
  background: var(--bg-card-hover);
  box-shadow: var(--shadow-lg), var(--shadow-glow-solo);  /* solo 模式 */
  transform: translateY(-2px);
}
```

#### Tag / Chip（标签组件）
- 圆角 `--radius-full`，内边距 `4px 10px`，字号 `--text-xs`
- 背景：`rgba(accent, 0.12)`，文字：`accent` 颜色
- 最大宽度截断 + `...`，避免过长破坏布局

#### Stat Card（数字统计卡片）
- 数字字重 `--weight-bold`，字号 `--text-3xl`，accent 色
- 副标题 `--text-sm`，`--text-secondary`
- 底部细线：`border-bottom: 2px solid accent`，宽度 `32px`
- 数字变化时：`@keyframes countUp` 淡入效果

#### Status Badge（状态标签）
```
pending   → 灰色圆点 + "待处理"
in_progress → 黄色脉冲动画圆点 + "进行中"
done      → 绿色圆点 + "已完成"
running   → 绿色脉冲 + "运行中"
stopped   → 红色圆点 + "已停止"
```

#### Button 层级
```
Primary   → accent 渐变背景，白字，hover 时 brightness(1.1)
Secondary → glass-bg，accent 描边，hover 时 bg-card-hover
Ghost     → 透明，hover 时 bg-card
Danger    → accent-danger 背景
```

#### 情绪图标映射
```
happy    → 😊  #ffd93d
calm     → 😌  #74b9ff
sad      → 😢  #636e72
anxious  → 😰  #fd79a8
angry    → 😤  #e17055
excited  → 🤩  #ff7675
neutral  → 😐  #8888a0
```

### 7.3 布局结构

```
┌──────────────────────────────────────────────────────────┐
│ ┌────────────┐  ┌──────────────────────────────────────┐ │
│ │ ┌────────┐ │  │  ╔══════════════════════════════════╗ │ │
│ │ │ SOLO / │ │  │  ║  Header: 搜索 / App状态 / 通知  ║ │ │
│ │ │ WOLO   │ │  │  ╚══════════════════════════════════╝ │ │
│ │ └────────┘ │  │                                      │ │
│ │            │  │  ┌──────────────────────────────┐   │ │
│ │  ○ 仪表盘  │  │  │                              │   │ │
│ │  ○ 条目    │  │  │      Main Content Area       │   │ │
│ │  ○ 记录    │  │  │                              │   │ │
│ │  ○ Todo   │  │  │  Dashboard / Records / Chat  │   │ │
│ │  ○ 报告    │  │  │                              │   │ │
│ │  ○ 统计    │  │  │                              │   │ │
│ │  ○ 搜索    │  │  └──────────────────────────────┘   │ │
│ │  ○ 聊天    │  │                                      │ │
│ │  ○ 设置    │  │                                      │ │
│ │            │  │                                      │ │
│ │ ──────────│  │                                      │ │
│ │ ● Gateway  │  │                                      │ │
│ │   v0.1.0   │  │                                      │ │
│ └────────────┘  └──────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

- **Sidebar**（宽度 220px，可折叠至 60px）：顶部 App 切换器带品牌色；导航项含图标+文字；底部显示 Gateway 状态指示灯（脉冲动画）+ 版本号
- **Header**（高度 56px）：左侧面包屑路径、中部全局搜索、右侧 Gateway 状态芯片 + 通知图标
- **Content**：16px padding，最大宽度 1400px，超宽屏自动居中

### 7.4 动画规范

#### 过渡原则
- 所有交互状态变化：`transition-base (200ms ease)`
- 弹性出现（模态、下拉）：`transition-spring (400ms cubic-bezier)`
- 数据刷新淡入：`transition-slow (350ms ease)`
- 严禁无意义动画，每个动画必须传递信息

#### 关键动画
```css
/* 页面切换：向右滑入 */
@keyframes slideIn {
  from { opacity: 0; transform: translateX(12px); }
  to   { opacity: 1; transform: translateX(0); }
}

/* 卡片加载骨架屏 */
@keyframes shimmer {
  from { background-position: -200% 0; }
  to   { background-position: 200% 0; }
}

/* Gateway 状态脉冲 */
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.5; transform: scale(0.85); }
}

/* 数字滚动（统计卡片）*/
@keyframes countUp {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* 流式文字光标 */
@keyframes blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0; }
}
```

### 7.5 关键页面设计说明

#### Dashboard（首页）
- **布局**：上方 4 个 Stat Card 横排（条目总数 / 记录总数 / 本周新增 / 待处理 Todo）
- **中部左侧**（60%宽）：近期活动时间线（每项带情绪图标 + 摘要 + 日期）
- **中部右侧**（40%宽）：情绪分布饼图 + Gateway 状态卡片
- **底部**：活跃度热力图（GitHub 风格，绿色/紫色 based on app）
- Solo 模式整体色调偏紫，Wolo 模式整体色调偏绿；切换时背景渐变色同步过渡

#### Records（记录列表）
- **卡片网格**（auto-fill, minmax(320px, 1fr)）
- 每卡：顶部色条（accent 色，高度 3px）+ 日期 + 情绪图标 + 摘要（2行截断）+ 底部标签行
- 悬停：`translateY(-2px)` + glow 阴影 + 查看详情按钮显现（`opacity 0→1`）
- 筛选栏：日期选择器 + 情绪多选 + 标签多选，所有筛选实时生效（防抖 300ms）

#### Chat（聊天）
- **全高分栏**：左侧消息列表（flex 1）+ 右侧输入区（底部固定，auto-resize）
- 用户消息：右对齐，accent 背景圆角气泡
- AI 回复：左对齐，glass-card，支持 Markdown 渲染
- 工具调用块：可折叠的 `<details>` 风格，单色描边，图标 + 工具名 + 参数摘要
- 流式输出：末尾光标 `▊` blink 动画；工具调用时显示"正在使用工具…"旋转指示器
- 输入框：`Cmd/Ctrl + Enter` 发送；`Shift + Enter` 换行

#### Stats（统计分析）
- **全屏图表画廊**：卡片式布局，每张图独立 glass-card
- 图表配色与 app 主题色一致（solo 紫色系，wolo 绿色系）
- Recharts 全局 dark theme：`stroke` 用 `--text-muted`，`text` 用 `--text-secondary`
- 活跃度热力图：实现 `ActivityHeatmap` 组件（52 列 × 7 行，tooltip 显示具体日期+条数）

### 7.6 空状态 & 加载状态

```
空状态（Empty State）：
  插画图标（SVG，40px，--text-muted 色）
  + 标题（"还没有记录"）
  + 副文本（"使用 solo CLI 开始记录吧"）
  + 可选操作按钮

骨架屏（Skeleton）：
  与真实内容等形状的占位块
  shimmer 动画，background: linear-gradient(90deg, glass-bg, glass-bg-strong, glass-bg)
  背景尺寸: 200% auto，持续 1.5s 循环

错误状态（Error State）：
  顶部 accent-danger 色细线
  错误摘要（text-primary）+ 可选"重试"按钮
  不展示技术堆栈给用户
```

---

## 8. 分阶段实施计划

### Phase 0: 项目脚手架 (1 step)
- [ ] 创建 `onboard/` 目录结构
- [ ] 初始化 Vite + React + TypeScript 前端
- [ ] 创建 FastAPI 后端骨架（health endpoint）
- [ ] 配置 pyproject.toml 新增 onboard 入口
- [ ] 实现 `onboard run` CLI 命令（前台启动 uvicorn）
- **验证**：`onboard run` 能启动，访问 `localhost:8090` 看到空页面

### Phase 1: 后端 API 层 (2 steps)
- [ ] 实现 solo_service.py / wolo_service.py（封装 store 读操作）
- [ ] 实现 stats API（统计聚合）
- [ ] 实现数据浏览 API（entries, records, todos, reports, decisions, highlights）
- [ ] 实现搜索 API（代理到 store.search）
- [ ] 实现 gateway 生命周期 API（start/stop/status）
- **验证**：`curl /api/solo/stats` 返回正确数据

### Phase 2: 前端设计系统 + 布局 (1 step)
- [ ] 实现 CSS 设计系统（index.css）
- [ ] 实现 Layout / Sidebar / Header 骨架组件
- [ ] 实现 Solo/Wolo 切换逻辑
- [ ] 实现 API 客户端层（client.ts, types.ts）
- [ ] 实现前端路由（React Router）
- **验证**：页面能切换路由，Sidebar 高亮正确

### Phase 3: Dashboard + 数据展示 (2 steps)
- [ ] Dashboard 页面（统计卡片、Gateway 状态、近期活动）
- [ ] Entries 浏览页面（分页、筛选）
- [ ] Records 浏览页面（卡片布局、标签、情绪图标）
- [ ] Record 详情页面
- [ ] Todos 页面（看板视图）
- [ ] Reports 页面（列表 + Markdown 渲染）
- [ ] [wolo] Decisions / Highlights 页面
- **验证**：所有数据页面正确展示真实数据

### Phase 4: 统计图表 (1 step)
- [ ] 集成 Chart.js / Recharts
- [ ] 情绪趋势折线图
- [ ] 记录量折线图（日/周/月）
- [ ] 标签热度柱状图
- [ ] 活跃度热力图（GitHub 风格）
- [ ] Todo 完成率环形图
- **验证**：图表正确渲染，交互流畅

### Phase 5: 聊天功能 (1 step)
- [ ] 后端 WebSocket 端点
- [ ] chat_service.py（集成 SoloQueryRunner / WoloQueryRunner）
- [ ] 前端 ChatPanel 组件
- [ ] 流式输出渲染
- [ ] 工具调用展示
- **验证**：能在 WebUI 中与 solo/wolo agent 对话

### Phase 6: 搜索 + 报告生成 (1 step)
- [ ] 全局搜索界面
- [ ] 搜索结果高亮
- [ ] 一键生成报告（进度展示）
- **验证**：搜索返回正确结果，报告生成流程可用

### Phase 7: CLI 集成 + 收尾 (1 step)
- [ ] `onboard start/stop/status` 命令
- [ ] `solo onboard run/start/stop` 子命令
- [ ] `wolo onboard run/start/stop` 子命令
- [ ] 前端 build → FastAPI 静态托管配置
- [ ] README.md 文档
- **验证**：所有 CLI 命令可用，`onboard start` 后台启动正常

---

## 9. 依赖新增

### Python（onboard 后端）
```
fastapi>=0.115
uvicorn[standard]>=0.30
```

### Node.js（onboard 前端）
```json
{
  "dependencies": {
    "react": "^19",
    "react-dom": "^19",
    "react-router-dom": "^7",
    "react-markdown": "^9",
    "remark-gfm": "^4",
    "recharts": "^2"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4",
    "typescript": "^5",
    "vite": "^6"
  }
}
```

---

## 10. 设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 前后端部署模式 | 分离 vs 一体 | 一体（FastAPI 托管静态文件） | 单进程、简单部署、`onboard run` 即用 |
| 数据访问方式 | 直接 import store vs HTTP 代理 | 直接 import | 同一 Python 进程，零延迟，类型安全 |
| 前端路由 | hash vs history | history（SPA fallback） | 更干净的 URL |
| 实时通信 | SSE vs WebSocket | WebSocket | 双向通信，支持取消/中断 |
| 图表库 | Chart.js vs Recharts vs D3 | Recharts | React 原生组件、声明式、足够轻量 |
| 同时支持 solo+wolo | 两个独立 app vs 统一 dashboard | 统一 dashboard | 代码复用、UX 一致 |

---

## 11. 风险与约束

1. **SQLite 并发**：solo/wolo gateway 进程可能正在写 DB，WebUI 只读访问需要 WAL 模式（已有）+ `PRAGMA busy_timeout`
2. **QueryRunner 依赖 API Key**：聊天功能需要已配置的 provider_profile
3. **前端 build**：生产部署需先 `npm run build`，开发时可 Vite dev server + FastAPI 分端口
4. **Workspace 路径**：需检测 `~/.solo` 和 `~/.wolo` 是否已初始化

---

## 12. 不做什么（Scope 边界）

- ❌ 不修改 solo/wolo 核心代码（仅新增 CLI 子命令）
- ❌ 不实现用户认证（本地工具，localhost only）
- ❌ 不做 PWA / 移动端适配（v1 仅桌面浏览器）
- ❌ 不做数据导入功能（使用现有 CLI）
- ❌ 不做配置编辑（只读展示，修改通过 `solo config` / `wolo config`）

---

## 13. TypeScript 类型系统

与后端 Pydantic 响应体严格对齐，统一定义在 `frontend/src/api/types.ts`。

```typescript
// ── App ──────────────────────────────────────────────────────────────────────
export type AppName = "solo" | "wolo";

// ── Entry ─────────────────────────────────────────────────────────────────────
export interface Entry {
  id: string;
  content: string;
  created_at: string;
  channel: string;
  sender_id: string;
  chat_id: string;
  message_id: string | null;
  metadata: Record<string, unknown> | null;
  attachments: StoredAttachment[];
}

// ── Record ────────────────────────────────────────────────────────────────────
export interface Record {
  id: string;
  entry_id: string;
  date: string;            // "YYYY-MM-DD"
  raw_content: string;
  corrected_content: string;
  summary: string;
  tags: string;            // comma-separated, parse client-side
  emotion: string;
  weekday: string;
  events: string;
  period: string;
  season: string;
  is_weekend: boolean;
  content_length: number;
  emotion_reason: string;
  related_people: string;
  related_places: string;
  source: string;
  created_at: string;
  attachments: StoredAttachment[];
}

// ── Todo ──────────────────────────────────────────────────────────────────────
export type TodoStatus = "pending" | "in_progress" | "done";
export type TodoPriority = "high" | "medium" | "low";

export interface Todo {
  id: string;
  record_id: string;
  title: string;
  category: string;
  priority: TodoPriority;
  due_date: string;
  status: TodoStatus;
  source: string;
  created_at: string;
  completed_at: string;
}

// ── Report ───────────────────────────────────────────────────────────────────
export type ReportType = "weekly" | "monthly" | "yearly";

export interface Report {
  id: string;
  report_type: ReportType;
  content: string;         // Markdown
  created_at: string;
}

// ── Wolo-only ─────────────────────────────────────────────────────────────────
export interface Decision {
  id: string;
  record_id: string;
  title: string;
  rationale: string;
  impact: string;
  project: string;
  source: string;
  created_at: string;
}

export type HighlightKind = "important" | "blocker" | "risk" | "prompt" | "tool";

export interface Highlight {
  id: string;
  record_id: string;
  kind: HighlightKind;
  title: string;
  content: string;
  project: string;
  tags: string;
  source: string;
  created_at: string;
}

// ── Attachments ───────────────────────────────────────────────────────────────
export interface StoredAttachment {
  filename: string;
  content_type: string;
  size: number;
  stored_path: string;
}

// ── Stats ─────────────────────────────────────────────────────────────────────
export interface AppStats {
  total_entries: number;
  total_records: number;
  pending_entries: number;
  total_todos: number;
  pending_todos: number;
  this_week_records: number;
  // wolo-only (undefined for solo)
  total_decisions?: number;
  total_highlights?: number;
  open_blockers?: number;
}

export interface EmotionDistribution {
  emotion: string;
  count: number;
}

export interface DailyCount {
  date: string;    // "YYYY-MM-DD"
  count: number;
}

export interface TagCount {
  tag: string;
  count: number;
}

// ── Gateway ───────────────────────────────────────────────────────────────────
export type GatewayStatusCode = "running" | "stopped" | "unknown";

export interface GatewayStatus {
  status: GatewayStatusCode;
  pid: number | null;
  uptime_seconds: number | null;
  port: number | null;
}

// ── Pagination ────────────────────────────────────────────────────────────────
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// ── Search ────────────────────────────────────────────────────────────────────
export interface SearchResult {
  records: Record[];
  total: number;
  query: string;
}

// ── WebSocket 聊天协议 ─────────────────────────────────────────────────────────
export type WsClientMessage =
  | { type: "message"; content: string }
  | { type: "cancel" };

export type WsServerMessage =
  | { type: "delta"; content: string }
  | { type: "tool_start"; tool: string; args: Record<string, unknown> }
  | { type: "tool_complete"; tool: string; result: string }
  | { type: "complete"; content: string }
  | { type: "error"; message: string };
```

---

## 14. 开发工作流

### 14.1 环境准备

```bash
# Python 后端依赖（在 OpenHarness 根目录）
uv sync --extra dev

# 前端依赖
cd onboard/frontend && npm ci
```

### 14.2 开发模式（热重载）

**方式 A：分端口（推荐）**

```bash
# Terminal 1 — FastAPI 后端（端口 8090）
uv run onboard run --reload

# Terminal 2 — Vite 前端（端口 5173，自动 proxy 到 8090）
cd onboard/frontend && npm run dev
```

Vite 配置代理（`vite.config.ts`）：
```typescript
server: {
  proxy: {
    '/api': 'http://localhost:8090',
    '/ws':  { target: 'ws://localhost:8090', ws: true },
  }
}
```
此模式前端走 `localhost:5173`，HMR 完整可用。

**方式 B：一体启动（查看构建效果）**

```bash
cd onboard/frontend && npm run build  # 产物写到 onboard/frontend/dist/
uv run onboard run                    # FastAPI 托管 dist/ + API
# 访问 http://localhost:8090
```

### 14.3 常用命令速查

```bash
# 验证后端 API
curl http://localhost:8090/api/health
curl http://localhost:8090/api/solo/stats

# 前台运行（开发）
uv run onboard run --port 8090 --reload

# 后台运行（类生产）
uv run onboard start --port 8090
uv run onboard status
uv run onboard stop

# solo / wolo 子命令
uv run solo onboard run
uv run wolo onboard run

# 类型检查
cd onboard/frontend && npx tsc --noEmit

# 前端 Lint
cd onboard/frontend && npx eslint src
```

### 14.4 FastAPI 开发技巧

- 访问 `http://localhost:8090/docs` 可查看自动生成的 Swagger UI（开发时启用）
- 所有路由返回 `application/json`；WebSocket 端点单独处理
- `--reload` 模式下修改 Python 文件自动重启

### 14.5 目录约定

```
onboard/frontend/dist/    # 构建产物，git-ignored
onboard/frontend/src/     # 源码，唯一需要编辑的前端目录
```

---

## 15. 实施前检查清单

在开始 Phase 0 之前确认：

- [ ] `~/.solo` 已初始化（运行过 `solo init`）
- [ ] `~/.wolo` 已初始化（运行过 `wolo init`）
- [ ] `uv sync --extra dev` 成功
- [ ] 端口 8090 未被占用（`lsof -ti:8090`）
- [ ] Node.js >= 18（`node --version`）
- [ ] `fastapi` / `uvicorn` 尚未在 `pyproject.toml` 中（避免重复添加）
