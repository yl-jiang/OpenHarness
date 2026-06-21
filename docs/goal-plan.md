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

> **路径修正**：前端在 `frontend/terminal/src/components/`（**不是** `_frontend/src/components/`）。参照现有 `StatusBar.tsx`、`TodoPanel.tsx`、`ModalHost.tsx`、`SelectModal.tsx`。设计详见 `goal-design.md` §10.5。

#### 3.2.1 协议层：`ui/protocol.py`

把 `GoalUpdatedEvent` 注册到现有 StreamEvent 序列化表：

```python
# src/openharness/ui/protocol.py
from openharness.engine.stream_events import GoalUpdatedEvent

def _serialize_goal_event(event: GoalUpdatedEvent) -> dict:
    return {
        "type": "goal_updated",
        "snapshot": event.snapshot.to_dict() if event.snapshot else None,
        "change": {
            "kind": event.change.kind,
            "status": event.change.status,
            "reason": event.change.reason,
            "actor": event.change.actor,
            "stats": event.change.stats.__dict__ if event.change.stats else None,
        } if event.change else None,
    }
# 注册到现有的事件序列化表
```

前端在 `frontend/terminal/src/types/events.ts` 声明对应 TS 类型：

```ts
export interface GoalUpdatedEvent {
  type: "goal_updated";
  snapshot: GoalSnapshot | null;
  change: GoalChange | null;
}
```

#### 3.2.2 `GoalPanel.tsx`（参照 TodoPanel.tsx）

```tsx
// frontend/terminal/src/components/GoalPanel.tsx
import { Box, Text } from "ink";

interface Props { snapshot: GoalSnapshot | null; }

export function GoalPanel({ snapshot }: Props) {
  if (!snapshot) return null;
  const statusColor = { active: "green", paused: "yellow", blocked: "red" }[snapshot.status] ?? "white";
  return (
    <Box flexDirection="column" borderStyle="round" paddingX={1}>
      <Box><Text bold>Goal: </Text><Text color={statusColor}>[{snapshot.status}]</Text></Box>
      <Text dimColor>{snapshot.objective}</Text>
      {snapshot.completion_criterion && <Text dimColor>Criterion: {snapshot.completion_criterion}</Text>}
      <BudgetBar label="turns" used={snapshot.turns_used} total={snapshot.budget.turn_budget} />
      <BudgetBar label="tokens" used={snapshot.tokens_used} total={snapshot.budget.token_budget} />
      <BudgetBar label="time" used={snapshot.wall_clock_ms} total={snapshot.budget.wall_clock_budget_ms} unit="ms" />
    </Box>
  );
}
```

`App.tsx`（或 `ConversationView.tsx`）订阅 `goal_updated` 事件，把最新 snapshot 存到 state 并渲染 `GoalPanel`；`snapshot === null` 时组件返回 `null`（面板自动隐藏）。

#### 3.2.3 `GoalStartPermissionPrompt.tsx`（参照 SelectModal.tsx 的模态模式）

```tsx
// frontend/terminal/src/components/GoalStartPermissionPrompt.tsx
import { SelectModal } from "./SelectModal";

interface Props {
  action: "permission_prompt_create" | "permission_prompt_resume";
  objective?: string;
  onSelect: (choice: "switch_auto" | "keep_default" | "cancel") => void;
}

export function GoalStartPermissionPrompt({ action, objective, onSelect }: Props) {
  const verb = action === "permission_prompt_create" ? "start" : "resume";
  const options = [
    { label: `Switch to Auto and ${verb} (recommended)`, value: "switch_auto" },
    { label: `Keep Default and ${verb} (tools will confirm)`, value: "keep_default" },
    { label: "Cancel", value: "cancel" },
  ];
  return (
    <>
      {objective && <Text dimColor>Objective: {objective}</Text>}
      <SelectModal title="Goal permission" options={options} onSelect={onSelect} />
    </>
  );
}
```

由 `ModalHost.tsx` 承载。TUI 启动时把 `onSelect` 包成异步函数注入 `bundle.goal_action_handler`。

#### 3.2.4 `StatusBar.tsx` 修改

在 StatusBar 右侧追加一个 chip：

- active: 显示 `Goal: 3/20 turns` 或 `Goal: 45.2k/500k tokens`（按 budget 设置选择）
- paused/blocked: 显示对应状态标签 + 短 reason
- 无 goal: 不渲染

#### 3.2.5 runtime 侧可插拔回调（重构现有非交互默认路径）

把 `runtime.py` 现有的「权限自动升级」逻辑提取为 `_apply_full_auto_upgrade(bundle, context, result)`，并引入可替换的 `bundle.goal_action_handler`：

```python
# runtime.py
goal_action_handler = bundle.goal_action_handler  # TUI 注入
if result.goal_action is not None and result.submit_prompt is None:
    if goal_action_handler is not None:
        choice = await goal_action_handler(result)
        if choice == "cancel":
            sync_app_state(bundle)
            return True
        if choice == "keep_default":
            _apply_goal_action_keep_default(bundle, context, result)
            return True
        # switch_auto: fall through
    _apply_full_auto_upgrade(bundle, context, result)
```

headless 模式（`ohmo`、`backend_host`）不注入 handler，自动走「切 Auto」默认路径 —— 现有非交互行为完全兼容。

#### 3.2.6 事件分发总览

| 事件源 | 前端消费者 | 行为 |
|---|---|---|
| `GoalUpdatedEvent(snapshot=..., change.kind=lifecycle)` | GoalPanel + StatusBar | 更新面板与状态 chip |
| `GoalUpdatedEvent(snapshot=..., change.kind=completion)` | GoalPanel | 显示完成摘要，3s 后淡出 |
| `GoalUpdatedEvent(snapshot=null)` | GoalPanel + StatusBar | 隐藏面板与 chip |
| `CommandResult.goal_action` | ModalHost | 弹 `GoalStartPermissionPrompt` |

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
| 错误分类 | P2 | API 错误按类型生成不同 pause reason |
| Cancel reminder 注入 | P0 | `/goal cancel` 后注入 GOAL_CANCELLED_REMINDER |
| goal_stop_turn 在 post_tool_stage 生效 | P0 | 验证 UpdateGoal 后 tool loop 正确终止 |

### 4.2 验证标准

- [ ] 进程重启后 `/goal resume` 恢复正常
- [ ] Context overflow 后 goal 继续执行
- [ ] Ctrl+C 中断后 goal 变 paused（goal_state 未丢失）
- [ ] `/goal cancel` 后模型不再响应已取消 goal 的 reminders

---

## Phase 5：Hook Events（生命周期通知）

**目标**：goal 的每次状态变更都能触发外部 hook 脚本，提供可观测性/自动化基础（设计详见 `goal-design.md` §10.6）。

### 5.1 新增/修改文件

```
src/openharness/hooks/__init__.py      # HookEvent 枚举加 6 个新事件
src/openharness/goal/state.py          # GoalMode 接受 hook_executor + _pending_hooks 队列
src/openharness/ui/runtime.py          # 创建 GoalMode 时传入 engine._hook_executor
src/openharness/engine/query_engine.py # _drive_goal 每轮结束 flush_hooks
tests/test_goal_hooks.py               # 新增
```

### 5.2 实现内容

#### `hooks/__init__.py`

```python
class HookEvent(str, Enum):
    ...
    GOAL_CREATED    = "goal_created"
    GOAL_RESUMED    = "goal_resumed"
    GOAL_PAUSED     = "goal_paused"
    GOAL_BLOCKED    = "goal_blocked"
    GOAL_COMPLETED  = "goal_completed"
    GOAL_CANCELLED  = "goal_cancelled"
```

#### `goal/state.py` —— GoalMode 接收 hook_executor

```python
class GoalMode:
    def __init__(self, tool_metadata, *, hook_executor=None):
        self._metadata = tool_metadata
        self._hook_executor = hook_executor
        self._pending_hooks: list[tuple[HookEvent, dict]] = []
        ...

    def create_goal(self, ...):
        ...
        self._pending_hooks.append((
            HookEvent.GOAL_CREATED,
            {"event": HookEvent.GOAL_CREATED.value, "goal": snapshot.to_dict()},
        ))
        return snapshot

    # pause_goal / resume_goal / mark_blocked / mark_complete / cancel_goal 同理入队

    async def flush_hooks(self) -> None:
        if self._hook_executor is None:
            self._pending_hooks.clear()
            return
        pending = self._pending_hooks
        self._pending_hooks = []
        for event, payload in pending:
            await self._hook_executor.execute(event, payload)
```

> **设计权衡**：状态变更方法保持同步（避免给所有调用方引入 await），hook 触发延迟到 driver 每轮结束。这意味着快速的状态连锁变更（create→pause）会合并到一次 flush，但不会丢失。

#### `runtime.py` 注入

```python
goal_mode = GoalMode(engine.tool_metadata, hook_executor=engine._hook_executor)
engine.tool_metadata[GOAL_MODE_KEY] = goal_mode
```

#### `_drive_goal` 每轮 flush

```python
async for event in self._stream_query_with_guards(...):
    yield event
await goal_mode.flush_hooks()
```

#### Payload schema

所有 hook 统一：`{"event": "goal_<x>", "goal": <snapshot_dict>, "reason": "...", "actor": "..."}`。Hook 脚本通过环境变量 `GOAL_OBJECTIVE` / `GOAL_REASON` / `GOAL_ID` / `GOAL_STATUS` 等访问 payload 字段（与现有 hook 机制一致）。

### 5.3 测试

```
tests/test_goal_hooks.py
  - test_create_goal_enqueues_created_hook
  - test_mark_complete_enqueues_completed_hook
  - test_mark_blocked_carries_reason_in_payload
  - test_cancel_goal_enqueues_cancelled_hook
  - test_flush_hooks_clears_queue
  - test_flush_noop_when_no_executor
  - test_driver_flushes_after_each_turn  # 用 fake hook_executor 计数
  - test_hook_receives_snapshot_dict_payload
  - test_multiple_state_changes_flush_in_order
```

### 5.4 验证标准

- [ ] `.openharness/hooks.yaml` 配置 `goal_completed` hook → 完成后文件追加日志
- [ ] `goal_blocked` hook 能触发外部通知（notify-send / Slack）
- [ ] 没有 hook 配置时 GoalMode 行为完全不变
- [ ] Hook 脚本失败不会阻断 driver（`hook_executor` 已隔离异常）
- [ ] 连续多个状态变更（create→pause→resume→complete）按顺序触发对应事件

---

## Phase 6：Goal Queue（多目标队列）

**目标**：支持多目标排队执行，对齐 kimi-code 的 `GoalQueueStore + /goal next`（设计详见 `goal-design.md` §14）。

### 6.1 新增/修改文件

```
src/openharness/goal/queue.py              # QueuedGoal + GoalQueueStore
src/openharness/goal/state.py              # GoalMode.start_next_from_queue()
src/openharness/tools/queue_goal_tool.py   # QueueGoalTool (模型用)
src/openharness/tools/__init__.py          # 注册 QueueGoalTool
src/openharness/engine/types.py            # ToolMetadataKey.GOAL_QUEUE
src/openharness/commands/registry.py       # /goal queue * 子命令 + /goal next/skip
src/openharness/commands/goal.py           # parse_goal_command 支持 queue 子命令
src/openharness/engine/query_engine.py     # _drive_goal 末尾检查队列并启动下一个
tests/test_goal_queue.py                   # 新增
tests/test_goal_queue_driver.py            # 新增
```

### 6.2 实现内容

#### `goal/queue.py`

```python
@dataclass
class QueuedGoal:
    queue_id: str
    objective: str
    completion_criterion: str | None = None
    budget_limits: GoalBudgetLimits = field(default_factory=GoalBudgetLimits)
    priority: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

class GoalQueueStore:
    QUEUE_KEY = "goal_queue"

    def __init__(self, tool_metadata: dict):
        self._metadata = tool_metadata
        self._items: list[QueuedGoal] = self._restore()

    def enqueue(self, objective, *, priority=0, ...) -> QueuedGoal: ...
    def pop(self) -> QueuedGoal | None: ...
    def remove(self, queue_id: str) -> bool: ...
    def reorder(self, queue_ids: list[str]) -> None: ...
    def clear(self) -> None: ...
    def peek(self) -> QueuedGoal | None: ...
    def list(self) -> list[QueuedGoal]: ...
    def __len__(self) -> int: ...

    # 借鉴 kimi-code `restoreGoalQueueItem`：启动失败时把队列项放回头部。
    # 幂等：queue_id 已存在则跳过，避免重复恢复。
    def restore_to_head(self, goal: QueuedGoal) -> None:
        if any(g.queue_id == goal.queue_id for g in self._items):
            return
        self._items.insert(0, goal)
        self._persist()

    def _persist(self) -> None:
        self._metadata[self.QUEUE_KEY] = [asdict(q) for q in self._items]
    def _restore(self) -> list[QueuedGoal]: ...
```

#### `engine/types.py` —— 新增 `GOAL_QUEUE`

```python
class ToolMetadataKey(str, Enum):
    ...
    GOAL_QUEUE = "goal_queue"

    @classmethod
    def all_persisted_keys(cls):
        return (..., cls.GOAL_STATE, cls.GOAL_QUEUE)
```

#### `tools/queue_goal_tool.py`

```python
class QueueGoalTool(BaseTool):
    name = "queue_goal"
    description = (
        "Enqueue a follow-up goal to start after the current goal finishes. "
        "Use when you identify a natural next step but the current goal is "
        "not yet done. Does not interrupt the current goal."
    )

    class InputModel(BaseModel):
        objective: str
        completion_criterion: str | None = None
        priority: int = 0

    async def execute(self, arguments, context):
        queue = context.metadata.get(GOAL_QUEUE_KEY)
        if queue is None:
            return ToolResult(is_error=True, output="Goal queue not available.")
        queued = queue.enqueue(arguments.objective, ...)
        return ToolResult(output=json.dumps({"queued": asdict(queued), "queue_length": len(queue)}))
```

#### `goal/state.py` —— GoalMode.start_next_from_queue

借鉴 kimi-code 的 `restoreGoalQueueItem`：先 `pop()`、`create_goal` 抛异常时把队列项放回 `_items[0]`，保证队列项不因启动失败而丢失。

```python
def start_next_from_queue(self, queue: GoalQueueStore) -> GoalSnapshot | None:
    """从队列 pop 一个并 create_goal。启动失败时回滚到头部（不丢失）。

    上下文策略（详见 goal-design.md §14.7）：
    新 goal 共享当前 agent 的完整 conversation history / tool_metadata /
    cost tracker，与 kimi-code 一致；不做任何上下文重置。
    """
    if self._state is not None:
        return None
    queued = queue.pop()
    if queued is None:
        return None
    try:
        return self.create_goal(
            queued.objective,
            completion_criterion=queued.completion_criterion,
            actor="runtime",
        )
    except Exception:
        # create_goal 抛异常（例如 objective 太长 / 已有 active goal）→
        # 把队列项放回头部，下次重试仍是同一个。
        queue.restore_to_head(queued)
        return None
```

#### `_drive_goal` 末尾检查队列

```python
if goal_mode.get_goal() is None:
    queue = self._tool_metadata.get(GOAL_QUEUE_KEY)
    if isinstance(queue, GoalQueueStore):
        next_snap = goal_mode.start_next_from_queue(queue)
        if next_snap is not None:
            is_first_turn = True
            continue  # 继续外层循环，驱动下一个目标
```

#### `/goal queue` 子命令族

```
/goal queue                       # 列出队列
/goal queue add Ship feature Y
/goal queue add --priority 10 ...
/goal queue remove <queue_id>
/goal queue clear
/goal queue reorder <id1> <id2> ...
/goal next                        # 取消当前 active，pop 并启动
/goal skip                        # 取消当前 active，pop 并丢弃
```

`parse_goal_command` 增加 `queue` 二级保留词处理。

#### kimi-code 对齐说明（参考 `~/Github/kimi-code` 源码）

| kimi-code 机制 | OpenHarness 实现 |
|---|---|
| `upcoming-goals.json` 独立文件存储 | 不引入独立文件，保持 `tool_metadata["goal_queue"]`（与 goal_state 同寿） |
| `UpcomingGoal` 仅含 `{id, objective, createdAt, updatedAt}` | `QueuedGoal` 可带 criterion/budget（显式指定时尊重），默认仅 objective |
| `restoreGoalQueueItem` 失败回滚到头部 | `GoalQueueStore.restore_to_head`（幂等） |
| `queueMutationLocks` per-file mutex | 不需要（Python 单进程，tool_metadata 是 dict） |
| `promoteNextQueuedGoal` 走标准 `/goal` 命令 | `_drive_goal` 末尾 `start_next_from_queue` 走标准 `create_goal`（等价语义） |
| `notifyQueuedGoalWaitingOnBlocked` blocked 不自动跳 | `_drive_goal` 仅在 `status == complete` 时检查队列（默认） |
| 队列任务共享同一 agent 上下文（无隔离） | 同样不做隔离（详见 `goal-design.md` §14.7） |

### 6.3 测试

```
tests/test_goal_queue.py
  - test_enqueue_and_list
  - test_pop_returns_highest_priority
  - test_remove_by_id
  - test_reorder
  - test_clear
  - test_persist_and_restore
  - test_enqueue_length_limit                    # 队列上限（如 50）
  - test_enqueue_empty_objective_rejected
  - test_restore_to_head_inserts_at_index_0
  - test_restore_to_head_is_idempotent           # 重复 restore 不重复插入
  - test_restore_to_head_preserves_existing_order  # 已有同 id 项时跳过
  - test_start_next_from_queue_creates_goal
  - test_start_next_from_queue_skips_when_active
  - test_start_next_from_queue_restores_on_failure   # create_goal 抛异常 → 队列项回滚到头部
  - test_start_next_from_queue_returns_none_on_empty

tests/test_goal_queue_driver.py
  - test_driver_auto_starts_next_after_complete
  - test_driver_does_not_advance_after_blocked   # 默认行为
  - test_driver_does_not_advance_after_cancel
  - test_goal_next_command_skips_current
  - test_queue_goal_tool_enqueues_without_interrupting
  - test_queued_goal_sees_previous_completion_summary   # 上下文共享：新 goal 的 messages 含上一 goal 的摘要
  - test_queued_goal_shares_tool_metadata                # read_file_state 等跨任务保留
  - test_queued_goal_cumulative_token_usage              # cost tracker 跨任务累加
  - test_queue_rollback_on_objective_too_long            # 队列项 objective 超限 → 回滚到头部，不丢
```

### 6.4 验证标准

- [ ] `/goal queue add X; /goal queue add Y` → 列出两项
- [ ] 当前 goal `complete` → driver 自动启动队列中下一个
- [ ] 当前 goal `blocked` → driver **不**自动启动下一个（默认）
- [ ] 当前 goal `cancel` → driver **不**自动启动下一个
- [ ] `/goal next` 取消当前 active 并启动队列头
- [ ] 模型调用 `queue_goal` 工具 → 入队成功，当前 turn 继续
- [ ] Session 重启 → 队列原样恢复
- [ ] 队列空时 driver 正常退出
- [ ] **启动失败回滚**：队列中某项 objective 过长 / 格式错误，启动抛异常 → 该项被恢复到队列头部，下次重试仍是同一个（借鉴 kimi-code `restoreGoalQueueItem`）
- [ ] **上下文共享**：队列中 Goal B 启动时，能看到 Goal A 的 completion summary（messages 中）和 read_file_state（tool_metadata 中）；cost tracker 跨任务累加
- [ ] **blocked 通知**：当前 goal blocked 且队列非空时，提示用户"下一个队列任务将在此 goal 完成后启动"（对标 kimi-code `notifyQueuedGoalWaitingOnBlocked`）

---

## Phase 7：Goal Settings 与权限自动恢复

**目标**：引入 `GoalSettings` 配置块（全局默认预算、driver 上限等），并提供可选的「goal 结束后自动恢复权限模式」（设计详见 `goal-design.md` §15）。

### 7.1 新增/修改文件

```
src/openharness/config/settings.py        # 新增 GoalSettings
src/openharness/goal/state.py              # GoalState.original_permission_mode
src/openharness/commands/registry.py       # 传递 original_permission_mode；新增 /permissions restore
src/openharness/engine/query_engine.py     # _drive_goal 末尾 _maybe_restore_permission
src/openharness/ui/runtime.py              # 处理 _pending_permission_restore
tests/test_goal_settings.py                # 新增
tests/test_goal_permission_restore.py      # 新增
```

### 7.2 实现内容

#### `config/settings.py`

```python
class GoalSettings(BaseModel):
    enabled: bool = True
    max_objective_length: int = 4000
    default_turn_budget: int | None = None
    default_token_budget: int | None = None
    default_wall_clock_budget_s: int | None = None
    auto_advance_on_blocked: bool = False       # 见 §14.6
    restore_permission_after_goal: bool = False # 见 §15.2
    hard_cap_iterations: int = 200
    max_queue_length: int = 50

class Settings(BaseModel):
    ...
    goal: GoalSettings = field(default_factory=GoalSettings)
```

`~/.openharness/settings.yaml` 支持：

```yaml
goal:
  default_turn_budget: 50
  restore_permission_after_goal: true
```

#### `goal/state.py` —— 记录原始权限

```python
@dataclass
class GoalState:
    ...
    original_permission_mode: str | None = None

class GoalMode:
    def create_goal(self, ..., *, original_permission_mode: str | None = None):
        ...
        self._state.original_permission_mode = original_permission_mode

    def original_permission_mode(self) -> str | None:
        return self._state.original_permission_mode if self._state else None
```

`/goal` handler 在 create/resume 时传入：

```python
original = _current_permission_mode(context)
goal_mode.create_goal(objective, ..., original_permission_mode=original)
```

#### `_drive_goal` 末尾判断恢复

```python
def _maybe_restore_permission(self):
    settings = self._settings
    if settings is None or not settings.goal.restore_permission_after_goal:
        return
    goal_mode = self._tool_metadata.get(GOAL_MODE_KEY)
    if not isinstance(goal_mode, GoalMode):
        return
    original = goal_mode.original_permission_mode()
    if original and original != PermissionMode.FULL_AUTO.value:
        self._tool_metadata["_pending_permission_restore"] = original
```

> `_pending_permission_restore` 用 `_` 前缀 → 自动进入 `_turn_private_metadata_keys`，turn 取消时回滚；不持久化。

#### `runtime.py` 在 submit_message 结束后处理

```python
try:
    async for event in bundle.engine.submit_message(submit_prompt):
        await render_event(event)
finally:
    pending = bundle.engine.tool_metadata.pop("_pending_permission_restore", None)
    if pending is not None:
        _restore_permission_mode(context, bundle, pending)
        await print_system(f"Restored permission mode to {pending}.")
```

`_restore_permission_mode` 复用 `/permissions <mode>` 完整路径（`build_permission_checker` + `_sync_full_auto_tools` + `app_state.set`）。

#### `/permissions restore` 子命令

```python
async def _permissions_restore_handler(args, context):
    goal_mode = context.engine.tool_metadata.get(GOAL_MODE_KEY)
    original = goal_mode.original_permission_mode() if goal_mode else None
    if original is None:
        return CommandResult(message="No goal-driven permission change to restore.")
    _restore_permission_mode(context, bundle, original)
    return CommandResult(message=f"Restored to {original}.")
```

注册：`registry.register(SlashCommand("permissions", ..., subcommands=[..., "restore"]))`。

#### 默认预算应用

`/goal` 创建时若用户未指定 budget，handler 根据 `settings.goal.default_turn_budget` 等自动调用 `set_budget_limits`：

```python
goal_mode.create_goal(objective, ..., actor="user")
defaults = GoalBudgetLimits(
    turn_budget=settings.goal.default_turn_budget,
    token_budget=settings.goal.default_token_budget,
    wall_clock_budget_ms=settings.goal.default_wall_clock_budget_s * 1000
        if settings.goal.default_wall_clock_budget_s else None,
)
if any(v is not None for v in (defaults.turn_budget, defaults.token_budget, defaults.wall_clock_budget_ms)):
    goal_mode.set_budget_limits(defaults)
```

### 7.3 测试

```
tests/test_goal_settings.py
  - test_default_settings_values
  - test_settings_yaml_load
  - test_default_budget_applied_on_create
  - test_user_explicit_budget_overrides_default
  - test_hard_cap_iterations_from_settings

tests/test_goal_permission_restore.py
  - test_restore_disabled_by_default
  - test_restore_enabled_returns_to_original
  - test_restore_skipped_when_original_was_full_auto
  - test_restore_via_pending_metadata_signal
  - test_restore_command_without_prior_change_errors
  - test_pending_restore_cleared_on_turn_cancel  # _前缀 key 的回滚
  - test_restore_survives_session_restart       # original_permission_mode 跨 session
```

### 7.4 验证标准

- [ ] 默认配置下 goal 完成后权限**不**恢复（向后兼容）
- [ ] 配置 `restore_permission_after_goal: true` 后：DEFAULT → /goal → FULL_AUTO → 完成 → 自动回 DEFAULT
- [ ] `/permissions restore` 手动恢复
- [ ] 原始权限为 FULL_AUTO 时不触发恢复（避免无意义切换）
- [ ] 全局默认 turn_budget 生效；用户显式指定时覆盖默认
- [ ] Turn 取消时 `_pending_permission_restore` 被回滚（不恢复）
- [ ] Session 重启后仍能恢复到进入前权限（original_permission_mode 持久化在 goal_state）

---

## 文件变更清单


### 新增文件

```
# Phase 1：核心状态机 + 工具
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

# Phase 2：QueryEngine 集成
tests/test_goal_driver.py
tests/test_goal_turn_stages.py

# Phase 3：Slash 命令 + TUI
src/openharness/commands/goal.py              # parse_goal_command + format_goal_status
tests/test_goal_command.py
tests/test_goal_permission.py
frontend/terminal/src/components/GoalPanel.tsx
frontend/terminal/src/components/GoalStartPermissionPrompt.tsx

# Phase 5：Hook Events
tests/test_goal_hooks.py

# Phase 6：Goal Queue
src/openharness/goal/queue.py
src/openharness/tools/queue_goal_tool.py
tests/test_goal_queue.py
tests/test_goal_queue_driver.py

# Phase 7：Goal Settings + 权限恢复
tests/test_goal_settings.py
tests/test_goal_permission_restore.py
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

| Phase | 预估工时 | 依赖 | 状态 |
|-------|---------|------|------|
| Phase 1: 状态机 + 工具 + budget | 3-4 天 | 无 | ✅ 已完成（80 测试通过） |
| Phase 2: QueryEngine 集成 | 2-3 天 | Phase 1 | ✅ 已完成 |
| Phase 3: Slash 命令 + TUI（基础部分） | 1-2 天 | Phase 2 | ⚠️ 命令/runtime 已完成；TUI 组件待实现 |
| Phase 4: 健壮性 | 1-2 天 | Phase 2 | ⚠️ 核心项已完成（cancel/interrupt/session 恢复）；剩余并发/overflow |
| **Phase 5: Hook Events** | **1 天** | **Phase 2** | 待实施 |
| **Phase 6: Goal Queue** | **2-3 天** | **Phase 5** | 待实施 |
| **Phase 7: Goal Settings + 权限恢复** | **1-2 天** | **Phase 5** | 待实施 |
| **总计（剩余）** | **4-6 天** | | |

> **推荐实施顺序**：Phase 5 → Phase 7 → Phase 6 → Phase 3 TUI 组件。Hook Events 先做能让后续功能自动获得可观测性；权限恢复实现简单且解决真实痛点；Goal Queue 工作量最大但价值中等；TUI 组件需要 React/Ink 经验且已有 runtime 兜底，可放最后。

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
| 权限切换后用户困惑 | 对话框明确告知切换行为；goal 完成后不自动恢复（除非配置启用）；可 `/permissions default` 或 `/permissions restore` 手动切回 |
| **Hook 脚本失败阻断 driver**（Phase 5） | `hook_executor` 已隔离异常；flush_hooks 内 try/except 兜底；hook 失败降级为日志而非 raise |
| **Hook 顺序与合并**（Phase 5） | `_pending_hooks` FIFO；driver 每轮 flush 一次；快速连锁（create→pause）合并到一次 flush 但顺序保持 |
| **队列无限增长**（Phase 6） | `max_queue_length`（默认 50）入队时校验；超限返回错误 |
| **driver 在队列中死循环**（Phase 6） | `hard_cap_iterations` 作用于**所有目标累计**，不是单目标；配置可调 |
| **blocked 后自动跳到下一个**（Phase 6） | 默认 `auto_advance_on_blocked: false`；显式开启才跳，避免掩盖需要人工介入的问题 |
| **权限恢复在 turn 取消时错误触发**（Phase 7） | `_pending_permission_restore` 用 `_` 前缀 → 自动进入 `_turn_private_metadata_keys`，turn 取消时回滚 |
| **原始权限记录跨 session 损坏**（Phase 7） | `_restore_from_metadata` 校验 `original_permission_mode` 取值必须为 `DEFAULT/PLAN/FULL_AUTO`；非法值降级为不恢复 |
| **TUI 模态框与 headless 模式不兼容**（Phase 3.2.5） | runtime 侧用可插拔回调；headless 模式不注入 handler，自动走「切 Auto」默认路径 |
| **Goal Queue 与现有单 goal UI 冲突**（Phase 6） | GoalPanel 显示当前 active；队列状态单独 chip 或 `/goal queue` 查看，不合并到主面板 |
