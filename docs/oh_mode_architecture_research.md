# OpenHarness OH 模式（CLI Agent 模式）核心架构研究报告

> 调研时间：2026-04-27
> 核心目录：`src/openharness/engine/`、`src/openharness/api/`、`src/openharness/bridge/`、`src/openharness/prompts/`、`src/openharness/config/`、`src/openharness/cli.py`

---

## 一、整体架构概览

OpenHarness 的 OH 模式（即 `oh` CLI 命令）是一个 **对话式 AI 编程代理**，架构上分为四层：

```
┌─────────────────────────────────────────────────────────────┐
│  CLI 层  (cli.py, cli_dry_run.py)                           │
│  解析命令行、打印模式、交互入口                                │
├─────────────────────────────────────────────────────────────┤
│  引擎层  (query_engine.py, query.py, messages.py)            │
│  对话引擎 + 工具感知循环 + auto-compact + silent-stop       │
├─────────────────────────────────────────────────────────────┤
│  API 层  (client.py, openai_client.py, copilot_client.py)    │
│  统一协议 + 多 provider 适配 + 重试 + streaming               │
├─────────────────────────────────────────────────────────────┤
│  工具/权限/提示词层  (tools/, permissions/, prompts/)        │
│  工具注册、权限检查、系统提示词组装                            │
└─────────────────────────────────────────────────────────────┘
```

**核心数据流**：用户输入 → `QueryEngine.submit_message()` → `_stream_query_with_guards()` → `run_query()` → API 调用 → 工具执行 → 结果返回 → 循环直到模型停止工具调用

---

## 二、核心模块详细分析

### 2.1 `query_engine.py` — 高层对话引擎

**职责**：维护对话历史、管理 agentic loop 的生命周期守卫（guard loop）、暴露异步事件流接口。

#### 关键组件

```
QueryEngine
├── _api_client: SupportsStreamingMessages   # API 客户端
├── _tool_registry: ToolRegistry             # 工具注册表
├── _permission_checker: PermissionChecker   # 权限检查器
├── _messages: list[ConversationMessage]     # 对话历史
├── _cost_tracker: CostTracker               # 成本追踪
├── _hook_executor: HookExecutor             # 钩子执行器
├── _tool_metadata: dict[str, object]        # 跨 turn 的元数据
└── _max_turns: int | None                   # 最大轮次限制
```

#### 核心方法：`submit_message()`

1. 将用户输入转为 `ConversationMessage` 追加到 `_messages`
2. 如果文本非空，调用 `remember_user_goal()` 记录目标到 `tool_metadata`
3. 触发 `HookEvent.USER_PROMPT_SUBMIT` 钩子
4. 构建 `QueryContext`（携带所有上下文参数）
5. 检查 coordinator 模式，追加协调员上下文消息
6. 调用 `_stream_query_with_guards()` 执行守卫循环
7. 最后触发 `HookEvent.STOP`

```python
# submit_message 流程
async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[StreamEvent]:
    user_message = ...  # 转为 ConversationMessage
    self._messages.append(user_message)          # 1. 追加用户消息
    remember_user_goal(self._tool_metadata, ...) # 2. 记录目标
    await self._hook_executor.execute(HookEvent.USER_PROMPT_SUBMIT, ...)  # 3. 触发钩子
    context = QueryContext(...)                  # 4. 构建上下文
    query_messages = list(self._messages)        # 5. 复制消息列表
    async for event in self._stream_query_with_guards(context, query_messages):
        yield event                               # 6. 转发事件
```

#### 核心方法：`_stream_query_with_guards()` — 守卫循环

这是 **agentic loop 的顶层控制结构**，包含两个关键守卫机制：

**Guard 1：`max_turns` 拦截**
```python
except MaxTurnsExceeded as exc:
    yield StatusEvent(message=f"Stopped after {exc.max_turns} turns")
    yield StreamFinished(reason="max_turns_exceeded", ...)
    return
```

**Guard 2：silent-stop detection（静默停止自动继续）**

检测条件（`_should_auto_continue_after_silent_stop`）：
1. 最后一条消息是 assistant 角色
2. assistant **没有** tool_calls（`last.tool_uses` 为空）
3. assistant **没有** 可见文本（`last.text.strip()` 为空）→ 完全静默
4. 上一条消息是 user 角色且包含 tool_result
5. 之前存在 assistant 的 tool_use 记录（说明模型在积极工作）

如果满足条件，自动追加内部提示 `_INTERNAL_AUTO_CONTINUE_PROMPT`，让模型继续工作：
```
"The previous assistant turn ended without a user-visible result after tool work. 
Continue working on the current request. Do not stop silently..."
```

**计数机制**：
- `_MAX_CONSECUTIVE_SILENT_STOPS = 1` — 连续静默停止的容忍次数
- `_MAX_AUTO_CONTINUE_ABSOLUTE = 5` — 整个会话的绝对上限
- 如果两轮之间模型有工具调用或可见文本输出，连续计数器重置为 0

**事件聚合逻辑**：
- `pending_turn_complete` 暂存最新的 `AssistantTurnComplete`
- 在遇到非 `AssistantTurnComplete` 事件时（如 tool execution 事件、compact event），先 yield 挂起的 turn complete
- 通过 `_is_meaningful()` 判断 turn 是否有意义（有 tool_use 或有文本）

```
_stream_query_with_guards 数据流:

while True:
    pending_turn_complete = None
    progress_in_this_run = False

    async for event, usage in run_query(context, query_messages):
        # 处理 cost
        # 事件排序：pending turn complete → 新 event
        # 累积 AssistantTurnComplete，转发其他事件

    # 检查 silent-stop
    if matched_silent_stop and can_continue:
        query_messages.pop()  # 移除静默的 assistant 消息
        query_messages.append(auto_continue_prompt)  # 追加继续提示
        continue  # 再次进入 run_query

    yield final pending_turn_complete
    return  # 所有守卫通过，结束
```

#### `continue_pending()` — 恢复中断的工具循环

当对话以 tool result 结尾（没有对应 assistant 回复）时，`has_pending_continuation()` 返回 true，调用 `continue_pending()` 重新进入引擎而不追加新用户消息。

---

### 2.2 `query.py` — 核心查询循环

**职责**：在单个 turn 内完成 API 调用 → 工具执行 → 结果返回的完整循环。

#### `run_query()` 主循环

```python
async def run_query(context: QueryContext, messages) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    compact_state = AutoCompactState()
    turn_count = 0
    
    while context.max_turns is None or turn_count < context.max_turns:
        turn_count += 1
        
        # 1. Auto-compact 检查（每轮开始前）
        async for event, usage in _stream_compaction(trigger="auto"):
            yield event, usage
        messages, was_compacted = last_compaction_result
        
        # 2. 消息归一化
        messages = normalize_messages_for_api(messages)
        
        # 3. API 调用（streaming）
        async for event in context.api_client.stream_message(ApiMessageRequest(...)):
            # ApiTextDeltaEvent → AssistantTextDelta
            # ApiRetryEvent → StatusEvent  
            # ApiMessageCompleteEvent → AssistantTurnComplete
        
        # 4. 检查模型是否请求工具
        if not final_message.tool_uses:
            # 无工具调用 → turn 结束，触发 STOP 钩子，返回
            await context.hook_executor.execute(HookEvent.STOP, ...)
            return
        
        # 5. 工具执行
        if len(tool_calls) == 1:
            # 单工具：顺序执行，立即 yield 事件
            result = await _execute_tool_call(context, ...)
        else:
            # 多工具：并发执行，使用 asyncio.gather(return_exceptions=True)
            raw_results = await asyncio.gather(*[_run(tc) for tc in tool_calls], return_exceptions=True)
        
        # 6. 追加 tool results 到消息列表，继续下一轮
        messages.append(ConversationMessage(role="user", content=tool_results))
```

#### 关键设计决策

**Auto-Compaction（自动压缩）**

每轮开始前检查 token 是否超过阈值：

```python
auto_compact_if_needed(
    messages,
    state=compact_state,
    trigger="auto",  # "auto", "manual", "reactive"
    force=False,
    ...
)
```

触发条件：`estimated_tokens > auto_compact_threshold + buffer_tokens(13000)`

压缩阶段：
1. **Microcompact**：清除旧 tool result 内容（最便宜）
2. **Full LLM-based summarization**：调用 LLM 生成摘要
3. **Session memory**：维护最近 12 条、最多 48 行的 session memory
4. **Context collapse**：截断超长文本

如果 API 返回 "prompt too long" 错误，触发 **reactive compact**（强制压缩）：
```python
if _is_prompt_too_long_error(exc):
    async for event, usage in _stream_compaction(trigger="reactive", force=True):
        yield event, usage
    messages, was_compacted = last_compaction_result
    if was_compacted:
        continue  # 重试 API 调用
```

**Permission 权限检查**

在 `_execute_tool_call()` 中执行：
```python
decision = context.permission_checker.evaluate(
    tool_name,
    is_read_only=tool.is_read_only(parsed_input),
    file_path=_file_path,
    command=_command,
)

if not decision.allowed:
    if decision.requires_confirmation and context.permission_prompt:
        confirmed = await context.permission_prompt(tool_name, decision.reason)
        # "once" / "always" / "reject" 三种回复
        if reply == "always":
            context.permission_checker.remember_allow(decision)
```

**Permission Mode（权限模式）**

三种模式定义在 `permissions/modes.py`：
- `DEFAULT`：只读工具自动允许，写操作需要用户确认
- `PLAN`：进入 planning 模式，修改 `tool_metadata[permission_mode]`
- `FULL_AUTO`：允许所有工具，无需确认

**Tool Carryover（工具状态传承）**

工具执行后，`_record_tool_carryover()` 维护上下文记忆：
- `read_file` → 记录文件读取状态、已验证的工作
- `skill_manager` → 记录技能调用历史
- `agent/send_message` → 记录异步代理活动
- `bash` → 记录工作日志
- `plan_mode` → 更新权限模式状态
- 所有文件操作 → 记录活跃 artifact

---

### 2.3 `messages.py` — 消息模型体系

**职责**：定义内部消息表示、内容块、API 序列化。

#### 内容块体系

```
ContentBlock = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock
```

| 类型 | 用途 | 关键字段 |
|------|------|----------|
| `TextBlock` | 文本内容 | `text: str` |
| `ImageBlock` | 多模态图片 | `media_type`, `data` (base64) |
| `ToolUseBlock` | 模型工具调用请求 | `id`, `name`, `input` |
| `ToolResultBlock` | 工具执行结果 | `tool_use_id`, `content`, `is_error` |

#### `ConversationMessage`

```python
class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: list[ContentBlock]
    
    @property
    def text(self) -> str:       # 拼接所有 TextBlock
    @property
    def tool_uses(self) -> list[ToolUseBlock]:  # 提取工具调用
    def to_api_param(self) -> dict:  # 转为 Anthropic SDK 格式
```

#### 归一化函数

`normalize_messages_for_api()`：
- 合并连续的用户纯文本消息（用 `\n\n` 连接）
- 其他组合（tool_result、assistant、混合内容）保持不变
- **不修改原始列表**，始终返回新列表

`assistant_message_from_api()`：
- 将 Anthropic SDK 响应转为 `ConversationMessage`
- 解析 `content_block` 中的 `text` 和 `tool_use`

---

### 2.4 `cost_tracker.py` — 成本追踪

极轻量实现：

```python
class CostTracker:
    def add(self, usage: UsageSnapshot):
        self._usage.input_tokens += usage.input_tokens
        self._usage.output_tokens += usage.output_tokens
    
    @property
    def total(self) -> UsageSnapshot:
        return self._usage  # 返回累积总量
```

`UsageSnapshot` 仅包含 `input_tokens` 和 `output_tokens` 两个整数。

---

### 2.5 `api/client.py` — API 客户端协议

#### `SupportsStreamingMessages` 协议

```python
class SupportsStreamingMessages(Protocol):
    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Yield streamed events for the request."""
```

这是引擎层与 API 层的 **唯一契约**，任何 provider 客户端都必须实现此接口。

#### 事件类型

| 类型 | 含义 |
|------|------|
| `ApiTextDeltaEvent` | 增量文本（流式） |
| `ApiMessageCompleteEvent` | 完整消息（终态） |
| `ApiRetryEvent` | 可恢复错误，附带重试信息 |

#### `ApiMessageRequest`

```python
class ApiMessageRequest:
    model: str
    messages: list[ConversationMessage]
    system_prompt: str | None
    max_tokens: int = 4096
    tools: list[dict[str, Any]] = []
```

#### `AnthropicApiClient` — Anthropic 原生实现

```python
class AnthropicApiClient:
    def __init__(self, api_key, base_url, claude_oauth=False, ...):
        # 支持三种认证：
        # 1. 标准 API Key
        # 2. OAuth Token（auth_token）
        # 3. Claude OAuth（claude_oauth=True，设备 OAuth 流程）
    
    async def stream_message(self, request):
        for attempt in range(MAX_RETRIES + 1):  # 最多 4 次重试
            try:
                self._refresh_client_auth()  # OAuth token 刷新
                async for event in self._stream_once(request):
                    yield event
                return  # 成功
            except OpenHarnessApiError:
                raise  # 认证错误不重试
            except Exception as exc:
                if _is_retryable(exc):
                    yield ApiRetryEvent(...)
                    await asyncio.sleep(delay)
                else:
                    raise
```

**重试策略**：
- 最大重试次数：3 次（共 4 次尝试）
- 延迟：指数退避 `min(1.0 * 2^attempt, 30.0) + jitter`
- 可重试状态码：429, 500, 502, 503, 529
- 网络错误/超时均触发重试

**Claude OAuth 支持**：
- 注入 `claude-attribution` header
- 注入 `oauth-2025-04-20` beta 头
- 生成 `user_id` JSON metadata（device_id + session_id）
- token 自动刷新机制

#### `OpenAICompatibleClient` — OpenAI 兼容实现

负责 **Anthropic → OpenAI 格式转换**：

```
Anthropic 格式                           OpenAI 格式
─────────────────────────────────     ─────────────────────────────────
system: separate param               →  role="system" message
user: [TextBlock, ToolResultBlock]   →  role="user" text
                                       role="tool" message (每个 tool_result)
assistant: [TextBlock, ToolUseBlock] →  role="assistant" content + tool_calls
```

关键转换函数：
- `_convert_messages_to_openai()`：消息格式转换
- `_convert_assistant_message()`：工具调用转换
- `_convert_tools_to_openai()`：工具 schema 转换（`parameters` / `input_schema` → `function`）
- `_parse_assistant_response()`：响应解析，处理 `tool_calls` 和 `reasoning_content`（thinking 模型）

特殊处理：
- GPT-5 / o1/o3/o4 系列使用 `max_completion_tokens` 而非 `max_tokens`
- 对 thinking 模型（如 Kimi k2.5）自动注入空 `reasoning_content` 字段

#### `CopilotClient` — GitHub Copilot 实现

```python
class CopilotClient:
    # 底层委托给 OpenAICompatibleClient
    # 额外注入 Copilot 特有 header:
    #   User-Agent: openharness/0.1.0
    #   Openai-Intent: conversation-edits
    # 使用 GitHub OAuth token 作为 Bearer token
```

---

### 2.6 `api/provider.py` — Provider 检测

通过模型名称关键字自动检测 provider：

```python
PROVIDERS = (
    # Gateways（按 key prefix / base_url 检测）
    ProviderSpec(name="openrouter", keywords=("openrouter",), detect_by_key_prefix="sk-or-"),
    ProviderSpec(name="siliconflow", keywords=("siliconflow",), ...),
    ProviderSpec(name="volcengine", keywords=("volces", "ark"), detect_by_base_keyword="volces"),
    
    # Standard providers（按 model name 检测）
    ProviderSpec(name="anthropic", keywords=("anthropic", "claude"), backend_type="anthropic"),
    ProviderSpec(name="openai", keywords=("gpt", "o1", "o3", "o4"), backend_type="openai_compat"),
    ProviderSpec(name="deepseek", keywords=("deepseek",), backend_type="openai_compat"),
    ProviderSpec(name="gemini", keywords=("gemini",), backend_type="openai_compat"),
    ProviderSpec(name="dashscope", keywords=("qwen", "dashscope"), backend_type="openai_compat"),
    ProviderSpec(name="moonshot", keywords=("moonshot", "kimi", "k2"), backend_type="openai_compat"),
    
    # Local / special
    ProviderSpec(name="openai_compatible", keywords=("openai",), is_gateway=True),
    ProviderSpec(name="groq", ...),
    ProviderSpec(name="vertex", ...),
    ProviderSpec(name="local", keywords=("local", "ollama", "lmstudio", "vllm"), is_local=True),
)
```

检测优先级由列表顺序控制。每种 provider 指定：
- `backend_type`：`"anthropic"` | `"openai_compat"` | `"copilot"`
- `env_key`：API key 环境变量
- `detect_by_key_prefix` / `detect_by_base_keyword`：自动检测信号
- `is_gateway` / `is_local` / `is_oauth`：分类标签

---

### 2.7 `prompts/` — 提示词系统

四层组装架构：

```
build_runtime_system_prompt()
├── 1. Base system prompt (system_prompt.py)
│     "You are OpenHarness, an open-source AI coding assistant CLI..."
│     包含通用指令：工具使用规则、安全原则、编码风格
│
├── 2. Environment section (environment.py)
│     OS, architecture, shell, cwd, date, Python version, git branch
│
├── 3. Custom settings (settings.system_prompt)
│     用户自定义的 system prompt（可选，覆盖默认）
│
├── 4. Fast mode / Reasoning settings
│     效率等级、Pass 数等运行时配置
│
├── 5. Skills guidance (context.py)
│     可用技能列表 + 技能使用指导
│
├── 6. Delegation guidance
│     Agent 工具使用说明
│
├── 7. Tool-use enforcement
│     "你必须立即调用工具，不能只是描述计划"
│
├── 8. CLAUDE.md (claudemd.py)
│     递归查找项目根目录的 CLAUDE.md / .claude/CLAUDE.md / .claude/rules/*.md
│
├── 9. Local rules (personalization)
│     用户本地规则文件
│
├── 10. Issue / PR / Repo Context
│     项目级 issue.md, pr_comments.md, active_repo_context.md
│
└── 11. Memory (memory system)
      MEMORY.md + 相关记忆文件（如果启用）
```

**System Prompt 设计要点**：
- 明确禁止模型生成/猜测 URL（除非用于编程）
- 要求在使用专用工具时不要使用 bash（read_file vs cat, edit_file vs sed 等）
- 单次工具调用可并行执行
- 简洁风格：结论先行、代码引用带行号

---

### 2.8 `bridge/` — 桥接层

**职责**：管理外部子进程会话（bridge session），用于隔离执行或远端代理。

```
bridge/
├── session_runner.py   # SessionHandle + spawn_session()
├── manager.py          # BridgeSessionManager（进程生命周期 + 输出捕获）
├── types.py            # WorkData, WorkSecret, BridgeConfig
└── work_secret.py      # 工作密钥编码
```

`BridgeSessionManager`：
- 管理子进程的 spawn / kill / 输出捕获
- 输出实时写入 `~/.openharness/data/bridge/{session_id}.log`
- `list_sessions()` 返回 UI 安全的快照

---

### 2.9 `config/` — 配置系统

**加载优先级**（最高→最低）：
1. CLI 参数
2. 环境变量（`ANTHROPIC_API_KEY`, `OPENHARNESS_MODEL` 等）
3. 配置文件（`~/.openharness/settings.json`）
4. 默认值

**路径解析**（`paths.py`）：
- 配置目录：`OPENHARNESS_CONFIG_DIR` 或 `~/.openharness/`
- 数据目录：`OPENHARNESS_DATA_DIR` 或 `~/.openharness/data/`
- 日志目录：`OPENHARNESS_LOGS_DIR` 或 `~/.openharness/logs/`

**Settings 模型**（`settings.py`）：
```python
Settings
├── provider: str                          # 默认 provider
├── api_format: str                        # "anthropic" | "openai" | "copilot"
├── model: str                             # 当前模型
├── base_url: str | None                   # 可选 base URL
├── api_key: str | None                    # 可选 API key
├── system_prompt: str | None              # 自定义 system prompt
├── permission: PermissionSettings         # 权限配置
├── memory: MemorySettings                 # 记忆系统配置
├── sandbox: SandboxSettings               # 沙箱配置
├── provider_profiles: dict[str, ProviderProfile]  # 命名 provider 配置
├── hooks: list[HookDefinition]            # 钩子定义
├── mcp_servers: dict[str, dict]           # MCP 服务器配置
└── ...
```

**ProviderProfile**：支持命名配置集，包含 `default_model`、`last_model`、`context_window_tokens`、`auto_compact_threshold_tokens`、`thinking_extra_body` 等。

---

## 三、数据流全景

### 3.1 完整请求链路

```
用户输入 "Fix the bug in x.py"
    │
    ▼
QueryEngine.submit_message()
    ├── 1. 转为 ConversationMessage，追加到 _messages
    ├── 2. remember_user_goal(tool_metadata, "Fix the bug in x.py")
    ├── 3. HookEvent.USER_PROMPT_SUBMIT
    ├── 4. 构建 QueryContext
    ├── 5. 检查 coordinator 上下文
    └── 6. _stream_query_with_guards(context, query_messages)
            │
            ▼
        while True (guard loop):
            │
            ├── turn_count++ (≤ max_turns)
            │
            ├── _stream_compaction("auto")  ← Auto-compact 检查
            │   ├── 估算 token 数
            │   ├── 超过阈值? → microcompact → full compact → session_memory
            │   └── yield CompactProgressEvent 进度事件
            │
            ├── normalize_messages_for_api()  ← 合并连续 user 文本消息
            │
            ├── api_client.stream_message(ApiMessageRequest)
            │   ├── 转换消息为 provider 格式
            │   ├── streaming API 调用
            │   │   ├── ApiTextDeltaEvent → yield AssistantTextDelta
            │   │   ├── ApiRetryEvent → yield StatusEvent (带退避延迟)
            │   │   └── ApiMessageCompleteEvent
            │   │       └── yield AssistantTurnComplete(message, usage)
            │   └── 重试逻辑 (MAX_RETRIES=3, 指数退避)
            │
            ├── if not final_message.tool_uses:
            │   └── HookEvent.STOP → return (turn 结束)
            │
            └── tool_calls = final_message.tool_uses
                │
                ├── if len(tool_calls) == 1:
                │   └── 顺序执行
                │       ├── ToolExecutionStarted → yield
                │       ├── _execute_tool_call()
                │       │   ├── HookEvent.PRE_TOOL_USE
                │       │   ├── 工具输入校验 (Pydantic)
                │       │   ├── PermissionChecker.evaluate()
                │       │   │   ├── 敏感路径检查 (SSH keys, AWS creds...)
                │       │   │   ├── 工具 deny/allow 列表
                │       │   │   ├── 路径规则匹配
                │       │   │   ├── 命令 deny 匹配
                │       │   │   ├── 已记住的 allow 检查
                │       │   │   ├── FULL_AUTO → 全部允许
                │       │   │   ├── read_only → 允许
                │       │   │   └── 其他 → requires_confirmation
                │       │   ├── PermissionPrompt (如果需确认)
                │       │   │   ├── "once" → 允许本次
                │       │   │   ├── "always" → 记住此规则
                │       │   │   └── "reject" → 返回错误
                │       │   ├── tool.execute()
                │       │   ├── HookEvent.POST_TOOL_USE
                │       │   └── _record_tool_carryover()
                │       │       ├── _carryover_state()  ← 活跃 artifact, 已验证工作
                │       │       └── _carryover_log()    ← 工作日志
                │       └── ToolExecutionCompleted → yield
                │
                └── else (多工具):
                    └── asyncio.gather(return_exceptions=True)
                        └── 并发执行，单工具失败不取消其他
                │
                ├── messages.append(ConversationMessage(role="user", content=tool_results))
                └── 循环继续下一轮
            │
            ▼
        检查 silent-stop:
            ├── assistant 无工具调用 + 无文本?
            ├── 上一条是 tool_result?
            ├── 之前有工具使用记录?
            └── 如果全部满足:
                ├── consecutive_silent_stops++
                ├── total_auto_continues++
                ├── 检查上限: ≤1 consecutive, <5 total
                ├── query_messages.pop()  // 移除静默 assistant
                ├── query_messages.append(auto_continue_prompt)
                └── continue  // 重新进入 run_query
            │
            ▼
        yield 最终 AssistantTurnComplete
        return (guard loop 结束)
            │
            ▼
        HookEvent.STOP 触发
```

### 3.2 事件流

`QueryEngine` 向外暴露的 `StreamEvent` 联合类型：

```python
StreamEvent = (
    AssistantTextDelta       # 模型文本增量
    | AssistantTurnComplete  # 完整 turn 结束
    | ToolExecutionStarted   # 工具执行开始
    | ToolExecutionCompleted # 工具执行完成
    | ErrorEvent             # 错误
    | StatusEvent            # 系统状态消息
    | CompactProgressEvent   # 压缩进度
    | StreamFinished         # 最终结束信号 (auto_continue_exhausted / max_turns_exceeded)
)
```

### 3.3 `tool_metadata` 跨 Turn 状态

`tool_metadata` 字典在引擎生命周期内共享，承载以下状态：

| 键 (ToolMetadataKey) | 用途 |
|----------------------|------|
| `permission_mode` | 当前权限模式（default/plan） |
| `read_file_state` | 文件读取历史（最多 6 个） |
| `invoked_skills` | 技能调用历史（最多 8 个） |
| `async_agent_state` | 异步代理活动（最多 8 个） |
| `async_agent_tasks` | 代理任务详情（最多 12 个） |
| `recent_work_log` | 工作日志（最多 10 个） |
| `recent_verified_work` | 已验证工作（最多 10 个） |
| `task_focus_state` | 任务焦点：goal, recent_goals, active_artifacts, next_step |
| `compact_checkpoints` | 压缩检查点 |
| `compact_last` | 上次压缩状态 |

---

## 四、关键设计决策分析

### 4.1 Auto-Compact

**为什么需要**：LLM 上下文窗口有限，对话会持续增长。

**触发条件**：`estimated_tokens > (threshold + 13000 buffer)`

**三层压缩策略**：
1. **Microcompact**：清除旧 tool result 文本（低成本，保留工具调用 ID 和结构）
2. **Full Compaction**：调用 LLM 生成结构化摘要
3. **Session Memory**：保留最近 N 条关键信息

**补偿检查点机制**：`_record_compact_checkpoint()` 记录每次压缩的 checkpoint 信息到 `tool_metadata`，便于诊断和问题追踪。

### 4.2 Silent-Stop Detection

**问题场景**：模型执行完工具后返回空消息（无文本、无工具调用），可能导致对话死锁。

**解决方案**：自动追加 `_INTERNAL_AUTO_CONTINUE_PROMPT`，提示模型继续工作。

**设计精巧之处**：
- 仅在模型"活跃工作后"才触发（之前必须有 tool_use 记录）
- 双重上限控制（1 连续 + 5 绝对），防止无限循环
- 中间有进展时重置计数器

### 4.3 Max Turns

**默认值**：`QueryEngine.__init__` 默认 8，`QueryContext` 默认 200。

**作用**：防止代理在单用户输入上无限循环。

**拦截方式**：`run_query` 循环退出时抛出 `MaxTurnsExceeded`，被 `_stream_query_with_guards` 捕获后转为 `StatusEvent` + `StreamFinished`。

### 4.4 Permission Mode

三种模式在 `query.py` 的 `_execute_tool_call` 中生效：

```
PermissionChecker.evaluate()
    ├── 敏感路径硬拦截（SSH/AWS/GCP/Azure/Docker/K8s 凭据）→ 不可覆盖
    ├── 工具 deny 列表 → 不可覆盖
    ├── 工具 allow 列表 → 立即允许
    ├── 路径规则匹配 → 按规则允许/拒绝
    ├── 命令 deny 匹配 → 拒绝
    ├── 已记住的 allow → 允许
    ├── FULL_AUTO → 全部允许
    ├── read_only → 允许
    ├── 其他 → requires_confirmation
    └── 触发 permission_prompt → 用户确认
```

**Remember Allow**：用户选择 "always" 后，规则被缓存到 `_remembered_allow_rules` 列表中，当前进程内有效。

---

## 五、模块依赖关系

```
cli.py
├── config/settings.py  (加载 Settings)
├── config/paths.py     (路径解析)
├── prompts/context.py  (构建系统提示词)
├── engine/query_engine.py  (核心引擎)
│   ├── engine/query.py         (查询循环)
│   │   ├── engine/messages.py  (消息模型)
│   │   ├── engine/types.py     (枚举/状态)
│   │   ├── engine/cost_tracker.py (成本)
│   │   ├── engine/stream_events.py (事件)
│   │   ├── api/client.py       (API 协议)
│   │   ├── api/usage.py        (UsageSnapshot)
│   │   ├── permissions/checker.py (权限)
│   │   ├── tools/base.py       (工具)
│   │   ├── hooks/executor.py   (钩子)
│   │   └── services/compact/   (压缩)
│   ├── api/client.py (SupportsStreamingMessages)
│   │   ├── api/openai_client.py  (OpenAI 兼容)
│   │   ├── api/copilot_client.py (Copilot)
│   │   └── api/errors.py
│   ├── permissions/checker.py
│   ├── tools/ (各种工具实现)
│   ├── hooks/ (钩子系统)
│   └── services/compact/__init__.py
├── api/provider.py     (Provider 检测)
│   └── api/registry.py (ProviderSpec 注册表)
└── bridge/ (桥接层)
    ├── bridge/manager.py
    └── bridge/session_runner.py
```

**依赖方向**：自下而上，每一层依赖其下一层的抽象。

- `api/client.py` 的 `SupportsStreamingMessages` 协议是引擎层与 API 层的**解耦点**
- `engine/types.py` 的 `ToolMetadataKey` 是跨 turn 状态的**唯一来源**
- `hooks/` 是横向切面，可在事件流的任意节点注入自定义逻辑

---

## 六、与 Claude Code 的对应关系

OpenHarness 的架构设计**明确参考了 Claude Code（Hermes）**的实现：

| Claude Code 概念 | OpenHarness 对应 | 文件位置 |
|-----------------|-----------------|----------|
| Conversation compaction | `services/compact/__init__.py` | `auto_compact_if_needed()` |
| Microcompact | `COMPACTABLE_TOOLS` + `_microcompact_messages` | 清除旧 tool result |
| Full LLM summarization | `compact_messages()` | 调用 LLM 生成摘要 |
| Session memory | `SESSION_MEMORY_KEEP_RECENT = 12` | 保留最近 N 条 memory |
| Silent-stop auto-continue | `_INTERNAL_AUTO_CONTINUE_PROMPT` | `_should_auto_continue_after_silent_stop()` |
| Permission modes | `permissions/modes.py` | `DEFAULT`, `PLAN`, `FULL_AUTO` |
| Permission checker | `permissions/checker.py` | `PermissionChecker.evaluate()` |
| Tool registry | `tools/base.py` | `ToolRegistry` |
| Hook system | `hooks/` | `pre_tool_use`, `post_tool_use`, `stop` |
| Tool metadata carryover | `tool_metadata` dict | `_record_tool_carryover()`, `_carryover_state()` |
| Task focus state | `engine/types.py` | `TaskFocusStateKey` |
| Context window management | `context_window_tokens` | `MemorySettings.context_window_tokens` |
| Auto-compact threshold | `auto_compact_threshold_tokens` | `MemorySettings.auto_compact_threshold_tokens` |
| CLAUDE.md loading | `prompts/claudemd.py` | `discover_claude_md_files()` |
| OpenAI-compatible provider | `api/openai_client.py` | `_convert_messages_to_openai()` |
| Provider registry | `api/registry.py` | `PROVIDERS` 元组 |
| Max turns limit | `max_turns` | `_stream_query_with_guards()` |

文档中明确写道（`services/compact/__init__.py` 头部注释）：
> "Faithfully translated from Claude Code's compaction system"

---

## 七、总结

OpenHarness OH 模式的核心是一个 **工具感知型对话代理引擎**，其架构特点：

1. **清晰的层次解耦**：`SupportsStreamingMessages` 协议隔离了引擎与 API 实现，使多 provider 支持成为可能
2. **完整的 agentic loop**：API 调用 → 工具执行（支持并行） → 结果返回 → 循环直到模型停止
3. **成熟的上下文管理**：auto-compact 三层压缩 + silent-stop 自动继续 + max_turns 安全上限
4. **细粒度权限控制**：三层防护（敏感路径硬拦截 + 用户配置 + 交互式确认）
5. **可观测性**：结构化事件流（`StreamEvent` 联合类型）+ 钩子系统 + 成本追踪
6. **跨 turn 状态传承**：`tool_metadata` 字典承载任务焦点、文件读取历史、技能调用等上下文
7. **高度模块化**：provider 注册表、工具注册表、钩子注册表均支持扩展
