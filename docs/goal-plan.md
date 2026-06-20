# /goal 实施计划

> 基于 `docs/goal-design.md`，分阶段交付 goal 驱动执行框架。
>
> **本计划的所有 API 假设已对照 OpenHarness 源码验证**：`engine/query_engine.py`、`engine/turn_stages.py`、`engine/types.py`、`engine/query.py`、`tools/base.py`、`commands/core.py`、`commands/registry.py`、`commands/skills.py`、`services/session_backend.py`、`permissions/checker.py`、`permissions/modes.py`、`ui/runtime.py`、`frontend/terminal/src/`。

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
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class GoalBudgetLimits:
    turn_budget: int | None = None
    token_budget: int | None = None
    wall_clock_budget_ms: int | None = None

@dataclass
class GoalState:
    goal_id: str
    objective: str
    completion_criterion: str | None = None
    # 仅 3 个 durable 状态。complete 是瞬态（mark_complete 后立即清除，不落盘）；
    # cancel 是删除动作，不是状态。绝不出现 "completed" / "cancelled" 这两个值。
    status: Literal["active", "paused", "blocked"] = "active"
    last_actor: str | None = None   # "user" | "model" | "runtime" | "system"
    turns_used: int = 0
    tokens_used: int = 0
    wall_clock_ms: int = 0
    wall_clock_resumed_at: float | None = None   # epoch ms
    budget_limits: GoalBudgetLimits = field(default_factory=GoalBudgetLimits)
    terminal_reason: str | None = None

class GoalMode:
    """单目标生命周期管理器。状态序列化到传入的 tool_metadata["goal_state"]。"""

    STATE_KEY = "goal_state"      # 不带 _ 前缀！见设计 §8.1
    MODE_KEY = "goal_mode"        # 运行时实例引用的 key

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

    # --- Session 恢复 ---
    def normalize_after_replay(self) -> None: ...

    # --- Persistence ---
    def _persist(self) -> None:
        """序列化 self._state 到 self._metadata[self.STATE_KEY]；None 时删除 key。"""
        if self._state is None:
            self._metadata.pop(self.STATE_KEY, None)
        else:
            self._metadata[self.STATE_KEY] = self._serialize(self._state)

    def _restore_from_metadata(self) -> GoalState | None: ...
```

**持久化方式**：每次状态变更后调用 `_persist()`，把 `GoalState` 序列化到 `tool_metadata["goal_state"]`。

> **同步方法的设计权衡**：kimi-code 用 async 方法是因为要做 append-only record logging 和 event emitting（异步 I/O）。OpenHarness 用 `tool_metadata` 内存字典，同步方法可行。代价是放弃 record replay 能力——状态只能从最后一次 `tool_metadata` 快照恢复。

> **⚠️ status 取值红线**：`GoalState.status` 永远只有 `"active" | "paused" | "blocked"`。`mark_complete` 内部临时置 `complete` 后**立即清除记录**（`_state = None` + `_persist()`），`complete` 绝不落盘。`cancel_goal` 直接 `_state = None`。前版计划把 `"completed"`/`"cancelled"` 写进 status 注释，已删除。

#### `goal/injection.py`

```python
def escape_untrusted_text(text: str) -> str:
    """与 kimi-code escapeUntrustedText 一致。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def build_goal_reminder(snapshot: GoalSnapshot) -> str | None:
    """根据 goal 状态生成注入文本。
    - active: 完整提醒（含 budget guidance）
    - paused: 轻量提示（含 completion_criterion）
    - blocked: 轻量提示（含 completion_criterion —— 与 kimi-code buildBlockedNote 一致）
    对 objective 和 completion_criterion 用 escape_untrusted_text() 转义。"""

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

> **不实现 `<system-reminder>` 标签包裹**：见设计 §5.4——OpenHarness 无 system-reminder 机制，注入纯文本。如目标模型验证后确认识别该标签，再在 `build_goal_reminder` 末尾按需包裹，但**不写入计划为既定行为**。

#### `goal/budget.py`

```python
def normalize_budget_input(value: float, unit: str) -> tuple[int | float, str]:
    """- turns/tokens: max(1, round(value))
       - 时间单位: 原值保留（由 budget_limits_from_input 做范围校验）"""

def budget_limits_from_input(value: float, unit: str) -> GoalBudgetLimits | None:
    """从工具参数构造 GoalBudgetLimits，含合理性校验。
    - 时间 budget 必须 >= 1 秒 且 <= 24 小时，否则返回 None
    - turns/tokens budget 做 max(1, round(value)) 标准化
    返回 None 时，调用方报错 "Goal budget not set: {value} {unit} is not a reasonable goal budget."
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

每个工具继承 `BaseTool`（`tools/base.py:41`）。**工具经 `ToolExecutionContext.metadata["goal_mode"]` 访问 GoalMode 实例**——这是 OpenHarness 无状态工具架构下的唯一可行路径（见设计 §4）。

> **为什么不用 `context.engine.goal_mode`**：`ToolExecutionContext`（`tools/base.py:22-29`）只有 `cwd/metadata/hook_executor/approval_coordinator/spawn_lock`，**没有 `engine` 字段**。`query.py:836-848` 构造 context 时把 `tool_metadata` 展开进 `metadata`，所以 GoalMode 实例放进 `tool_metadata["goal_mode"]` 后，工具能经 `context.metadata["goal_mode"]` 拿到。

#### `create_goal_tool.py`

```python
class CreateGoalTool(BaseTool):
    name = "create_goal"

    class InputModel(BaseModel):
        objective: str
        completion_criterion: str | None = None
        replace: bool = False

    async def execute(self, arguments: InputModel, context: ToolExecutionContext) -> ToolResult:
        goal_mode = context.metadata.get("goal_mode")
        if goal_mode is None:
            return ToolResult(is_error=True, output="Goal mode is not available.")
        snapshot = goal_mode.create_goal(
            arguments.objective,
            completion_criterion=arguments.completion_criterion,
            replace=arguments.replace,
        )
        return ToolResult(output=json.dumps({"goal": snapshot.to_dict()}, indent=2))

    def to_api_schema(self) -> dict: ...   # 手写 LLM-friendly schema（见 base.py:67 注释）
```

#### `update_goal_tool.py` —— 停止信号走 ToolResult.metadata

```python
class UpdateGoalTool(BaseTool):
    name = "update_goal"

    class InputModel(BaseModel):
        status: Literal["active", "complete", "paused", "blocked"]   # complete 无 d
        reason: str | None = None

    async def execute(self, arguments: InputModel, context: ToolExecutionContext) -> ToolResult:
        goal_mode = context.metadata.get("goal_mode")
        if goal_mode is None:
            return ToolResult(is_error=True, output="No current goal.")

        # complete / blocked / paused 需要停止当前 turn；active 继续。
        # 停止信号不通过新增 ToolResult 字段传递，而是塞进 metadata dict
        # （ToolResult.metadata 已用于 noop/doom-loop 等元数据，见 query.py:685）。
        # turn loop 在 post_tool_stage 检查 metadata["goal_stop_turn"]。
        stop_meta = {"goal_stop_turn": True}

        if arguments.status == "complete":
            snapshot = goal_mode.mark_complete(reason=arguments.reason, actor="model")
            # mark_complete 已 emit completion 事件并清除记录；
            # 注入摘要提示让模型写最终回复
            if snapshot is not None:
                context.metadata["__pending_injection__"] = build_completion_summary_prompt(snapshot)
            return ToolResult(output="Goal marked complete.", metadata=stop_meta)

        if arguments.status == "blocked":
            snapshot = goal_mode.mark_blocked(reason=arguments.reason, actor="model")
            if snapshot is not None:
                context.metadata["__pending_injection__"] = build_blocked_reason_prompt(snapshot)
            return ToolResult(output="Goal marked blocked.", metadata=stop_meta)

        if arguments.status == "paused":
            goal_mode.pause_goal(reason=arguments.reason, actor="model")
            return ToolResult(output="Goal paused.", metadata=stop_meta)

        # active → resume，无停止信号
        goal_mode.resume_goal(actor="model")
        return ToolResult(output="Goal resumed.")
```

> **关于 `__pending_injection__`**：completion/blocked 摘要提示需要在 tool result 之后注入成一条 user message（让模型基于它写最终回复）。但工具内直接调 `engine.inject_user_message` 拿不到 engine。方案：把待注入文本暂存进 `context.metadata`（这是 tool_metadata 的展开），由 `_drive_goal` 循环或 `post_tool_stage` 在 turn 结束后取出并注入。详见 Phase 2.2。
>
> **替代方案（更简洁，推荐）**：不暂存，而是让 `_drive_goal` 在每轮结束检测到 status 变为 `complete`/`blocked` 时，自己调 `build_completion_summary_prompt`/`build_blocked_reason_prompt` 注入。这样工具只负责改状态 + 返回 stop 信号，注入逻辑集中在 driver。Phase 2 采用此方案。

#### `get_goal_tool.py` / `set_goal_budget_tool.py`

```python
class GetGoalTool(BaseTool):
    name = "get_goal"
    class InputModel(BaseModel):
        pass
    async def execute(self, arguments, context: ToolExecutionContext) -> ToolResult:
        goal_mode = context.metadata.get("goal_mode")
        snapshot = goal_mode.get_goal() if goal_mode else None
        return ToolResult(output=json.dumps({"goal": snapshot.to_dict() if snapshot else None}, indent=2))

class SetGoalBudgetTool(BaseTool):
    name = "set_goal_budget"
    class InputModel(BaseModel):
        value: float
        unit: Literal["turns", "tokens", "seconds", "minutes", "hours"]
    async def execute(self, arguments, context: ToolExecutionContext) -> ToolResult:
        goal_mode = context.metadata.get("goal_mode")
        if goal_mode is None or goal_mode.get_goal() is None:
            return ToolResult(is_error=True, output="No current goal.")
        limits = budget_limits_from_input(arguments.value, arguments.unit)
        if limits is None:
            return ToolResult(is_error=True,
                output=f"Goal budget not set: {arguments.value} {arguments.unit} is not a reasonable goal budget.")
        snapshot = goal_mode.set_budget_limits(limits)
        return ToolResult(output=json.dumps({"goal": snapshot.to_dict()}, indent=2))
```

### 1.4 ⚠️ 不新增 `ToolResult.stop_turn` / `stop_batch` 字段

> **前版计划要给 `ToolResult` 加 `stop_turn`/`stop_batch` 字段——本计划撤销这个改动**。理由：
>
> 1. `ToolResult` 是 `@dataclass(frozen=True)`（`tools/base.py:33`），所有工具共用。为 goal 一个特性给它加两个语义模糊的字段，污染面太大。
> 2. OpenHarness 的 tool loop 不是「逐个执行可中断」的简单循环，而是 `turn_stages.py` 的 8-stage 流水线，且 `tool_execution_stage`（`turn_stages.py:594-656`）用 `asyncio.gather` **并行执行同一批所有 tool_call**。所谓 `stop_batch`（停止同批剩余工具）在这种并行模型里没有自然的落点——除非把并行改串行，那是大重构。
> 3. 现有 `ToolResult.metadata` dict 已经是传递元数据的标准通道（`query.py:685` 的 noop 标记 `result_metadata={"noop": True}` 就是先例）。
>
> **改用**：`ToolResult.metadata["goal_stop_turn"] = True`。`post_tool_stage`（`turn_stages.py:663`）读 tool result 的 `result_metadata`（即 `raw_result.metadata`，见 `query.py:883-884` 透传），发现该标志则 `state.action = TurnAction.STOP`。这是最小改动。

### 1.5 测试

```
tests/
├── test_goal_state.py
│   - test_create_goal
│   - test_create_goal_replaces_existing
│   - test_create_goal_empty_objective_raises
│   - test_pause_resume_cycle
│   - test_cancel_clears_state (status 永不为 "cancelled"，记录直接删除)
│   - test_mark_complete_clears_state (status 永不为 "completed"，记录直接删除)
│   - test_mark_blocked_stays_resumable
│   - test_budget_over_budget
│   - test_normalize_after_replay (active → paused)
│   - test_persist_and_restore (用 "goal_state" key，非 "_goal_state")
│
├── test_goal_injection.py
│   - test_active_reminder_format (含 budget guidance，used/total 阈值)
│   - test_paused_note_format (含 completion_criterion)
│   - test_blocked_note_format (含 completion_criterion —— 与 kimi-code 一致)
│   - test_untrusted_objective_escaping
│   - test_usage_fraction_uses_used_over_total  # 验证 used/budget，不是 remaining/budget
│
├── test_goal_tools.py
│   - test_create_goal_tool
│   - test_update_goal_tool_complete_sets_goal_stop_turn
│   - test_update_goal_tool_blocked_sets_goal_stop_turn
│   - test_update_goal_tool_paused_sets_goal_stop_turn
│   - test_update_goal_tool_active_no_stop_meta
│   - test_get_goal_tool (无 goal 返回 {goal: null})
│   - test_set_goal_budget_tool_time_reasonable
│   - test_set_goal_budget_tool_time_unreasonable
│   - test_set_goal_budget_tool_turns_normalized
│   - test_tools_access_goal_mode_via_context_metadata  # 关键：验证 context.metadata["goal_mode"]
│
├── test_goal_budget.py
│   - test_normalize_budget_input_turns
│   - test_normalize_budget_input_tokens
│   - test_budget_limits_from_input_time_valid
│   - test_budget_limits_from_input_time_out_of_range
```

### 1.6 验证标准

- [ ] `uv run pytest -q tests/test_goal_state.py` 全部通过
- [ ] `uv run pytest -q tests/test_goal_tools.py` 全部通过
- [ ] `uv run pytest -q tests/test_goal_budget.py` 全部通过
- [ ] `uv run ruff check src/openharness/goal src/openharness/tools/*goal*.py` 无报错
- [ ] 确认 `GoalState.status` 取值仅 `active/paused/blocked`（grep 不应出现 completed/cancelled 作为 status）

---

## Phase 2：QueryEngine 集成 — _drive_goal 循环

**目标**：`/goal` 命令创建目标后，QueryEngine 自动驱动多轮执行。

### 2.1 GoalMode 注入

GoalMode 实例放进 `tool_metadata`，在 `cli.py` 初始化 QueryEngine 时创建并注入：

```python
# cli.py（或 QueryEngine 构造处）
tool_metadata = {...}
goal_mode = GoalMode(tool_metadata)
tool_metadata[GoalMode.MODE_KEY] = goal_mode   # "goal_mode" —— 运行时引用
engine = QueryEngine(..., tool_metadata=tool_metadata)
```

> **不在 `QueryEngine.__init__` 加 `goal_mode` 参数**：前版计划加了一等属性 `self._goal_mode`，但工具拿不到 engine，这个属性对工具无用。统一走 `tool_metadata["goal_mode"]`，driver 和工具都从同一处取。

### 2.2 修改文件

#### `engine/types.py` —— 新增 GOAL_STATE 枚举（关键）

```python
class ToolMetadataKey(str, Enum):
    ...
    GOAL_STATE = "goal_state"     # 新增，不带 _ 前缀

    @classmethod
    def all_persisted_keys(cls) -> tuple["ToolMetadataKey", ...]:
        return (
            ...,
            cls.GOAL_STATE,       # 加入持久化白名单
        )
    # ⚠️ 不要加进 turn_checkpoint_keys() —— goal 状态是跨 turn 的，不应随单 turn 取消回滚
```

> 这是前版完全遗漏的改动。没有它，`_goal_state`/`goal_state` 既不会持久化（不在白名单），`_goal_state` 还会被回滚（在 turn_checkpoint_keys，因 `_` 前缀）。必须用无下划线的 `GOAL_STATE` + 加进 `all_persisted_keys` + 不加进 `turn_checkpoint_keys`。

#### `services/session_backend.py` + `services/session_storage.py` —— 扩展快照存储 tool_metadata

当前 `save_snapshot`（`session_backend.py:72-81`）只存 `model/system_prompt/messages/usage`。新增 `tool_metadata` 参数，序列化 `ToolMetadataKey.all_persisted_keys()` 对应子集：

```python
def save_snapshot(self, *, cwd, model, system_prompt, messages, usage,
                  tool_metadata: dict | None = None) -> Path:
    ...
    if tool_metadata:
        persisted = {
            key.value: tool_metadata[key.value]
            for key in ToolMetadataKey.all_persisted_keys()
            if key.value in tool_metadata
        }
        snapshot["tool_metadata"] = persisted

def load_by_id(...) / load_latest(...):
    ...  # 恢复时把 snapshot["tool_metadata"] 回填
```

> 前版文件变更清单漏了这两个文件，必须补上。

#### `engine/query_engine.py` —— submit_message 分支路由到 _drive_goal

`_drive_goal` 作为 `submit_message` 的内部分支，复用其 hook 执行与 memory 收尾逻辑（`query_engine.py:678-718`）：

```python
async def submit_message(self, prompt: str | ConversationMessage) -> AsyncIterator[StreamEvent]:
    # ... 现有的 append user message、hook、构造 context 逻辑 ...
    goal_mode = self._tool_metadata.get("goal_mode")
    goal = goal_mode.get_goal() if goal_mode else None

    if goal is not None and goal.status == "active":
        # 路由到 goal 驱动循环（复用同一 QueryContext）
        async for event in self._drive_goal(context=context, query_messages=query_messages,
                                            first_input=user_message.text):
            yield event
    else:
        async for event in self._stream_query_with_guards(context=context, query_messages=query_messages):
            yield event
    # ... finally 的 memory 收尾（_update_session_memory / _schedule_extract_memories）保持不变 ...

async def _drive_goal(self, *, context, query_messages, first_input: str) -> AsyncIterator[StreamEvent]:
    """多轮目标驱动循环。见设计 §5.2。"""
    goal_mode = self._tool_metadata["goal_mode"]
    is_first_turn = True

    while True:
        # 1. 预算前置检查
        goal = goal_mode.get_goal()
        if goal and goal.status == "active" and goal.budget.over_budget:
            goal_mode.mark_blocked(reason="A configured budget was reached", actor="runtime")
            yield GoalUpdatedEvent(snapshot=goal_mode.get_goal(),
                                   change=GoalChange(kind="lifecycle", status="blocked",
                                                     reason="budget reached", actor="runtime"))
            return

        # 2. 计入统计
        goal_mode.increment_turn()

        # 3. 注入 reminder + continuation（首轮分开，续轮合并）
        if is_first_turn:
            reminder = build_goal_reminder(goal_mode.get_goal())
            if reminder:
                self.inject_user_message(reminder)   # 合并到尾部 user msg
            # first_input 已在 submit_message 开头 append，这里不再重复
        else:
            reminder = build_goal_reminder(goal_mode.get_goal())
            combined = f"{reminder}\n\n{GOAL_CONTINUATION_PROMPT}" if reminder else GOAL_CONTINUATION_PROMPT
            query_messages.append(ConversationMessage.from_user_text(combined))
            self._messages.append(ConversationMessage.from_user_text(combined))

        # 4. 运行一轮（拦截 AssistantTurnComplete 统计 token）
        local_messages = list(query_messages)
        async for event in self._stream_query_with_guards(context=context, query_messages=local_messages):
            yield event
            if isinstance(event, AssistantTurnComplete):
                goal_mode.record_token_usage(event.usage.total_tokens)
        query_messages = local_messages
        is_first_turn = False

        # 5. 中断检测 —— 捕获 CancelledError（Ctrl+C 由 runtime.py 传导）
        #    注意：_stream_query_with_guards 的 auto-continue guard 不暴露中断标志，
        #    需在 try/except asyncio.CancelledError 中包裹上面的 async for。
        # 6. 检查 status 变化（UpdateGoal 工具改的）
        goal = goal_mode.get_goal()
        if goal is None:
            return  # complete（已清除）或 cancel
        if goal.status == "complete":
            # 注入摘要提示，让模型写最终回复（这一轮不进 drive_goal 循环）
            self.inject_user_message(build_completion_summary_prompt(goal))
            # 跑最后一轮让模型写摘要，然后 clear
            async for event in self._stream_query_with_guards(context=context, query_messages=query_messages):
                yield event
            goal_mode._clear_internal()
            yield GoalUpdatedEvent(snapshot=None, change=None)
            return
        if goal.status != "active":
            return  # paused / blocked，模型决定停止

        # 7. 预算后置检查
        if goal.budget.over_budget:
            goal_mode.mark_blocked(reason="A configured budget was reached", actor="runtime")
            yield GoalUpdatedEvent(snapshot=goal_mode.get_goal(),
                                   change=GoalChange(kind="lifecycle", status="blocked",
                                                     reason="budget reached", actor="runtime"))
            return
```

> **token 统计**：从 `AssistantTurnComplete.usage`（`stream_events.py:52-56`）取，`event.usage.total_tokens`。前版写 `input_tokens + output_tokens` 也可，但 `total_tokens` 更直接。

#### `engine/stream_events.py` —— 新增 GoalUpdatedEvent

```python
@dataclass(frozen=True)
class GoalUpdatedEvent(StreamEvent):
    snapshot: GoalSnapshot | None
    change: GoalChange | None

@dataclass(frozen=True)
class GoalChange:
    kind: Literal["lifecycle", "completion"]   # 不含 "created" —— 与 kimi-code 对齐
    status: str | None = None
    reason: str | None = None
    actor: str | None = None
    stats: GoalChangeStats | None = None

@dataclass(frozen=True)
class GoalChangeStats:
    turns_used: int
    tokens_used: int
    wall_clock_ms: int

# 更新 StreamEvent 联合类型，加入 GoalUpdatedEvent
StreamEvent = (
    AssistantTextDelta | ReasoningDelta | AssistantTurnComplete
    | ToolExecutionStarted | ToolExecutionCompleted | ErrorEvent
    | StatusEvent | CompactProgressEvent | StreamFinished
    | GoalUpdatedEvent
)
```

#### `engine/turn_stages.py` —— post_tool_stage 读取 goal_stop_turn

```python
async def post_tool_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    ...  # 现有逻辑 ...

    # 新增：检查 goal 工具的停止信号
    for result in state.tool_results:
        meta = getattr(result, "result_metadata", None) or {}
        if isinstance(meta, dict) and meta.get("goal_stop_turn"):
            state.action = TurnAction.STOP
            return
```

> 注意 `tool_results` 是 `ToolResultBlock` 列表，其 `result_metadata`（`turn_stages.py:571,619,653` 透传自 `raw_result.metadata`）携带了 `goal_stop_turn`。检查位置在 `post_tool_stage`（结果 append 之后、下一轮之前）最干净。

#### `engine/query.py` —— goal_gate（可选，仿 done_gate）

若要强制 `UpdateGoal` 单独调用（避免与其它工具混用造成语义混乱），可仿 `done_gate_stage`（`turn_stages.py:544`）加一个 `goal_gate_stage`，把混用的 `UpdateGoal` 拒绝为 error。**第一版可不做**，先依赖 post_tool_stage 的 STOP 信号 + driver 的 status 检查。

### 2.3 工具注册 —— 始终注册

```python
# build_tool_registry() 无条件注册
registry.register(CreateGoalTool())
registry.register(UpdateGoalTool())
registry.register(GetGoalTool())
registry.register(SetGoalBudgetTool())
```

> 工具自行处理无 goal 情况（见设计 §10.2）。始终注册避免「工具时而可用时而不可用」导致的模型困惑。

### 2.4 测试

```
tests/test_goal_driver.py
  - test_drive_goal_single_turn_complete
  - test_drive_goal_multi_turn_iteration
  - test_drive_goal_budget_exhaustion
  - test_drive_goal_user_interrupt_pauses   # 捕获 CancelledError → pause_goal
  - test_drive_goal_continuation_prompt_injected
  - test_drive_goal_reminder_injection_first_turn
  - test_drive_goal_reminder_injection_continuation_combined
  - test_drive_goal_token_stats_from_assistant_turn_complete
  - test_goal_state_persists_across_turn_rollback  # 关键：验证 goal_state 不被 turn 取消清除
```

### 2.5 验证标准

- [ ] `uv run pytest -q tests/test_goal_driver.py` 全部通过
- [ ] 手动测试：`/goal` 创建目标 → 模型自主执行多轮 → 完成/受阻
- [ ] 预算限制生效：turn_budget=2 → 2 轮后自动 blocked
- [ ] UpdateGoalTool complete/blocked/paused 在 post_tool_stage 正确置 TurnAction.STOP
- [ ] **goal_state 跨 turn 取消不丢失**（用无 `_` 前缀 key + 不在 turn_checkpoint_keys）
- [ ] **goal_state 跨 session 持久化**（save_snapshot 存 tool_metadata，load 回填）

---

## Phase 3：Slash 命令与 TUI 集成

**目标**：`/goal` 命令完整可用，TUI 显示目标状态面板。

### 3.1 新增/修改文件

#### `commands/registry.py` —— 新增 /goal 命令

```python
GOAL_ALREADY_EXISTS_MSG = (
    "A goal is already active. Use `/goal replace <objective>` to replace it, "
    "or `/goal status` to inspect it."
)

async def _goal_handler(args: str, context: CommandContext) -> CommandResult:
    parsed = parse_goal_command(args)
    goal_mode = context.engine.tool_metadata.get("goal_mode")   # 经 tool_metadata 取

    if parsed["kind"] == "status":
        snapshot = goal_mode.get_goal() if goal_mode else None
        if snapshot is None:
            return CommandResult(message="No goal set. Start one with `/goal <objective>`.")
        return CommandResult(message=format_goal_status(snapshot))

    if parsed["kind"] == "pause":
        goal_mode.pause_goal()
        return CommandResult(message="Goal paused. Use `/goal resume` to continue.")

    if parsed["kind"] == "resume":
        # 边界检查
        snapshot = goal_mode.get_goal()
        if snapshot is None:
            return CommandResult(message="No goal to resume.")
        if snapshot.status not in ("paused", "blocked"):
            return CommandResult(message=f"Goal is {snapshot.status} and cannot be resumed.")

        # 权限检查（设计 §10.7.5）
        permission_mode = context.app_state.get().permission_mode if context.app_state else "default"
        if permission_mode == PermissionMode.PLAN.value:
            return CommandResult(message="Plan mode blocks all mutating tools. Switch to Auto or Default before resuming a goal.")
        if permission_mode == PermissionMode.DEFAULT.value:
            return CommandResult(message="", goal_action="permission_prompt_resume")

        goal_mode.resume_goal()
        return CommandResult(message="Resuming goal...", submit_prompt="Resume the active goal.")

    if parsed["kind"] == "cancel":
        goal_mode.cancel_goal()
        context.engine.inject_user_message(GOAL_CANCELLED_REMINDER)
        return CommandResult(message="Goal cancelled.")

    if parsed["kind"] == "create":
        snapshot = goal_mode.get_goal() if goal_mode else None
        if snapshot is not None and snapshot.status == "active" and not parsed["replace"]:
            return CommandResult(message=GOAL_ALREADY_EXISTS_MSG)

        # 权限检查（设计 §10.7）
        permission_mode = context.app_state.get().permission_mode if context.app_state else "default"
        if permission_mode == PermissionMode.PLAN.value:
            return CommandResult(message="Plan mode blocks all mutating tools. Switch to Auto or Default before starting a goal.")
        if permission_mode == PermissionMode.DEFAULT.value:
            return CommandResult(message="", goal_action="permission_prompt_create",
                                 goal_objective=parsed["objective"], goal_replace=parsed["replace"])

        goal_mode.create_goal(parsed["objective"], replace=parsed["replace"])
        return CommandResult(message=f"Goal set: {parsed['objective']}", submit_prompt=parsed["objective"])

registry.register(SlashCommand(
    "goal", "Set, manage, and track autonomous goals", _goal_handler,
    subcommands=["status", "pause", "resume", "cancel", "replace"],
))
```

> **`goal_mode` 取法**：经 `context.engine.tool_metadata.get("goal_mode")`（QueryEngine 有 `tool_metadata` property，`query_engine.py:210`），不假设 engine 有 `goal_mode` 属性。

#### `commands/core.py` —— CommandResult 扩展字段

```python
@dataclass
class CommandResult:
    message: str | None = None
    should_exit: bool = False
    clear_screen: bool = False
    replay_messages: list | None = None
    continue_pending: bool = False
    continue_turns: int | None = None
    refresh_runtime: bool = False
    submit_prompt: str | None = None
    submit_model: str | None = None
    # 新增
    goal_action: str | None = None          # "permission_prompt_create" | "permission_prompt_resume"
    goal_objective: str | None = None
    goal_replace: bool = False
```

#### `ui/runtime.py` —— 新增 goal_action 分支（前版遗漏）

`runtime.py:931-1000` 现有流程在 `submit_prompt is None and not continue_pending` 时只 `sync_app_state` 返回（line 993-998）。需加 `goal_action` 分支弹模态框：

```python
if result is not None:
    ...
    if result.goal_action is not None:
        # 弹 GoalStartPermissionPrompt 模态框（经 ModalHost）
        choice = await _show_goal_permission_prompt(bundle, result)
        if choice == "switch_auto":
            _switch_to_full_auto(context)
            if result.goal_action == "permission_prompt_create":
                goal_mode.create_goal(result.goal_objective, replace=result.goal_replace)
                # 触发 submit_prompt 进入 drive_goal
                ... 走 submit_prompt 流程 ...
            else:
                goal_mode.resume_goal()
                ... submit_prompt="Resume the active goal." ...
        elif choice == "keep_default":
            ... 不切权限，直接创建/恢复 ...
        # cancel → 不操作
        ...
```

> **`_switch_to_full_auto` 复用现有命令逻辑**（设计 §10.7.3），不手写 `PermissionChecker(mode=...)`：

```python
def _switch_to_full_auto(context: CommandContext) -> None:
    from openharness.commands.skills import build_permission_checker as _build_permission_checker
    settings = load_settings()
    settings.permission.mode = PermissionMode.FULL_AUTO
    save_settings(settings)
    context.engine.set_permission_checker(_build_permission_checker(settings, context))
    _sync_full_auto_tools(context, is_full_auto=True)
    if context.app_state is not None:
        context.app_state.set(permission_mode=PermissionMode.FULL_AUTO.value)
```

### 3.2 前端组件（frontend/terminal/src/components/）

> **路径修正**：前端在 `frontend/terminal/src/components/`（**不是** `_frontend/src/components/`）。参照现有 `StatusBar.tsx`、`TodoPanel.tsx`、`ModalHost.tsx`、`SelectModal.tsx`。

#### `GoalPanel.tsx`（参照 TodoPanel.tsx）

```tsx
// 渲染目标状态面板
// - 目标名称 + 状态标签 (active/paused/blocked)
// - 进度：turns/tokens/time vs budget
// - 操作按钮：pause / resume / cancel
```

#### `GoalStartPermissionPrompt.tsx`（参照 SelectModal.tsx 的模态模式）

```tsx
interface Props {
    action: "permission_prompt_create" | "permission_prompt_resume";
    objective?: string;
    replace?: boolean;
    onSelect: (choice: "switch_auto" | "keep_default" | "cancel") => void;
}
// 3 选项：Switch to Auto and {start|resume}（推荐）/ Keep Default（带警告）/ Cancel
```

#### `StatusBar.tsx` 修改

显示当前 goal 简要状态（active 时显示 turns 进度）。

#### 协议层 `ui/protocol.py` + 前端事件类型

`GoalUpdatedEvent` 需在前后端协议层注册，前端才能接收渲染。

#### 事件处理

| `change.kind` | `change.status` | 行为 |
|---|---|---|
| `lifecycle` | `active` (含 created) | 显示面板 |
| `lifecycle` | `paused` | 状态标签 → paused |
| `lifecycle` | `blocked` | 状态标签 → blocked |
| `completion` | `complete` | 显示完成摘要 → 淡出面板 |
| `snapshot=None` | — | 隐藏面板 |

### 3.3 测试

```
tests/test_goal_permission.py
  - test_goal_create_full_auto_no_prompt
  - test_goal_create_default_returns_goal_action
  - test_goal_create_plan_rejected
  - test_goal_resume_full_auto_no_prompt
  - test_goal_resume_default_returns_goal_action
  - test_goal_resume_plan_rejected
  - test_switch_to_full_auto_uses_build_permission_checker  # 验证不手写 PermissionChecker(mode=)
  - test_permission_choice_switch_auto
  - test_permission_choice_keep_default
  - test_permission_choice_cancel
```

### 3.4 验证标准

- [ ] `/goal Ship feature X` → 模型开始自主执行
- [ ] `/goal pause` → 状态变 paused
- [ ] `/goal resume` → 恢复（边界检查：无 goal / 不可恢复状态时报错）
- [ ] `/goal cancel` → 清除 + 注入 cancelled reminder
- [ ] `/goal status` → 显示 turns/tokens/time
- [ ] `/goal` 在已有 active goal 时提示用 `/goal replace`
- [ ] GoalPanel 在 TUI 正确渲染
- [ ] StatusBar 显示 goal 进度
- [ ] `FULL_AUTO` 直接创建/恢复，无对话框
- [ ] `DEFAULT` 弹 `GoalStartPermissionPrompt`
- [ ] 选"切换 Auto"→ 权限变 FULL_AUTO，goal 启动
- [ ] 选"保持 Default"→ goal 在 DEFAULT 启动（工具需确认）
- [ ] 选"取消"→ 不创建
- [ ] `PLAN` 直接拒绝
- [ ] Goal 完成后权限不自动恢复

---

## Phase 4：健壮性与边界情况

### 4.1 任务清单

| 任务 | 优先级 | 说明 |
|------|--------|------|
| Session 恢复降级 | P0 | `normalize_after_replay` 在 `/resume` 时 active → paused |
| Context overflow 处理 | P0 | goal turn 中 overflow 不改变 goal 状态，compact 后继续 |
| 中断检测 (CancelledError) | P0 | `_drive_goal` 捕获 Ctrl+C → `pause_goal(actor="runtime")` |
| 并发安全 | P1 | goal 执行中用户输入新消息 → steer buffer 或拒绝 |
| Max turns 交互 | P1 | goal 的 turn 计数独立于 engine 的 max_turns |
| Hook 集成 | P1 | GoalCreated/GoalCompleted/GoalBlocked hook events |
| 错误分类 | P2 | API 错误按类型生成不同 pause reason |
| Cancel reminder 注入 | P0 | `/goal cancel` 后注入 GOAL_CANCELLED_REMINDER |
| goal_stop_turn 在 post_tool_stage 生效 | P0 | 验证 UpdateGoal 后 tool loop 正确终止 |

### 4.2 验证标准

- [ ] 进程重启后 `/goal resume` 恢复正常
- [ ] Context overflow 后 goal 继续执行
- [ ] Hook 能阻止 goal continuation
- [ ] Ctrl+C 中断后 goal 变 paused（goal_state 未丢失）
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
frontend/terminal/src/components/GoalPanel.tsx
frontend/terminal/src/components/GoalStartPermissionPrompt.tsx
```

### 修改文件

```
src/openharness/engine/types.py             # ⚠️ 新增 GOAL_STATE 枚举 + 加入 all_persisted_keys（前版遗漏）
src/openharness/services/session_backend.py # ⚠️ save_snapshot/load 增加 tool_metadata 存取（前版遗漏）
src/openharness/services/session_storage.py # ⚠️ 配合 tool_metadata 持久化（前版遗漏）
src/openharness/engine/query_engine.py      # submit_message 分支路由 _drive_goal + token 统计
src/openharness/engine/stream_events.py     # GoalUpdatedEvent/GoalChange/GoalChangeStats + 更新 StreamEvent 联合
src/openharness/engine/turn_stages.py       # post_tool_stage 检查 goal_stop_turn → TurnAction.STOP
src/openharness/commands/core.py            # CommandResult 新增 goal_action/goal_objective/goal_replace
src/openharness/commands/registry.py        # /goal 命令 + 边界检查 + 权限检查
src/openharness/cli.py                      # 初始化 GoalMode 注入 tool_metadata["goal_mode"]
src/openharness/ui/runtime.py               # ⚠️ goal_action 分支 + _switch_to_full_auto（前版遗漏）
src/openharness/ui/protocol.py              # ⚠️ 注册 GoalUpdatedEvent 协议（前版遗漏）
frontend/terminal/src/components/StatusBar.tsx  # 显示 goal 状态
frontend/terminal/src/App.tsx (或同等入口)   # 处理 GoalUpdatedEvent + goal_action 路由
```

> **⚠️ 标记的 5 项是前版计划遗漏或写错的**，必须包含。

> **明确 NOT 修改**：
> - `tools/base.py` 的 `ToolResult` —— **不加** stop_turn/stop_batch 字段（用 metadata 传递，见 Phase 1.4）
> - `engine/query.py` 的 tool loop —— 停止逻辑落在 `turn_stages.py`，不重构并行执行

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
| 模型不遵守 UpdateGoal 协议 | Continuation prompt 强化指导；driver 硬预算兜底；UpdateGoal 的 goal_stop_turn 在 post_tool_stage 强制 STOP |
| Goal turn 中 context overflow | 复用现有 auto-compact 机制，不改变 goal 状态 |
| 无限循环 | 默认 turn_budget 硬上限（或 engine max_turns）；连续 silent stop 检测（复用 `_stream_query_with_guards` 现有逻辑） |
| 用户中断后状态不一致 | 捕获 CancelledError → `pause_goal(actor="runtime")`；goal_state 用无 `_` key 不被 turn 回滚 |
| TUI 渲染阻塞 | GoalPanel 独立组件，不阻塞主对话流 |
| 工具始终注册导致误用 | CreateGoalTool description 限制场景；无 goal 时其它工具返回错误 |
| Reminder 注入合并问题 | reminder + continuation prompt 合为一条字符串注入（inject_user_message 会合并连续 user msg） |
| completion 摘要的 provider prefill 限制 | 摘要作为 user message 注入在 tool result 之后，确保 provider 请求以 user message 结尾（kimi-code 用 system-reminder 正是为规避此点；OpenHarness 需验证） |
| 权限切换后用户困惑 | 对话框明确告知切换行为；goal 完成后不自动恢复；可 `/permissions default` 手动切回 |
