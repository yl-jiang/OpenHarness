# /goal 实施计划

> 基于 `docs/goal-design.md`，分阶段交付 goal 驱动执行框架。

---

## 总体策略

采用 **由内到外** 的交付顺序：先实现核心状态机和工具（可独立测试），再集成到 QueryEngine 驱动循环，最后接入 TUI。每个阶段都有明确的验证标准。

---

## Phase 1：核心状态机与工具（无 UI 依赖）

**目标**：GoalMode 状态机 + 4 个模型工具，可通过 Python API 独立使用。

### 1.1 新增文件

```
src/openharness/goal/
├── __init__.py
├── state.py          # GoalMode 状态机 + GoalState/GoalSnapshot/GoalBudgetReport
├── injection.py      # build_goal_reminder() 生成注入文本 + escape_untrusted_text()
└── budget.py         # normalize_budget_input() + budget_limits_from_input() 辅助函数
```

### 1.2 实现内容

#### `goal/state.py`

```python
@dataclass
class GoalState:
    objective: str
    completion_criterion: str | None = None
    status: str = "active"          # "active" | "paused" | "blocked" | "completed" | "cancelled"
    reason: str | None = None
    last_actor: str | None = None   # 新增：记录最近一次状态变更的发起方
    created_at: float = 0.0         # time.monotonic()
    turns_used: int = 0
    tokens_used: int = 0
    wall_clock_ms: int = 0
    budget_limits: GoalBudgetLimits | None = None

class GoalMode:
    """单目标生命周期管理器。"""

    def __init__(self, tool_metadata: dict[str, object]) -> None:
        self._metadata = tool_metadata
        self._state: GoalState | None = self._restore_from_metadata()

    # --- Reads ---
    def get_goal(self) -> GoalSnapshot | None: ...
    def get_active_goal(self) -> GoalSnapshot | None: ...

    # --- Creation ---
    def create_goal(self, objective: str, *, completion_criterion: str | None = None,
                    replace: bool = False, actor: str = "model") -> GoalSnapshot: ...

    # --- Lifecycle ---
    def pause_goal(self, *, reason: str | None = None, actor: str = "user") -> GoalSnapshot: ...
    def resume_goal(self, *, reason: str | None = None, actor: str = "user") -> GoalSnapshot: ...
    def cancel_goal(self, *, actor: str = "user") -> GoalSnapshot: ...

    # --- Terminal outcomes ---
    def mark_complete(self, *, reason: str | None = None, actor: str = "model") -> GoalSnapshot | None: ...
    def mark_blocked(self, *, reason: str | None = None, actor: str = "runtime") -> GoalSnapshot | None: ...

    # --- Accounting ---
    def record_token_usage(self, token_delta: int) -> GoalSnapshot | None: ...
    def increment_turn(self) -> GoalSnapshot | None: ...

    # --- Budget ---
    def set_budget_limits(self, limits: GoalBudgetLimits) -> GoalSnapshot: ...
```

**持久化方式**：每次状态变更后调用 `_persist()`，将 `GoalState` 序列化到 `tool_metadata["_goal_state"]`。

> **同步 vs 异步方法的设计权衡**：kimi-code 使用 async 方法是因为需要做 append-only record logging 和 event emitting（可能涉及异步 I/O）。OpenHarness 使用 `tool_metadata` 内存字典存储，所以同步方法可行。代价是放弃了 kimi-code 的 record replay 能力——状态只能从最后一次 `tool_metadata` 快照恢复，而非从完整操作日志重建。

#### `goal/injection.py`

```python
def escape_untrusted_text(text: str) -> str:
    """XML-escape objective/completion_criterion to prevent breaking out of <untrusted_objective> tags."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def build_goal_reminder(snapshot: GoalSnapshot) -> str | None:
    """根据 goal 状态生成注入文本。active 返回完整提醒，paused/blocked 返回轻量提示。
    对 objective 和 completion_criterion 使用 escape_untrusted_text() 进行转义。"""

def build_completion_summary_prompt(snapshot: GoalSnapshot) -> str:
    """目标完成后，注入让模型生成最终摘要的提示。"""

def build_blocked_reason_prompt(snapshot: GoalSnapshot) -> str:

    """目标受阻后，注入让模型解释阻塞原因的提示。"""

GOAL_CANCELLED_REMINDER = (
    "The user cancelled the current goal. "
    "Ignore earlier active-goal reminders for that goal. "
    "Handle the next user request normally unless the user starts or resumes a goal."
)
```

#### `goal/budget.py`

```python
def normalize_budget_input(value: float, unit: str) -> tuple[int | float, str]:
    """标准化 budget 输入值。
    - turns/tokens: max(1, round(value))
    - 时间单位: 原值保留（后续由 budget_limits_from_input 做范围校验）
    """

def budget_limits_from_input(value: float, unit: str) -> GoalBudgetLimits | None:
    """从工具参数构造 GoalBudgetLimits，包含合理性校验。
    - 时间 budget 必须 >= 1 秒 且 <= 24 小时，否则拒绝并返回 None
    - turns/tokens budget 做 max(1, round(value)) 标准化
    返回 None 时，调用方应报错 "Goal budget not set: {value} {unit} is not a reasonable goal budget."
    """
```

### 1.3 新增工具

```
src/openharness/tools/
├── create_goal_tool.py
├── update_goal_tool.py
├── get_goal_tool.py
└── set_goal_budget_tool.py
```

每个工具继承 `BaseTool`。**GoalMode 作为 QueryEngine 的一等属性**（而非藏在 generic metadata dict 中），工具通过 QueryEngine 引用或 `tool_metadata` 中专用的 `"goal_mode"` key 访问 GoalMode 实例。

> **设计理由**：GoalMode 是一个跨切面关注点，多个组件（QueryEngine、命令处理器、工具）都需要访问它。将它作为 QueryEngine 的一等属性而非埋在 generic `metadata` dict 中，使得依赖关系显式化、访问路径清晰，并避免与其它 metadata key 冲突。

#### `create_goal_tool.py`

```python
class CreateGoalTool(BaseTool):
    name = "create_goal"

    class InputModel(BaseModel):
        objective: str
        completion_criterion: str | None = None
        replace: bool = False

    async def execute(self, arguments: InputModel, context: ToolExecutionContext) -> ToolResult:
        goal_mode: GoalMode = context.engine.goal_mode   # 通过 QueryEngine 一等属性访问
        snapshot = goal_mode.create_goal(
            arguments.objective,
            completion_criterion=arguments.completion_criterion,
            replace=arguments.replace,
        )
        return ToolResult(output=json.dumps({"goal": snapshot.to_dict()}, indent=2))

    def to_api_schema(self) -> dict:
        return {
            "name": "create_goal",
            "description": (
                "Create a durable, structured goal that the runtime will pursue across "
                "multiple turns. Call only when the user explicitly asks to start a goal "
                "or work autonomously toward an outcome. Do NOT create a goal for greetings, "
                "ordinary questions, or vague requests."
            ),
            "parameters": { ... },
        }
```

#### `update_goal_tool.py`

```python
class UpdateGoalTool(BaseTool):
    name = "update_goal"

    class InputModel(BaseModel):
        status: Literal["active", "paused", "completed", "blocked"]
        reason: str | None = None

    async def execute(self, arguments: InputModel, context: ToolExecutionContext) -> ToolResult:
        goal_mode: GoalMode = context.engine.goal_mode

        if arguments.status == "completed":
            snapshot = goal_mode.mark_complete(reason=arguments.reason)
            # 结束当前 turn 和 batch + 注入 completion summary prompt
            reminder = build_completion_summary_prompt(snapshot)
            context.engine.inject_user_message(reminder)
            return ToolResult(
                output=json.dumps({"goal": snapshot.to_dict()}, indent=2),
                stop_turn=True,
                stop_batch=True,
            )

        if arguments.status == "blocked":
            snapshot = goal_mode.mark_blocked(reason=arguments.reason)
            # 结束当前 turn 和 batch + 注入 blocked reason prompt
            reminder = build_blocked_reason_prompt(snapshot)
            context.engine.inject_user_message(reminder)
            return ToolResult(
                output=json.dumps({"goal": snapshot.to_dict()}, indent=2),
                stop_turn=True,
                stop_batch=True,
            )

        if arguments.status == "paused":
            snapshot = goal_mode.pause_goal(reason=arguments.reason)
            return ToolResult(
                output=json.dumps({"goal": snapshot.to_dict()}, indent=2),
                stop_turn=True,
                stop_batch=True,
            )

        # active → 无 stop flag
        snapshot = goal_mode.get_active_goal()
        return ToolResult(output=json.dumps({"goal": snapshot.to_dict()}, indent=2))
```

> **ToolResult 扩展**：为支持 goal 工具控制 tool loop 行为，在 `ToolResult` 中新增 `stop_turn` 和 `stop_batch` 字段。engine 的 tool loop（`run_query` / `query.py`）需要在每次工具执行后检查这些 flag，并在 `stop_turn=True` 或 `stop_batch=True` 时终止当前 tool loop。

#### `ToolResult` 新增字段

```python
@dataclass(frozen=True)
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    stop_turn: bool = False       # 新增：结束当前 turn（不再运行 tool loop 的后续 step）
    stop_batch: bool = False      # 新增：当前 batch 后不再执行更多 tool call
```

#### `set_goal_budget_tool.py`

```python
class SetGoalBudgetTool(BaseTool):
    name = "set_goal_budget"

    class InputModel(BaseModel):
        value: float          # 正数
        unit: Literal["turns", "tokens", "milliseconds", "seconds", "minutes", "hours"]

    async def execute(self, arguments: InputModel, context: ToolExecutionContext) -> ToolResult:
        goal_mode: GoalMode = context.engine.goal_mode

        limits = budget_limits_from_input(arguments.value, arguments.unit)
        if limits is None:
            return ToolResult(
                is_error=True,
                output=f"Goal budget not set: {arguments.value} {arguments.unit} is not a reasonable goal budget.",
            )

        snapshot = goal_mode.set_budget_limits(limits)
        return ToolResult(output=json.dumps({"goal": snapshot.to_dict()}, indent=2))
```

### 1.4 测试

```
tests/
├── test_goal_state.py        # GoalMode 状态机单元测试
│   - test_create_goal
│   - test_create_goal_replaces_existing
│   - test_create_goal_empty_objective_raises
│   - test_pause_resume_cycle
│   - test_cancel_clears_state
│   - test_mark_complete_clears_state
│   - test_mark_blocked_stays_resumable
│   - test_budget_over_budget
│   - test_normalize_after_replay
│   - test_persist_and_restore
│
├── test_goal_injection.py    # Reminder 生成测试
│   - test_active_reminder_format
│   - test_paused_note_format
│   - test_blocked_note_format
│   - test_untrusted_objective_escaping
│
├── test_goal_tools.py        # 工具执行测试
│   - test_create_goal_tool
│   - test_update_goal_tool_complete       # 验证 stop_turn=True, stop_batch=True
│   - test_update_goal_tool_blocked        # 验证 stop_turn=True, stop_batch=True
│   - test_update_goal_tool_paused         # 验证 stop_turn=True, stop_batch=True
│   - test_update_goal_tool_active         # 验证无 stop flag
│   - test_get_goal_tool
│   - test_set_goal_budget_tool_time_reasonable
│   - test_set_goal_budget_tool_time_unreasonable     # e.g. 0.5 seconds or 100000 hours
│   - test_set_goal_budget_tool_turns_normalized      # e.g. 0.3 turns → normalized to 1

├── test_goal_budget.py       # Budget 辅助函数测试
│   - test_normalize_budget_input_turns
│   - test_normalize_budget_input_tokens
│   - test_budget_limits_from_input_time_valid
│   - test_budget_limits_from_input_time_out_of_range
```

### 1.5 验证标准

- [ ] `uv run pytest -q tests/test_goal_state.py` 全部通过
- [ ] `uv run pytest -q tests/test_goal_tools.py` 全部通过
- [ ] `uv run pytest -q tests/test_goal_budget.py` 全部通过
- [ ] `uv run ruff check src/openharness/goal src/openharness/tools/create_goal_tool.py ...` 无报错

---

## Phase 2：QueryEngine 集成 — drive_goal 循环

**目标**：`/goal` 命令创建目标后，QueryEngine 自动驱动多轮执行。

### 2.1 GoalMode 访问模式

GoalMode 作为 QueryEngine 的一等属性：

```python
class QueryEngine:
    def __init__(self, ..., goal_mode: GoalMode | None = None):
        ...
        self._goal_mode = goal_mode  # 一等属性，而非 metadata dict 中的值

    @property
    def goal_mode(self) -> GoalMode | None:
        return self._goal_mode
```

工具访问 GoalMode 的两种方式：
1. **推荐：通过 QueryEngine 一等属性** — `context.engine.goal_mode`
2. **备选：通过 tool_metadata 专用 key** — `tool_metadata["goal_mode"]` 持有 GoalMode 实例引用

推荐方式 1，因为它使依赖关系显式化且避免 metadata key 冲突。两种方式在功能上等效（都引用同一个 GoalMode 实例）。

### 2.2 修改文件

#### `engine/query_engine.py`

```python
class QueryEngine:
    def __init__(self, ..., goal_mode: GoalMode | None = None):
        ...
        self._goal_mode = goal_mode  # 一等属性

    @property
    def goal_mode(self) -> GoalMode | None:
        return self._goal_mode

    async def submit_message(self, prompt: str) -> AsyncIterator[StreamEvent]:
        ...
        # 新增：如果 goal 是 active，路由到 drive_goal
        goal = self._goal_mode.get_goal() if self._goal_mode else None
        if goal is not None and goal.status == "active":
            async for event in self._drive_goal(prompt):
                yield event
            return
        # 否则走原有逻辑
        async for event in self._stream_query_with_guards(...):
            yield event

    async def _drive_goal(self, first_input: str) -> AsyncIterator[StreamEvent]:
        """多轮目标驱动循环。

        首轮：goal reminder 通过 inject_user_message 单独注入（在用户原始消息之前），
        然后 submit_message 处理用户原始 prompt。
        续轮：goal reminder + continuation prompt 合并为单个 user message 提交，
        确保提醒和续行指令作为一个连贯的 turn 一起到达模型。
        """
        turn_input = first_input
        is_first_turn = True

        while True:
            goal = self._goal_mode.get_goal()
            if goal and goal.status == "active" and goal.budget.over_budget:
                self._goal_mode.mark_blocked(reason="A configured budget was reached")
                yield GoalUpdatedEvent(
                    snapshot=self._goal_mode.get_goal(),
                    change=GoalChange(kind="lifecycle", status="blocked", reason="budget reached"),
                )
                return

            self._goal_mode.increment_turn()

            if is_first_turn:
                # 首轮：goal reminder 单独注入（作为独立的 user message 添加到会话历史），
                # 然后 submit_message 处理用户的原始 prompt。
                reminder = build_goal_reminder(self._goal_mode.get_goal())
                if reminder:
                    self.inject_user_message(reminder)
                async for event in self._stream_query_with_guards(turn_input):
                    yield event
                    if isinstance(event, AssistantTurnComplete) and self._goal_mode:
                        total_tokens = event.usage.input_tokens + event.usage.output_tokens
                        self._goal_mode.record_token_usage(total_tokens)
                is_first_turn = False
            else:
                # 续轮：goal reminder + continuation prompt 合并为单个 user message。
                # 确保提醒和续行指令一起到达模型，作为一个连贯的 turn。
                reminder = build_goal_reminder(self._goal_mode.get_goal())
                combined_input = f"{reminder}\n\n{GOAL_CONTINUATION_PROMPT}" if reminder else GOAL_CONTINUATION_PROMPT
                async for event in self._stream_query_with_guards(combined_input):
                    yield event
                    if isinstance(event, AssistantTurnComplete) and self._goal_mode:
                        total_tokens = event.usage.input_tokens + event.usage.output_tokens
                        self._goal_mode.record_token_usage(total_tokens)

            # 检查结果
            if self._turn_was_cancelled:
                self._goal_mode.pause_goal(reason="Paused after interruption", actor="runtime")
                yield GoalUpdatedEvent(
                    snapshot=self._goal_mode.get_goal(),
                    change=GoalChange(kind="lifecycle", status="paused", reason="interrupted", actor="runtime"),
                )
                return
            if self._turn_failed:
                self._goal_mode.pause_goal(reason=self._failure_reason, actor="runtime")
                yield GoalUpdatedEvent(
                    snapshot=self._goal_mode.get_goal(),
                    change=GoalChange(kind="lifecycle", status="paused", reason=self._failure_reason, actor="runtime"),
                )
                return

            goal = self._goal_mode.get_goal()
            if goal is None or goal.status != "active":
                return  # 模型通过 UpdateGoalTool 决定停止

            if goal.budget.over_budget:
                self._goal_mode.mark_blocked(reason="A configured budget was reached")
                yield GoalUpdatedEvent(
                    snapshot=self._goal_mode.get_goal(),
                    change=GoalChange(kind="lifecycle", status="blocked", reason="budget reached"),
                )
                return

            turn_input = None  # 续轮不再需要外部 input
```

#### `engine/stream_events.py`

替换 `GoalStatusEvent` 为统一的 `GoalUpdatedEvent`（匹配 kimi-code 模式）：

```python
@dataclass(frozen=True)
class GoalUpdatedEvent(StreamEvent):
    snapshot: GoalSnapshot | None   # 当前 goal 状态（null when cleared）
    change: GoalChange | None       # 变更描述

@dataclass(frozen=True)
class GoalChange:
    kind: Literal["lifecycle", "completion", "created"]
    status: str | None = None
    reason: str | None = None
    actor: str | None = None
    stats: GoalChangeStats | None = None

@dataclass(frozen=True)
class GoalChangeStats:
    turns_used: int
    tokens_used: int
    wall_clock_ms: int
```

> **设计理由**：用单一 `GoalUpdatedEvent` 替代原先 6-kind 的 `GoalStatusEvent`。TUI 只需监听一种事件类型，通过 `change.kind` 和 `change.status` 区分具体行为，更统一、更易处理。

### 2.3 工具注册

**始终注册 goal 工具**（而非仅在 goal_mode 存在时注册）。工具自行处理无 goal 的情况：

```python
# 在 build_tool_registry() 中——无条件注册
registry.register(CreateGoalTool())
registry.register(UpdateGoalTool())
registry.register(GetGoalTool())
registry.register(SetGoalBudgetTool())
```

各工具在无 goal 时的行为：
- **GetGoalTool**：返回 `{goal: null}`
- **CreateGoalTool**：创建新 goal（无 goal 时正常工作；已有 goal 时需 `replace=True`）
- **UpdateGoalTool**：返回错误 `"No current goal"`
- **SetGoalBudgetTool**：返回错误 `"No current goal"`

> **设计理由**：始终注册使得模型在用户请求时始终可以调用 CreateGoal，即使 GoalMode 未预先初始化。这避免了"工具时而可用时而不可用"导致的模型困惑。

### 2.4 Token 统计集成

从 `AssistantTurnComplete` 事件中提取 token usage（而非 usage callback）：

```python
# 在 _drive_goal 循环中——每次 yield 事件时检查
async for event in self._stream_query_with_guards(...):
    yield event
    if isinstance(event, AssistantTurnComplete) and self._goal_mode:
        total_tokens = event.usage.input_tokens + event.usage.output_tokens
        self._goal_mode.record_token_usage(total_tokens)
```

> **设计理由**：OpenHarness 的 `_stream_query_with_guards` 没有直接的 usage callback。usage 数据来自 `AssistantTurnComplete.usage` 事件。在 drive_goal 循环中拦截这些事件是自然的 token 统计点。

### 2.5 测试

```
tests/test_goal_driver.py
  - test_drive_goal_single_turn_complete
  - test_drive_goal_multi_turn_iteration
  - test_drive_goal_budget_exhaustion
  - test_drive_goal_user_interrupt
  - test_drive_goal_api_error_pauses
  - test_drive_goal_continuation_prompt_injected
  - test_drive_goal_reminder_injection_first_turn_separate
  - test_drive_goal_reminder_injection_continuation_combined
  - test_drive_goal_token_stats_from_assistant_turn_complete
```

### 2.6 验证标准

- [ ] `uv run pytest -q tests/test_goal_driver.py` 全部通过
- [ ] 手动测试：`/goal` 创建目标 → 模型自主执行多轮 → 完成/受阻
- [ ] 预算限制生效：设置 turn_budget=2 → 2 轮后自动 blocked
- [ ] UpdateGoalTool complete/blocked/paused 正确设置 stop_turn + stop_batch
- [ ] Tool loop 在 stop_turn=True 时终止

---

## Phase 3：Slash 命令与 TUI 雇用

**目标**：`/goal` 命令完整可用，TUI 显示目标状态面板。

### 3.1 新增/修改文件

#### `commands/registry.py`

新增 `/goal` 命令注册（含边界检查）：

```python
GOAL_ALREADY_EXISTS_MSG = (
    "A goal is already active. Use `/goal replace <objective>` to replace it, "
    "or `/goal status` to inspect it."
)

async def _goal_handler(args: str, context: CommandContext) -> CommandResult:
    parsed = parse_goal_command(args)
    goal_mode = context.engine.goal_mode   # 通过 QueryEngine 一等属性访问

    if parsed["kind"] == "status":
        snapshot = goal_mode.get_goal() if goal_mode else None
        if snapshot is None:
            return CommandResult(message="No goal set. Start one with `/goal <objective>`.")
        return CommandResult(message=format_goal_status(snapshot))

    if parsed["kind"] == "pause":
        snapshot = goal_mode.pause_goal()
        return CommandResult(message="Goal paused. Use `/goal resume` to continue.")

    if parsed["kind"] == "resume":
        # 边界检查
        if not context.engine.model_is_configured():
            return CommandResult(message="LLM not set. Configure a model before resuming a goal.")
        snapshot = goal_mode.get_goal()
        if snapshot is None:
            return CommandResult(message="No goal to resume.")
        if snapshot.status not in ("paused", "blocked"):
            return CommandResult(message=f"Goal is {snapshot.status} and cannot be resumed. Use `/goal status` to inspect it.")

        # 权限模式检查（详见设计文档 10.7.5）
        permission_mode = context.app_state.permission_mode
        if permission_mode == PermissionMode.PLAN:
            return CommandResult(
                message="Plan mode blocks all mutating tools. Switch to Auto or Default before resuming a goal."
            )
        if permission_mode == PermissionMode.DEFAULT:
            # 返回特殊 CommandResult，由 TUI 层弹出 GoalStartPermissionPrompt 对话框
            return CommandResult(
                message="",
                goal_action="permission_prompt_resume",
            )

        # FULL_AUTO → 直接恢复
        goal_mode.resume_goal()
        return CommandResult(
            message="Resuming goal...",
            submit_prompt="Resume the active goal.",
        )

    if parsed["kind"] == "cancel":
        goal_mode.cancel_goal()
        # 注入 cancelled reminder，让模型知道目标已取消，忽略之前的 active-goal reminders
        context.engine.inject_user_message(GOAL_CANCELLED_REMINDER)
        return CommandResult(message="Goal cancelled.")

    if parsed["kind"] == "create":
        # 边界检查
        if not context.engine.model_is_configured():
            return CommandResult(message="LLM not set. Configure a model before starting a goal.")
        snapshot = goal_mode.get_goal() if goal_mode else None
        if snapshot is not None and snapshot.status == "active" and not parsed["replace"]:
            return CommandResult(message=GOAL_ALREADY_EXISTS_MSG)

        # 权限模式检查（详见设计文档 10.7）
        permission_mode = context.app_state.permission_mode
        if permission_mode == PermissionMode.PLAN:
            return CommandResult(
                message="Plan mode blocks all mutating tools. Switch to Auto or Default before starting a goal."
            )
        if permission_mode == PermissionMode.DEFAULT:
            # 返回特殊 CommandResult，由 TUI 层弹出 GoalStartPermissionPrompt 对话框
            # 用户在对话框中选择"切换到 Auto 并启动"/"保持 Default 启动"/"取消"
            return CommandResult(
                message="",
                goal_action="permission_prompt_create",
                goal_objective=parsed["objective"],
                goal_replace=parsed["replace"],
            )

        # FULL_AUTO → 直接创建
        goal_mode.create_goal(parsed["objective"], replace=parsed["replace"])
        return CommandResult(
            message=f"Goal set: {parsed['objective']}",
            submit_prompt=parsed["objective"],
        )

registry.register(SlashCommand(
    "goal",
    "Set, manage, and track autonomous goals",
    _goal_handler,
    subcommands=["status", "pause", "resume", "cancel", "replace"],
))
```

#### `CommandResult` 扩展

为支持权限对话框的数据传递，`CommandResult` 新增以下可选字段：

```python
@dataclass
class CommandResult:
    message: str
    submit_prompt: str | None = None
    # ... 现有字段 ...
    goal_action: str | None = None          # 新增："permission_prompt_create" | "permission_prompt_resume"
    goal_objective: str | None = None       # 新增：create 时的目标文本
    goal_replace: bool = False              # 新增：create 时是否替换现有目标
```

TUI 层收到 `goal_action` 非空的 `CommandResult` 时，弹出 `GoalStartPermissionPrompt` 对话框，而非直接提交 prompt。

### 3.2 TUI 组件

> **前端文件路径说明**：前端组件位于 `_frontend/src/components/` 子包内（随 openharness 包安装）。开发时修改本地源码后需要重新构建并安装包才能生效。

#### `_frontend/src/components/GoalPanel.tsx`

```tsx
// 渲染目标状态面板（类似 TodoPanel）
// - 目标名称 + 状态标签 (active/paused/blocked)
// - 进度条：turns/tokens/time vs budget
// - 操作按钮：pause / resume / cancel
```

#### `_frontend/src/components/StatusBar.tsx`

修改：在 StatusBar 中显示当前 goal 简要状态（active 时显示 turns 进度）。

#### `GoalStartPermissionPrompt` 权限选择对话框

当 `CommandResult.goal_action` 非空时，TUI 弹出权限选择对话框。这是 OpenHarness 版本的 kimi-code `GoalStartPermissionPrompt`，适配 React/Ink TUI 框架。

```tsx
// _frontend/src/components/GoalStartPermissionPrompt.tsx

interface GoalStartPermissionPromptProps {
    action: "permission_prompt_create" | "permission_prompt_resume";
    objective?: string;       // create 时的目标文本
    replace?: boolean;        // create 时是否替换现有目标
    onSelect: (choice: "switch_auto" | "keep_default" | "cancel") => void;
}

// 渲染一个模态对话框，包含：
// - 标题：根据 action 显示 "Start Goal" 或 "Resume Goal"
// - 3 个选项按钮（纵向排列）：
//   1. "Switch to Auto and {action}" （推荐，高亮）
//   2. "Keep Default and {action}" （附带警告文字）
//   3. "Cancel"
// - 警告文字（仅在选项 2 下方显示）：
//   "Default mode asks you before OpenHarness runs commands, edits files,
//    or takes other risky actions.
//    Default mode is not suitable for unattended goal work — the goal will
//    frequently pause and wait for your approval.
//    Consider switching to Auto mode for a smoother goal experience."
```

**选项处理逻辑：**

```python
# 在 TUI 的 CommandResult 处理层
def _handle_goal_permission_choice(choice: str, result: CommandResult):
    if choice == "switch_auto":
        # 复用 /permissions full_auto 的切换逻辑
        _switch_to_full_auto(context)
        # 然后执行原始 goal 操作
        if result.goal_action == "permission_prompt_create":
            goal_mode.create_goal(result.goal_objective, replace=result.goal_replace)
            # submit_prompt = result.goal_objective → 进入 drive_goal
        elif result.goal_action == "permission_prompt_resume":
            goal_mode.resume_goal()
            # submit_prompt = "Resume the active goal." → 进入 drive_goal

    elif choice == "keep_default":
        # 保持 DEFAULT 模式，直接执行 goal 操作（带警告）
        if result.goal_action == "permission_prompt_create":
            goal_mode.create_goal(result.goal_objective, replace=result.goal_replace)
        elif result.goal_action == "permission_prompt_resume":
            goal_mode.resume_goal()

    elif choice == "cancel":
        # 不做任何操作，恢复输入框内容
        pass

def _switch_to_full_auto(context: CommandContext):
    """复用 /permissions full_auto 命令的实现逻辑。"""
    settings = load_settings()
    settings.permission.mode = PermissionMode.FULL_AUTO
    save_settings(settings)
    context.engine.set_permission_checker(
        PermissionChecker(mode=PermissionMode.FULL_AUTO)
    )
    _sync_full_auto_tools(context, is_full_auto=True)
    context.app_state.permission_mode = PermissionMode.FULL_AUTO
```

#### 事件处理

TUI 监听 `GoalUpdatedEvent`（取代原先的 `GoalStatusEvent`），通过 `change.kind` 和 `change.status` 更新面板：

| `change.kind` | `change.status` | 行为 |
|---|---|---|
| `created` | `active` | 显示面板 |
| `lifecycle` | `active` | 更新进度 |
| `lifecycle` | `paused` | 更新状态标签为 paused |
| `lifecycle` | `blocked` | 更新状态标签为 blocked |
| `completion` | `completed` | 显示完成摘要 → 淡出面板 |
| `lifecycle` | `cancelled` | 隐藏面板 |

### 3.3 权限测试

```
tests/test_goal_permission.py
  - test_goal_create_full_auto_no_prompt           # FULL_AUTO 模式直接创建，不弹对话框
  - test_goal_create_default_returns_prompt_action # DEFAULT 模式返回 goal_action="permission_prompt_create"
  - test_goal_create_plan_rejected                 # PLAN 模式直接拒绝
  - test_goal_resume_full_auto_no_prompt           # FULL_AUTO 模式直接恢复
  - test_goal_resume_default_returns_prompt_action  # DEFAULT 模式返回 goal_action="permission_prompt_resume"
  - test_goal_resume_plan_rejected                  # PLAN 模式直接拒绝
  - test_switch_to_full_auto                         # 验证 _switch_to_full_auto 正确调用 _sync_full_auto_tools 和 set_permission_checker
  - test_permission_choice_switch_auto               # 选择"切换到 Auto"后 goal 正常创建
  - test_permission_choice_keep_default              # 选择"保持 Default"后 goal 在 DEFAULT 模式创建
  - test_permission_choice_cancel                    # 选择"取消"后不创建 goal
```

### 3.4 验证标准

- [ ] `/goal Ship feature X` → 模型开始自主执行
- [ ] `/goal pause` → 执行中断，状态变为 paused
- [ ] `/goal resume` → 恢复执行（边界检查：未配置 LLM 时报错，无 goal 时报错，不可恢复状态时报错）
- [ ] `/goal cancel` → 清除目标 + 注入 cancelled reminder
- [ ] `/goal status` → 显示 turns/tokens/time 统计
- [ ] `/goal` 在已有 active goal 时报错提示使用 `/goal replace`
- [ ] GoalPanel 在 TUI 中正确渲染
- [ ] StatusBar 显示 goal 进度
- [ ] `uv run pytest -q tests/test_goal_permission.py` 全部通过
- [ ] `FULL_AUTO` 模式下 `/goal` 直接创建/恢复，无对话框
- [ ] `DEFAULT` 模式下 `/goal` 弹出 `GoalStartPermissionPrompt` 对话框
- [ ] 对话框选择"切换到 Auto"后权限模式变为 `FULL_AUTO`，goal 正常启动
- [ ] 对话框选择"保持 Default"后 goal 在 `DEFAULT` 模式下启动（工具需确认）
- [ ] 对话框选择"取消"后不创建 goal，输入框恢复
- [ ] `PLAN` 模式下 `/goal` 直接拒绝，提示切换权限模式
- [ ] `/goal resume` 在 `DEFAULT`/`PLAN` 模式下执行同样的权限检查
- [ ] Goal 完成后权限模式不自动恢复

---

## Phase 4：健壮性与边界情况

### 4.1 任务清单

| 任务 | 优先级 | 说明 |
|------|--------|------|
| Session 恢复 | P0 | `normalize_after_replay` 在 `/resume` 时降级 active → paused |
| Context overflow 处理 | P0 | goal turn 中 overflow 不改变 goal 状态，compact 后继续 |
| 并发安全 | P1 | goal 执行中用户输入新消息 → steer buffer 或拒绝 |
| Max turns 交互 | P1 | goal 的 turn 计数独立于 engine 的 max_turns |
| Hook 集成 | P1 | GoalCreated/GoalCompleted/GoalBlocked hook events |
| 错误分类 | P2 | API 错误按类型生成不同 pause reason |
| Tool loop stop flag 处理 | P0 | engine tool loop 检查 ToolResult.stop_turn / stop_batch 并终止循环 |
| Cancel reminder 注入 | P0 | `/goal cancel` 后注入 GOAL_CANCELLED_REMINDER |

### 4.2 验证标准

- [ ] 进程重启后 `/goal resume` 恢复正常
- [ ] Context overflow 后 goal 继续执行
- [ ] Hook 能阻止 goal continuation
- [ ] ToolResult.stop_turn 正确终止 tool loop
- [ ] `/goal cancel` 后模型不再响应已取消 goal 的 reminders

---

## 文件变更清单

### 新增文件

```
src/openharness/goal/__init__.py
src/openharness/goal/state.py
src/openharness/goal/injection.py
src/openharness/goal/budget.py
src/openharness/tools/create_goal_tool.py
src/openharness/tools/update_goal_tool.py
src/openharness/tools/get_goal_tool.py
src/openharness/tools/set_goal_budget_tool.py
tests/test_goal_state.py
tests/test_goal_injection.py
tests/test_goal_tools.py
tests/test_goal_budget.py
tests/test_goal_driver.py
tests/test_goal_permission.py
_frontend/src/components/GoalPanel.tsx
_frontend/src/components/GoalStartPermissionPrompt.tsx
```

### 修改文件

```
src/openharness/engine/query_engine.py     # 新增 goal_mode 一等属性 + drive_goal 方法 + token 统计
src/openharness/engine/stream_events.py     # GoalUpdatedEvent/GoalChange/GoalChangeStats 替代 GoalStatusEvent
src/openharness/engine/tool_result.py       # ToolResult 新增 stop_turn / stop_batch 字段
src/openharness/engine/query.py             # tool loop 检查 stop_turn / stop_batch flag
src/openharness/engine/command_result.py    # CommandResult 新增 goal_action / goal_objective / goal_replace 字段
src/openharness/commands/registry.py        # 新增 /goal 命令 + 边界检查 + 权限模式检查
src/openharness/cli.py                      # 初始化 GoalMode
src/openharness/ui/permission_dialog.py     # 新增 _switch_to_full_auto() + _handle_goal_permission_choice()
_frontend/src/components/StatusBar.tsx      # 显示 goal 状态
_frontend/src/App.tsx                       # 处理 GoalUpdatedEvent + CommandResult.goal_action 路由
```

---

## 里程碑与时间估算

| Phase | 预估工时 | 依赖 |
|-------|---------|------|
| Phase 1: 状态机 + 工具 + budget | 3-4 天 | 无 |
| Phase 2: QueryEngine 集成 | 2-3 天 | Phase 1 |
| Phase 3: TUI 集成 | 1-2 天 | Phase 2 |
| Phase 4: 健壮性 | 1-2 天 | Phase 2 |
| **总计** | **7-11 天** | |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 模型不遵守 UpdateGoal 协议 | Continuation prompt 中强化指导；driver 硬预算兜底；UpdateGoalTool 的 stop_turn/stop_batch 强制终止 tool loop |
| Goal turn 中 context overflow | 复用现有 auto-compact 机制，不改变 goal 状态 |
| 无限循环 | 默认 turn_budget=50 硬上限；连续 silent stop 检测 |
| 用户中断后状态不一致 | `pause_goal(actor="runtime")` 确保中断后状态为 paused |
| TUI 渲染阻塞 | GoalPanel 作为独立组件，不阻塞主对话流 |
| 工具始终注册导致模型误用 | CreateGoalTool description 明确限制使用场景；无 goal 时其它工具返回错误 |
| Goal reminder 注入时机错误 | 首轮单独注入、续轮合并注入，确保提醒和续行指令到达时机正确 |
| 权限切换后用户困惑 | GoalStartPermissionPrompt 对话框明确告知切换行为；goal 完成后不自动恢复权限，避免突然弹出确认提示；用户可随时 `/permissions default` 手动切回 |
