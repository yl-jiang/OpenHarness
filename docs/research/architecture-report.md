# OpenHarness 全面架构研究报告

> **项目**: OpenHarness (v0.1.7) + ohmo  
> **根目录**: `/home/uih/F/JYL/Github/OpenHarness/`  
> **日期**: 2026-05-07  
> **研究方式**: 6 个子代理并行调研后综合

---

## 目录

1. [项目概览](#1-项目概览)
2. [整体架构蓝图](#2-整体架构蓝图)
3. [核心引擎层 (Engine)](#3-核心引擎层-engine)
4. [工具系统 (Tool System)](#4-工具系统-tool-system)
5. [技能系统 (Skills)](#5-技能系统-skills)
6. [插件系统 (Plugins)](#6-插件系统-plugins)
7. [钩子系统 (Hooks)](#7-钩子系统-hooks)
8. [多 Provider 认证与 API](#8-多-provider-认证与-api)
9. [MCP 客户端](#9-mcp-客户端)
10. [权限系统 (Permissions)](#10-权限系统-permissions)
11. [Sandbox 沙箱](#11-sandbox-沙箱)
12. [Swarm 多智能体协调](#12-swarm-多智能体协调)
13. [任务系统 (Tasks)](#13-任务系统-tasks)
14. [内存系统 (Memory)](#14-内存系统-memory)
15. [IM 渠道系统 (Channels)](#15-im-渠道系统-channels)
16. [CLI 与命令系统](#16-cli-与命令系统)
17. [UI 层 (React Ink TUI + Textual)](#17-ui-层-react-ink-tui--textual)
18. [ohmo 个人代理应用](#18-ohmo-个人代理应用)
19. [桥接系统 (Bridge)](#19-桥接系统-bridge)
20. [配置与状态管理](#20-配置与状态管理)
21. [设计思想总结](#21-设计思想总结)

---

## 1. 项目概览

OpenHarness 是一个 **开源 Agent Harness 框架**，为 LLM 提供"手、眼、记忆和安全边界"。它不是一个模型，而是一个完整的 Agent 基础设施。

```
Harness = Tools + Knowledge + Observation + Action + Permissions
```

| 组件 | 行数 (approx.) | 说明 |
|---|---|---|
| `src/openharness/` | ~25,000 | 核心 Harness 框架 |
| `ohmo/` | ~3,600 | 个人代理应用 |
| `frontend/terminal/` | ~2,000 | React Ink TUI |
| `tests/` | ~15,000 | 909 个测试 |

**三条 CLI 入口**：
- `oh` — OpenHarness 主 CLI (typer)
- `openh` — Windows 兼容别名
- `ohmo` — 个人代理 CLI

---

## 2. 整体架构蓝图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         oh CLI (Typer)                              │
│  oh setup │ oh mcp │ oh plugin │ oh auth │ oh provider │ oh cron   │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────────┐
│                    Runtime 调度层 (cli.py)                           │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐    │
│  │TUI 模式 │  │Print 模式│  │Dry-run  │  │Backend-only 模式│    │
│  │ React   │  │text/json │  │preview   │  │(headless host)  │    │
│  └────┬────┘  └──────────┘  └──────────┘  └────────┬─────────┘    │
└───────┼─────────────────────────────────────────────┼──────────────┘
        │                                             │
┌───────▼─────────────────────────────────────────────▼──────────────┐
│                        QueryEngine (engine/)                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                   Guard Loop (外循环)                         │  │
│  │  _stream_query_with_guards()                                 │  │
│  │  • Auto-continue detection    • Cost tracking               │  │
│  │  • Max turn budget           • Silent stop detection        │  │
│  └─────────────────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                   Stage Pipeline (内循环)                     │  │
│  │  PreTurn → Compact → Preprocess → APICall → ResponseRouting │  │
│  │  → DoneGate → ToolExecution → PostTool                      │  │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   工具/技能/插件/钩子 层                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ 40+ Tools│  │  Skills  │  │  Plugins │  │ Hooks (pre/post)│   │
│  │File,Shell│  │SkillMgr  │  │Bundled/  │  │PreToolUse       │   │
│  │Search,Web│  │Load/Reg  │  │External  │  │PostToolUse      │   │
│  │Git,MCP.. │  │.md skill │  │installer │  │PreQuery/Post..  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   横向支撑系统                                        │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────────┐   │
│  │Auth  │ │ API  │ │ MCP  │ │Perm  │ │Swarm │ │ Sandbox      │   │
│  │11个  │ │Client│ │Stdio │ │多层  │ │Sub-  │ │ Docker       │   │
│  │提供方│ │Stream│ │HTTP  │ │模式  │ │agent │ │ 隔离执行     │   │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────────────┘   │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────────┐   │
│  │Memory│ │Chnl  │ │Brdge │ │Tasks │ │Cron  │ │Personalize   │   │
│  │MEMORY│ │10 IM │ │Sess. │ │BG/FG │ │定时  │ │规则提取      │   │
│  │.md   │ │渠道  │ │Bridge│ │Manager│ │任务  │ │              │   │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心引擎层 (Engine)

**文件**: `src/openharness/engine/`

### 3.1 双层循环架构

OpenHarness 的 Agent Loop 采用**双层流式架构**：

#### 外层：Guard Loop (`query_engine.py:237-365`)

`QueryEngine._stream_query_with_guards()` 是外层循环，调用内层 `run_query()` 后检查结果进行**自动继续 (auto-continue)**：

```python
# 关键机制: 静默停止检测
_MAX_CONSECUTIVE_SILENT_STOPS = 1   # 连续静默停止上限
_MAX_AUTO_CONTINUE_ABSOLUTE = 5     # 绝对上限，防止失控循环
```

- 驱动单次用户输入内的多轮循环
- 检测"工具执行后无可见结果"的情况，自动注入继续提示
- 累积成本跟踪，消息历史 checkpoint

#### 内层：Stage Pipeline (`turn_stages.py:589-598`)

8 个阶段组成固定管线：

```python
DEFAULT_TURN_STAGES = (
    pre_turn_stage,         # Token clamp 警告（一次性）
    compact_stage,          # 自动压缩检查（异步执行）
    preprocess_stage,       # 图片→文字转换 + 消息规范化
    api_call_stage,         # 流式 LLM 调用 + 错误处理/重试
    response_routing_stage, # 处理响应，路由到工具或退出
    done_gate_stage,        # 强制执行 done() 单独调用
    tool_execution_stage,   # 执行工具（单次或并行）
    post_tool_stage,        # 追加结果，检测 done() 终止
)
```

### 3.2 单轮生命周期

1. **`run_query()` 入口** (`query.py:661-696`): 初始化 `TurnState`，进入 `while` 循环（默认最多 8 轮，`QueryContext` 中最多 200 轮）

2. **PreTurn** (`turn_stages.py:111-120`): 当 `max_tokens` 因 provider 限制被 clamp 时发出一次性警告

3. **Compact** (`turn_stages.py:127-180`): 在异步任务中运行 `auto_compact_if_needed()`，通过进度队列流式输出 `CompactProgressEvent`。压缩后重新注入 todo-store 上下文并清除文件读取缓存

4. **Preprocess** (`turn_stages.py:186-193`): 将用户图片块转换为文字描述（对纯文本模型），合并连续纯文本用户消息

5. **API Call** (`turn_stages.py:200-310`): 
   - 支持流式 (`stream=True`) 和非流式两种模式
   - 指数退避重试（`_api_call_with_retry`）
   - `StreamInterrupted` 检测：如果流在前 3 秒内没有任何事件，触发重试

6. **Response Routing** (`turn_stages.py:312-394`): 
   - 检测 `input_should_send`（让模型选择是否继续）
   - Text-only 响应 → 标记完成
   - Tool-use 响应 → 进入工具执行阶段

7. **DoneGate** (`turn_stages.py:396-434`): 确保 `done()` 必须单独调用（不能与其他工具一起调用）

8. **Tool Execution** (`turn_stages.py:436-542`):
   - 使用 `ToolPipeline` 执行工具
   - 如果存在多个并行工具（同属一个 tool_use_group），**并行执行**
   - 如果存在串行工具组，顺序执行

9. **PostTool** (`turn_stages.py:544-580`): 将工具结果追加到消息历史，检测 `done()` 终止信号

### 3.3 流式事件架构 (`stream_events.py`)

所有流式事件继承自 `StreamEvent`：

```
StreamEvent
├── StatusEvent                    # 状态消息（info/warning/error/done）
├── StreamFinished                 # 流结束（含完整消息和 token 用量）
├── CompactPhaseProgress           # 压缩进度
├── CompactProgressEvent           # 压缩进度块
├── AssistantTurnComplete          # 助手回合完成
├── ToolStreamEvent                # 工具流事件
│   ├── ToolUseEvent               # 工具开始使用
│   ├── ToolInputEvent             # 工具输入
│   └── ToolResultEvent            # 工具结果
```

### 3.4 Tool Pipeline (`tool_pipeline.py`)

`ToolPipeline` 使用策略模式组合多个处理阶段：

```
ToolPipeline 阶段流程:
1. tool_resolution   — 解析 tool_use_id → BaseTool 实例
2. permission_check  — 检查权限模式、路径规则、命令规则
3. tool_execution    — 调用 tool.execute()
4. result_normalize  — 标准化结果输出（截断、转义）
5. repair_loop       — 工具调用出错时自动修复并重试
```

### 3.5 成本跟踪 (`cost_tracker.py`)

```python
class CostTracker:
    session_cost: float           # 当前会话成本
    session_input_tokens: int
    session_output_tokens: int
    turn_input_tokens: int
    turn_output_tokens: int
```

支持多种计费模型配置，跟踪每次 API 调用的 token 用量和费用。

### 3.6 消息管理 (`messages.py`)

```python
@dataclass
class ConversationMessage:
    role: Literal["user", "assistant", "system", "tool_result"]
    content: list[ContentBlock]
    # tool_use_id, metadata...

@dataclass
class TextBlock: ...
@dataclass
class ToolUseBlock: ...
@dataclass
class ToolResultBlock: ...
```

支持文本、工具调用、工具结果、图片等多模态内容块。

---

## 4. 工具系统 (Tool System)

**文件**: `src/openharness/tools/`

### 4.1 核心接口 (`base.py`)

```python
class BaseTool(ABC):
    name: str
    description: str
    input_model: type[BaseModel]    # Pydantic 参数模型

    @abstractmethod
    async def execute(self, arguments, context) -> ToolResult
    def is_read_only(self, arguments) -> bool
    def to_api_schema(self) -> dict[str, Any]
```

### 4.2 工具分类 (40+ 工具)

| 类别 | 工具 | 数量 |
|---|---|---|
| **文件操作** | `read_file`, `write_file`, `edit_file`, `glob`, `grep` | 5 |
| **Shell** | `bash` | 1 |
| **搜索** | `grep_tool`, `tool_search`, `web_search`, `web_fetch` | 4 |
| **Git/Worktree** | `enter_worktree`, `exit_worktree` | 2 |
| **MCP** | `mcp_tool`, `list_mcp_resources`, `read_mcp_resource`, `mcp_auth_tool` | 4 |
| **任务管理** | `task_create/get/list/output/stop/update/wait` | 7 |
| **团队/代理** | `team_create/delete`, `agent_tool`, `send_message` | 4 |
| **Swarm** | `todo_tool`, `plan_mode`, `sleep`, `done`, `config_tool` | 5 |
| **工具/技能管理** | `skill_manager`, `cron_manager`, `remote_trigger` | 3 |
| **系统/其他** | `bash`, `brief`, `lsp`, `notebook_edit`, `image_to_text`, `memory_tool`, `ask_user_question` | 7 |
| **内部** | `list_mcp_resources`, `read_mcp_resource` | 2 |

### 4.3 ToolRegistry (`base.py:76-109`)

```python
class ToolRegistry:
    def register(self, tool: BaseTool)
    def unregister(self, name: str)
    def get(self, name: str) -> BaseTool | None
    def list_tools(self) -> list[BaseTool]
    def to_api_schema(self) -> list[dict]   # 缓存，版本变化时重新生成
```

- 版本化、缓存化的工具注册表
- 每个工具注册/注销时自增版本计数器，自动使 schema 缓存失效

### 4.4 只读检测 (`base.py: is_read_only`)

每个工具可以实现 `is_read_only()` 方法。系统根据此方法在 plan 模式下只允许只读工具执行。已实现的只读工具：`read_file`, `glob`, `grep`, `web_search`, `web_fetch`, `tool_search`, `list_mcp_resources`, `read_mcp_resource`, `task_list`, `task_get`, `task_output`, `sleep`, `config_tool`, `brief`, `tool_search`, `lsp`。

---

## 5. 技能系统 (Skills)

**文件**: `src/openharness/skills/`

### 5.1 架构

| 文件 | 功能 |
|---|---|
| `types.py` | `SkillDefinition`, `SkillCollection` 类型定义 |
| `registry.py` | `SkillRegistry` — 技能注册表 |
| `loader.py` | `load_skills()` — 从磁盘加载 .md 技能文件 |
| `metadata.py` | `SkillMetadata` — 技能元数据解析 |
| `bundled/` | 内置技能包 |

### 5.2 技能发现流程

```
load_skills()
  → 扫描 bundled 目录
  → 扫描 ~/.openharness/skills/ 用户目录
  → 扫描项目 .claude/skills/
  → 每个 .md 文件解析 frontmatter → SkillMetadata
  → 注册到 SkillRegistry
```

### 5.3 SKILL.md 格式

技能文件是 Markdown 文件，包含 YAML frontmatter：

```yaml
---
name: my-skill
description: 技能描述
trigger: 触发词
---
# 技能内容
...

[工具调用指南、工作流等]
```

系统会在系统提示词中注入与当前任务相关的技能内容。

---

## 6. 插件系统 (Plugins)

**文件**: `src/openharness/plugins/`

### 6.1 架构

| 文件 | 功能 |
|---|---|
| `types.py` | `PluginMetadata`, `PluginContext`, `PluginAPI` |
| `loader.py` | `load_plugins()` — 扫描和加载插件 |
| `installer.py` | `install_plugin()` — 安装插件 |
| `schemas.py` | 插件配置 schema |
| `bundled/` | 内置插件 |

### 6.2 插件生命周期

```
发现阶段:
  1. 扫描 built-in 插件目录
  2. 扫描 ~/.openharness/plugins/
  3. 扫描项目 .openharness/plugins/

加载阶段:
  1. 执行每个插件的 __init__.py
  2. 收集 plugin metadata
  3. 注册插件提供的工具到 ToolRegistry
  4. 注册插件提供的钩子到 HookExecutor

运行阶段:
  插件可以:
  - 提供自定义工具 (PluginTool)
  - 注册 PreQuery/PostQuery/PreTool/PostTool 钩子
  - 访问 PluginContext (含 settings, cwd, session_id)
```

### 6.3 插件 vs 技能

| 方面 | 技能 (Skills) | 插件 (Plugins) |
|---|---|---|
| 本质 | Markdown 提示词 | Python 代码 |
| 提供 | 指令、工作流 | 工具、钩子 |
| 安装 | 文件复制 | pip install 或复制 |
| 作用时机 | 提示词注入 | 运行时加载 |

---

## 7. 钩子系统 (Hooks)

**文件**: `src/openharness/hooks/`

### 7.1 架构

```python
# 事件类型 (events.py)
class HookEvent(Enum):
    PRE_QUERY = "pre_query"           # 查询前
    POST_QUERY = "post_query"         # 查询后
    PRE_TOOL_USE = "pre_tool_use"     # 工具执行前
    POST_TOOL_USE = "post_tool_use"   # 工具执行后
    QUERY_STREAM = "query_stream"     # 流式数据
    SESSION_START = "session_start"   # 会话开始
    SESSION_END = "session_end"       # 会话结束
    COMPACT_START = "compact_start"   # 压缩开始
    COMPACT_FINISH = "compact_finish" # 压缩完成
```

### 7.2 HookExecutor (`executor.py`)

```python
class HookExecutor:
    def execute(self, event: HookEvent, context: HookContext) -> AsyncIterator[HookResult]
```

- 支持多个钩子按优先级排序执行
- 每个钩子可以：
  - `allow` — 继续执行
  - `deny(message)` — 阻止操作
  - `modify(data)` — 修改请求/响应数据
  - `async_task(coroutine)` — 启动后台异步任务

### 7.3 热重载 (`hot_reload.py`)

使用 `watchfiles` 监控插件目录的文件变化，当插件代码更新时自动重新加载，无需重启。

---

## 8. 多 Provider 认证与 API

**文件**: `src/openharness/auth/`, `src/openharness/api/`

### 8.1 支持的 Provider (11 个)

| Provider | Profile | 认证方式 |
|---|---|---|
| `anthropic` | `claude-api` | API Key |
| `anthropic_claude` | `claude-subscription` | Claude CLI 凭证桥接 |
| `openai` | `openai-compatible` | API Key / Base URL |
| `openai_codex` | `codex` | Codex CLI 凭证桥接 |
| `copilot` | `copilot` | GitHub OAuth |
| `moonshot` | `moonshot` | API Key |
| `gemini` | `gemini` | API Key |
| `deepseek` | `deepseek` | API Key |
| `dashscope` | `qwen` | API Key |
| `minimax` | `minimax` | API Key |
| `bedrock` / `vertex` | — | AWS/GCP 原生 |

### 8.2 认证解析流程 (`config/settings.py:695`)

```
Settings.resolve_auth():
  1. Subscription 来源 → 读取外部凭证文件，自动刷新 OAuth token
  2. Copilot → 返回 "copilot-managed" 哨兵值
  3. API Key 来源 → 优先级:
     a. Profile 级 credential_slot
     b. 环境变量 (ANTHROPIC_API_KEY, OPENAI_API_KEY 等)
     c. Settings 实例的 api_key 字段
     d. 文件凭证存储 (~/.openharness/credentials.json)
```

### 8.3 API 客户端抽象

```python
# api/client.py
class SupportsStreamingMessages(Protocol):
    """所有 API 客户端实现的协议"""
    async def stream_messages(self, ...) -> AsyncIterator[StreamEvent]
    async def send_messages(self, ...) -> Message  # 非流式
```

**实现**:
- `api/openai_client.py` — OpenAI 兼容客户端（含流式、思考 tokens）
- `api/copilot_client.py` — GitHub Copilot 客户端
- `api/codex_client.py` — OpenAI Codex 客户端

### 8.4 跨 Provider 兼容

`platforms.py` 定义了各 Provider 的兼容参数：
- 支持 `max_tokens` 限制不同（如 Gemini 最高 8K）
- 支持 `thinking` / `reasoning_effort` 参数
- 支持图片输入（Claude 原生、OpenAI via base64）
- 自动适配请求格式差异

---

## 9. MCP 客户端

**文件**: `src/openharness/mcp/`

### 9.1 架构

| 文件 | 功能 |
|---|---|
| `types.py` | `McpServerConfig`, `McpTool` 类型 |
| `client.py` | `McpClientManager` — 管理 MCP 连接 |
| `config.py` | `load_mcp_server_configs()` — 加载配置 |

### 9.2 支持两种传输协议

1. **Stdio 传输** — 通过子进程启动 MCP 服务器（如 `npx @anthropic/mcp-server`）
2. **HTTP 传输** — 连接远程 MCP 服务器（SSE 或直接 HTTP）

### 9.3 关键特性

- **自动重连**: 断开时自动重连
- **工具发现**: 从 MCP 服务器发现的工具自动注册到 ToolRegistry
- **资源读取**: 支持 `list_resources` 和 `read_resource`
- **JSON Schema 推断**: MCP 工具输入自动从 JSON Schema 映射

---

## 10. 权限系统 (Permissions)

**文件**: `src/openharness/permissions/`

### 10.1 三层权限模式

```python
class PermissionMode(Enum):
    DEFAULT = "default"          # 交互审批
    PLAN = "plan"                # 只读模式（仅允许只读工具）
    FULL_AUTO = "full_auto"      # 完全自动（跳过审批）
```

### 10.2 权限检查器 (`checker.py`)

```python
class PermissionChecker:
    def check_tool(self, tool_name, args, mode) -> PermissionVerdict
    def check_path(self, path, mode) -> bool
    def check_command(self, command, mode) -> bool
```

检查维度：
- **工具级别**: 工具是否在白名单/黑名单中
- **路径级别**: 敏感路径保护（内置 `/etc`, `/sys`, `.env` 等保护）
- **命令级别**: `bash` 命令安全检查（注入检测、危险命令拦截）
- **URL 级别**: `web_fetch` URL 验证保护

### 10.3 交互审批对话框

在 `DEFAULT` 模式下，每个工具调用前弹出交互式审批对话框（Rich/Prompt toolkit），用户可以选择允许/拒绝/修改参数。

---

## 11. Sandbox 沙箱

**文件**: `src/openharness/sandbox/`

### 11.1 Docker Sandbox

| 组件 | 功能 |
|---|---|
| `docker_backend.py` | Docker 容器管理（创建、执行、清理） |
| `docker_image.py` | 镜像管理（构建、拉取、缓存） |
| `session.py` | 沙箱会话管理 |
| `adapter.py` | 统一沙箱适配器接口 |
| `path_validator.py` | 路径映射和验证 |

### 11.2 沙箱工作流

```
1. 选择/构建 Docker 镜像（支持自定义）
2. 创建隔离容器（挂载工作目录，网络控制）
3. 在容器中执行 bash 命令
4. 收集输出和退出码
5. 可选择持久化容器或每次新建
```

---

## 12. Swarm 多智能体协调

**文件**: `src/openharness/swarm/`

### 12.1 架构概览 (11 个文件)

```
swarm/
├── types.py              # BackendType, PaneBackend 协议
├── registry.py           # TeamRegistry — 团队注册表
├── in_process.py         # 进程内子代理
├── subprocess_backend.py # 子进程代理后端
├── spawn_utils.py        # 代理生成工具
├── team_lifecycle.py     # 团队生命周期管理
├── permission_sync.py    # 权限同步
├── mailbox.py            # 消息邮箱
├── worktree.py           # Git worktree 管理
├── lockfile.py           # 锁文件管理
└── __init__.py
```

### 12.2 两种代理后端

| 特性 | In-Process | Subprocess |
|---|---|---|
| 通信 | 直接协程调用 | 子进程管道 |
| 隔离性 | 共享进程空间 | 独立进程 |
| 性能 | 低延迟 | 中等延迟 |
| 适用场景 | 内部工具调用 | 独立工作代理 |

### 12.3 团队生命周期

```
team_create(name, description)
  → TeamRegistry.register(team)
  → spawn_utils.spawn_agent(team, agent_config)
    → SubprocessBackend: fork 子进程 + 建立管道
    → InProcessBackend: 创建协程任务
  → mailbox 建立消息通道
  → worktree 可选创建 git worktree
  → permission_sync 同步权限模式
```

### 12.4 子进程通信架构

```
Leader 进程                    Subprocess Agent
    │                              │
    ├── spawn(agent_config) ──────►│
    │                              │ (加载配置、初始化引擎)
    │◄── ready ────────────────────┤
    │                              │
    ├── send_message(task) ───────►│
    │                              │ (执行任务、调用工具)
    │◄── stream_events ────────────┤
    │                              │
    ├── permission_request ───────►│ (权限同步)
    │◄── permission_verdict ───────┤
    │                              │
    └── stop ────────────────────► │
```

### 12.5 Coordinator 模式 (`coordinator/`)

Coordinator 模式允许多个 Agent 协作完成复杂任务：
- `coordinator_mode.py` — 协调器模式实现
- `agent_definitions.py` — Agent 角色定义
- 自动分解任务、分配给子代理、汇总结果

---

## 13. 任务系统 (Tasks)

**文件**: `src/openharness/tasks/`

### 13.1 架构

```python
# types.py
@dataclass
class Task:
    id: str
    type: TaskType          # SHELL | LOCAL_AGENT
    status: TaskStatus      # PENDING | RUNNING | COMPLETED | FAILED | KILLED
    command: str | None
    result: str | None
    created_at: datetime
```

### 13.2 两种任务类型

| 类型 | 类 | 说明 |
|---|---|---|
| Shell 任务 | `LocalShellTask` | 异步执行 shell 命令，实时输出 |
| Agent 任务 | `LocalAgentTask` | 在子进程中运行完整 Agent 循环 |

### 13.3 TaskManager (`manager.py`)

```python
class TaskManager:
    def create_task(self, task_type, command) -> Task
    def get_task(self, task_id) -> Task
    def list_tasks(self, status=None) -> list[Task]
    def stop_task(self, task_id)
    def wait_for_task(self, task_id, timeout)
```

- 全局单例 `_task_manager`
- 子进程任务通过 stdin/stdout JSON 行协议通信
- 支持超时停止

---

## 14. 内存系统 (Memory)

**文件**: `src/openharness/memory/`

### 14.1 架构

```
memory/
├── memdir.py       # MEMORY.md 目录管理
├── manager.py      # MemoryManager — 统一接口
├── store.py        # 持久化存储（文件 + SQLite）
├── scan.py         # 扫描和发现
├── search.py       # 语义搜索
├── paths.py        # 路径管理
├── types.py        # MemoryEntry, MemoryMetadata
├── lifecycle.py    # 记忆生命周期
└── providers.py    # 存储后端抽象
```

### 14.2 关键特性

- **MEMORY.md**: 项目级持久化记忆文件，AI 可读写
- **记忆目录**: `~/.openharness/memory/` 存储持久化记忆条目
- **自动上下文注入**: 扫描与当前任务相关的记忆并注入系统提示
- **搜索**: 基于关键词和语义的混合搜索
- **生命周期**: 记忆条目有创建时间、访问频率和过期策略

### 14.3 记忆注入

在每次查询前，MemoryManager 扫描记忆存储，提取与当前上下文相关的记忆，以系统消息形式注入对话历史。

---

## 15. IM 渠道系统 (Channels)

**文件**: `src/openharness/channels/`

### 15.1 支持 10 个 IM 渠道

| 渠道 | 文件 | 协议 |
|---|---|---|
| Feishu | `feishu.py` | Lark OAPI SDK |
| Slack | `slack.py` | Slack SDK |
| Telegram | `telegram.py` | python-telegram-bot |
| Discord | `discord.py` | discord.py |
| 钉钉 | `dingtalk.py` | 钉钉 API |
| 企业微信 | `mochat.py` | 企业微信 API |
| QQ | `qq.py` | QQ 机器人 API |
| Matrix | `matrix.py` | Matrix 协议 |
| WhatsApp | `whatsapp.py` | WhatsApp API |
| Email | `email.py` | IMAP/SMTP |

### 15.2 适配器模式 (`adapter.py`)

```python
class ChannelAdapter(ABC):
    @abstractmethod
    async def send_message(self, channel_id, message)
    @abstractmethod
    async def receive_events(self) -> AsyncIterator[ChannelEvent]
    @abstractmethod
    async def start(self)
    @abstractmethod
    async def stop(self)
```

### 15.3 事件总线 (`bus/`)

```
queue.py — AsyncQueue (asyncio.Queue 封装)
events.py — ChannelEvent 类型定义
  ├── MessageEvent      # 用户消息
  ├── CommandEvent      # 斜杠命令
  ├── AttachmentEvent   # 附件上传
  └── SystemEvent       # 系统事件
```

### 15.4 消息流

```
IM 平台 → ChannelAdapter → EventBus → Bridge → QueryEngine → Tools
                                         ↓
IM 平台 ← ChannelAdapter ← EventBus ← Bridge ← 流式响应
```

---

## 16. CLI 与命令系统

**文件**: `src/openharness/cli.py`

### 16.1 CLI 子命令树

```
oh [OPTIONS] [PROMPT]...        # 交互会话（默认）
   setup                        # 交互式 Provider 配置向导
   mcp  (list|add|remove)       # MCP 服务器管理
   plugin (list|install|uninstall)  # 插件管理
   auth (login|status)          # 认证管理
   provider                     # Provider 配置管理
   cron (start|stop|status|list|toggle|history|logs)  # 定时任务管理
```

### 16.2 主要 CLI 选项 (约 40 个)

| 面板 | 关键选项 |
|---|---|
| **会话** | `--continue/-c`, `--resume/-r`, `--name/-n` |
| **模型** | `--model/-m`, `--effort`, `--max-turns`, `--verbose` |
| **输出** | `--print/-p`, `--output-format` (text/json/stream-json), `--dry-run` |
| **权限** | `--permission-mode` (default/plan/full_auto), `--allowed-tools`, `--disallowed-tools` |
| **系统** | `--system-prompt/-s`, `--settings`, `--base-url`, `--api-key`, `--theme` |

### 16.3 运行时模式调度 (`cli.py:1306`)

```
Subcommand invoked? → 委托到子命令
--dry-run → cli_dry_run.build_dry_run_preview()
--continue/--resume → 从存储加载会话
--backend-only → 启动后端主机（React TUI 连接）
--print/-p → 一次性非交互模式
默认 → 启动交互式会话
```

### 16.4 斜杠命令 (`commands/`)

```python
# registry.py
class CommandRegistry:
    def register(self, name, handler, description)
    def dispatch(self, command_line) -> bool

# 内置命令
/memory   → 记忆管理
/skills   → 技能列表/加载
/compact  → 手动压缩
/clear    → 清除历史
/tokens   → Token 用量
/doctor   → 诊断
/model    → 切换模型
```

### 16.5 Dry-Run 模式 (`cli_dry_run.py`)

`oh --dry-run` 预览所有解析后的配置而不执行：
- 运行时配置预览
- 认证状态和有效性检查
- 可用技能列表
- 注册的命令
- 可用工具和 MCP 服务器
- **就绪状态评估** (ready/warning/blocked) 和行动建议

---

## 17. UI 层 (React Ink TUI + Textual)

### 17.1 前端技术栈

| 技术 | 用途 |
|---|---|
| React Ink | 终端 UI 渲染 |
| TypeScript | 前端类型安全 |
| IPC (JSON lines) | 前后端通信 |

### 17.2 React 组件层次

```
App
├── AlternateScreen      # 备用屏幕缓冲
├── WelcomeBanner        # 欢迎界面
├── TranscriptPane       # 对话转录面板
│   ├── ConversationView # 对话视图
│   │   └── MarkdownText # Markdown 渲染
│   └── InlineActivityIndicator # 活动指示器
├── SidePanel            # 侧边面板
│   └── SwarmPanel       # Swarm 状态面板
├── Composer             # 输入编辑器
│   └── ExpandedComposer # 扩展输入
├── CommandPicker        # 命令选择器
├── StatusBar            # 状态栏
│   ├── model, tokens, cost
│   └── permission mode indicator
├── ToolCallDisplay      # 工具调用展示（树形连接线）
├── TodoPanel            # 任务面板
├── ModalHost            # 模态框
│   ├── PermissionDialog # 权限审批弹窗
│   └── SelectModal      # 选择模态框
└── Footer               # 页脚
```

### 17.3 前后端通信协议 (`protocol.py`)

前后端通过 JSON lines over stdin/stdout 通信：

```python
# backend → frontend 事件
TerminalOutput(content, style)     # 终端输出
StreamEvent(type, data)           # 流式事件
StateUpdate(key, value)           # 状态更新
PromptRequest(context)            # 输入请求

# frontend → backend 事件
UserInput(text)                   # 用户输入
PermissionResponse(verdict)       # 权限响应
Command(command, args)            # 命令
Resize(rows, cols)                # 终端大小变化
```

### 17.4 Textual 回退 UI (`textual_app.py`)

当 React Ink 不可用时，Textual 作为回退 TUI。功能较简单但提供基本的交互体验。

---

## 18. ohmo 个人代理应用

**文件**: `ohmo/`

### 18.1 ohmo 定位

ohmo 是构建在 OpenHarness 之上的**个人 AI 代理应用**，而非 OpenHarness 的分支：

| 方面 | OpenHarness (核心) | ohmo (应用) |
|---|---|---|
| 角色 | 通用 Agent Harness 框架 | 个人代理应用 |
| 入口 | `oh` CLI | `ohmo` CLI |
| 配置 | 项目配置驱动 | `~/.ohmo/` 工作空间 |
| 人格 | 通用系统提示 | `soul.md` + `identity.md` + `user.md` |
| 会话 | CWD 范围 | 工作空间范围，多 key |
| 渠道 | 提供库级别渠道实现 | Gateway 服务连接渠道→运行时 |
| 记忆 | 项目级内存 | 个人记忆 + `/memory` 命令 |
| 分发 | 库 | 应用 (`python -m ohmo`) |

### 18.2 工作空间结构 (`workspace.py`)

```
~/.ohmo/
├── soul.md            # 人格定义（你是谁）
├── identity.md        # 身份信息
├── user.md            # 用户信息
├── memory/            # 个人记忆
├── skills/            # 个人技能
├── plugins/           # 个人插件
├── groups/            # 群组配置
├── sessions/          # 会话存储
├── logs/              # 日志
└── attachments/       # 附件
```

### 18.3 Gateway 架构 (`gateway/`)

```
IM 平台 (Telegram/Feishu/Slack/Discord)
    │
    ▼
GatewayRuntime (gateway/runtime.py)
    │ 管理渠道适配器、消息循环
    ▼
GatewayRouter (gateway/router.py)
    │ 消息路由、渠道分发
    ▼
Bridge (gateway/bridge.py)
    │ 连接渠道和 OpenHarness 引擎
    ▼
OpenHarness QueryEngine (多轮会话)
```

### 18.4 桥接模式 (`gateway/bridge.py`)

Bridge 是 ohmo 的核心组件，它：
1. 将 IM 消息转换为 OpenHarness 查询
2. 管理多轮会话状态（按用户/群组隔离）
3. 将流式响应转发回对应渠道
4. 支持附件和多模态消息

### 18.5 会话存储 (`session_storage.py`)

```python
class OhmoSessionBackend:
    def save_session(self, key: str, messages: list)
    def load_session(self, key: str) -> list
    def list_sessions(self) -> list[str]
    def delete_session(self, key: str)
```

- 基于 `~/.ohmo/sessions/` 的文件存储
- 按 `user_id:channel` 隔离不同对话
- 支持会话恢复

---

## 19. 桥接系统 (Bridge)

**文件**: `src/openharness/bridge/`

### 19.1 架构

| 文件 | 功能 |
|---|---|
| `manager.py` | `BridgeManager` — 会话桥接管理器 |
| `session_runner.py` | 在独立会话中运行 Agent 循环 |
| `types.py` | 桥接类型定义 |
| `work_secret.py` | Work-in-progress 秘密管理 |

Bridge 层允许 OpenHarness 在**非交互式环境**中运行，例如：
- 后台服务
- CI/CD 管道
- IM 渠道集成
- 定时任务

### 19.2 SessionRunner (`session_runner.py`)

```python
class SessionRunner:
    async def run_session(self, input_text, session_id) -> AsyncIterator[StreamEvent]
    async def resume_session(self, session_id) -> AsyncIterator[StreamEvent]
```

- 管理独立的 Agent 会话（消息历史隔离）
- 支持会话持久化和恢复
- 流式输出事件

---

## 20. 配置与状态管理

**文件**: `src/openharness/config/`, `src/openharness/state/`

### 20.1 配置系统 (`config/`)

| 文件 | 功能 |
|---|---|
| `settings.py` | `Settings` 数据类 — 所有配置字段 (~80 个) |
| `paths.py` | 路径管理 (XDG 兼容) |
| `schema.py` | 配置 schema 验证 |

**设置优先级**:
1. CLI 参数
2. 环境变量
3. 配置文件 (`~/.openharness/settings.json`)
4. 项目配置 (`.openharness/settings.json`)
5. 默认值

### 20.2 状态管理 (`state/`)

| 文件 | 功能 |
|---|---|
| `app_state.py` | `AppState` — 全局应用状态单例 |
| `store.py` | `Store` — 键值存储（支持观察者模式） |

AppState 管理运行时状态：当前工作目录、会话 ID、模型、权限模式、成本跟踪等。

---

## 21. 设计思想总结

### 21.1 核心设计原则

1. **框架 vs 应用分离**: OpenHarness 是框架库，ohmo 是应用。框架保持通用，应用承载用户场景。

2. **管道化引擎**: Agent Loop 使用 Stage Pipeline 模式，8 个阶段各自独立可测试、可替换。外层 Guard Loop 关注生命周期，内层 Pipeline 关注单轮执行。

3. **策略模式权限**: PermissionChecker + PermissionMode 实现可扩展的权限策略，从完全自动到逐次审批。

4. **Adapter 模式渠道**: 10 个 IM 渠道通过统一 ChannelAdapter 接口集成，事件总线解耦消息生产与消费。

5. **插件化扩展**: 四层扩展系统——工具、技能、插件、钩子——覆盖从提示注入到 Python 代码的扩展需求。

### 21.2 关键架构决策

| 决策 | 选择 | 理由 |
|---|---|---|
| CLI 框架 | Typer | 类型安全，自动生成帮助，Rich 集成 |
| TUI 引擎 | React Ink | 组件化 UI，流式渲染，声明式更新 |
| API 抽象 | Protocol 类 | 鸭子类型，无需继承，易于测试 |
| 认证 | 多 Provider + 凭证桥接 | 兼容 Claude/Codex CLI 生态 |
| 代理通信 | JSON lines 管道 | 简单可靠，平台无关 |
| 技能格式 | Markdown + YAML | 人类可读，AI 友好，版本可控 |
| 设置存储 | JSON 文件 | 简单可编辑，无数据库依赖 |
| 记忆持久化 | MEMORY.md + 目录 | Git 友好，透明可追踪 |

### 21.3 数据流全景

```
用户输入 → CLI (Typer) → Runtime 调度
  → UI (React TUI / Print / Headless)
    → QueryEngine (Guard Loop → Stage Pipeline)
      → API Call (流式 LLM 调用)
        → Response Routing (Text vs Tool)
          → ToolPipeline (Resolve → PermCheck → Execute → Normalize → Repair)
            → Tool 实现 (Bash/File/Search/etc)
              → 结果返回 → PostTool → 循环/结束
```

### 21.4 规模数据

| 度量 | 值 |
|---|---|
| Python 源文件 | ~180 |
| 工具数量 | 40+ |
| 支持 Provider | 11 |
| 支持 IM 渠道 | 10 |
| 测试数量 | 909 |
| E2E 测试套件 | 6 |
| 外部依赖 | ~25 个主要库 |
| 最新版本 | v0.1.7 |
| 许可证 | MIT |

---

*本报告由 6 个子代理并行调研后综合生成。每个子代理分别深入研究了特定子系统，最终汇总为本文档。*
