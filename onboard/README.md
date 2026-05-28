# Onboard

Solo/Wolo 统一 Web 仪表盘 — 浏览日志、查看统计、生成报告、实时聊天，一个页面搞定。

## 快速开始

```bash
# 安装依赖
cd onboard/frontend && npm ci && cd ../..
uv sync --extra dev

# 构建前端
cd onboard/frontend && npm run build && cd ../..

# 前台启动
uv run python -m onboard run

# 或后台启动
uv run python -m onboard start
```

启动后终端会输出：

```
  🔑 Access token: aBcDeFgHiJkLmNoPqRsTuVwX
  🔗 Direct link:  http://0.0.0.0:8090?token=aBcDeFgHiJkLmNoPqRsTuVwX
```

在浏览器中打开 Direct link 即可自动认证进入应用。

## CLI 命令

| 命令 | 说明 |
|------|------|
| `onboard run` | 前台启动（`--host`, `--port`, `--reload`） |
| `onboard start` | 后台启动 |
| `onboard stop` | 停止后台进程 |
| `onboard status` | 查看运行状态 |
| `onboard token` | 显示当前 access token |
| `onboard token --reset` | 重置 token（所有已登录会话失效） |

## 访问认证

Onboard 使用 **Token Gate** 机制保护私人数据：

- 首次启动时自动生成随机 token，持久存储于 `~/.onboard/secret`
- 浏览器访问时需输入 token，验证通过后设置 30 天有效的 session cookie
- Token 不会随重启改变，除非手动 `onboard token --reset` 或删除 secret 文件
- 支持 URL 参数一次性认证：`http://host:port?token=xxx`（自动设置 cookie 后跳转）

### Token 丢失恢复

```bash
# 方式 1：CLI 查看
onboard token

# 方式 2：直接读文件
cat ~/.onboard/secret

# 方式 3：删除文件后重启，自动生成新 token
rm ~/.onboard/secret
onboard stop && onboard start
```

## 功能概览

### 📊 Dashboard
- 条目/记录总数、本周新增、待处理数
- Gateway 运行状态与生命周期管理
- 情绪趋势图、标签云、Todo 进度

### 📝 数据浏览
- **Entries** — 原始日志条目，按时间/频道筛选
- **Records** — 结构化记录，支持标签、情绪、日期筛选
- **Decisions** — [wolo] 决策记录
- **Highlights** — [wolo] 高亮与阻塞项

### ✅ Todos
- 查看待办事项，按状态/分类过滤
- 一键标记完成

### 📈 Reports
- 查看已生成的分析报告（Markdown 渲染）
- 在线触发报告生成（周报、月报等）

### 💬 Chat
- WebSocket 实时对话，流式输出
- 聊天会话历史浏览与导出（Markdown/HTML）

### 🔍 Search
- 全文搜索记录、条目

### ⚙️ Settings
- 查看当前 solo/wolo 配置

## 技术栈

| 层 | 选型 |
|----|------|
| 前端 | Vite + React 19 + TypeScript + Tailwind CSS |
| 图表 | Recharts |
| Markdown | react-markdown + remark-gfm |
| 后端 | FastAPI + uvicorn |
| 实时通信 | WebSocket |
| 部署 | 前端 build 产物由 FastAPI 静态托管，单进程 |

## 项目结构

```
onboard/
├── __init__.py
├── __main__.py          # python -m onboard 入口
├── cli.py               # typer CLI
├── server.py            # FastAPI app + uvicorn 生命周期
├── auth.py              # Token Gate 认证中间件
├── api/
│   ├── solo_routes.py   # Solo REST API
│   ├── wolo_routes.py   # Wolo REST API
│   ├── chat.py          # WebSocket 聊天
│   ├── lifecycle.py     # Gateway 管理
│   └── stats.py         # 统计聚合
├── services/
│   ├── solo_service.py  # 封装 solo store
│   ├── wolo_service.py  # 封装 wolo store
│   └── chat_service.py  # 聊天会话管理
└── frontend/            # Vite + React
    ├── src/
    │   ├── api/         # API 客户端 + 类型定义
    │   ├── components/  # 通用 UI 组件
    │   ├── pages/       # 页面组件
    │   └── hooks/       # 自定义 React hooks
    └── dist/            # 构建产物（gitignored）
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ONBOARD_WORKSPACE` | `~/.onboard` | 数据/配置目录 |

## 开发

```bash
# 前端开发模式（热更新）
cd onboard/frontend && npm run dev

# 后端开发模式（自动重载）
uv run python -m onboard run --reload

# 类型检查
cd onboard/frontend && npx tsc --noEmit
```
