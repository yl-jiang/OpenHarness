# tool_metadata 技术参考

> 本文档汇总 `QueryContext.tool_metadata`（`dict[str, object]`）的用途、存储内容、初始化来源与生命周期。

---

## 1. 概述

`tool_metadata` 是**跨查询生命周期的共享状态容器**，在 `QueryEngine` 初始化时创建，贯穿整个会话。它既承载短期运行时状态（工具调用历史、缓存），也承载中期上下文（任务焦点、已验证工作），还挂载外部集成句柄（MCP、记忆、自我进化控制器）。

---

## 2. 存储信息分类

### 2.1 任务焦点与上下文感知

| Key | 类型 | 说明 |
|-----|------|------|
| `task_focus_state` | `dict` | 当前任务焦点，含 `goal`（用户目标）、`active_artifacts`（活跃工件）、`recent_goals`、`verified_state` |
| `recent_verified_work` | `list[str]` | 最近已验证的完成工作摘要 |
| `recent_work_log` | `list[str]` | 最近操作日志（比 `verified_work` 更轻量） |

### 2.2 工具执行历史

| Key | 类型 | 说明 |
|-----|------|------|
| `tool_call_history` | `list[dict]` | 工具调用签名历史，用于 **doom loop（死循环）检测** |
| `read_file_state` | `list[dict]` | `read_file` 调用历史（路径、offset、limit、内容） |
| `tool_name_repair_notices` | `list[dict]` | LLM 工具名拼写错误及自动修复记录 |

### 2.3 异步 Agent

| Key | 类型 | 说明 |
|-----|------|------|
| `async_agent_state` | `list[dict]` | 异步 Agent 生成活动状态 |
| `async_agent_tasks` | `list[dict]` | 已生成的异步 Agent 任务详情 |

### 2.4 权限与模式

| Key | 类型 | 说明 |
|-----|------|------|
| `permission_mode` | `str` | 当前权限模式：`"plan"` 或 `"default"` |

### 2.5 自我进化（Self-Evolution）

| Key | 类型 | 说明 |
|-----|------|------|
| `self_evolution_state` | `dict` | 后台记忆/Skill 审查计数器 |
| `self_evolution_controller` | `object` | 会话级 `SelfEvolutionController` 实例（**不持久化**） |

### 2.6 对话压缩（Compact）

| Key | 类型 | 说明 |
|-----|------|------|
| `compact_checkpoints` | `list[dict]` | 压缩检查点历史 |
| `compact_last` | `dict` | 最近一次压缩的详细状态 |

### 2.7 运行时配置与外部集成

| Key | 类型 | 说明 |
|-----|------|------|
| `session_id` | `str` | 当前会话唯一标识 |
| `current_model` | `str` | 当前使用的模型 |
| `current_provider` | `str` | 当前模型提供商 |
| `current_api_format` | `str` | 当前 API 格式 |
| `current_base_url` | `str` | 当前 API 基础地址 |
| `current_active_profile` | `str` | 当前激活的配置文件 |
| `mcp_manager` | `object` | MCP 客户端管理器实例（**不持久化**） |
| `bridge_manager` | `object` | Bridge 管理器实例（**不持久化**） |
| `memory_provider_manager` | `object` | 记忆提供者管理器（**不持久化**） |
| `todo_store` | `object` | 待办事项存储（**不持久化**） |
| `system_prompt_refresher` | `callable` | 系统提示刷新回调（**不持久化**） |
| `extra_skill_dirs` | `list[Path]` | 额外 Skill 目录 |
| `extra_plugin_roots` | `list[Path]` | 额外插件根目录 |

> **说明**：标注"不持久化"的 key 在会话保存时会被过滤，仅存在于内存中。

---

## 3. 初始化与填充来源

### 3.1 阶段一：UI Runtime 搭建骨架

**文件**：`src/openharness/ui/runtime.py`

会话启动时创建 `restored_metadata` 默认值：

```python
restored_metadata = {
    "read_file_state": [],
    "invoked_skills": [],
    "async_agent_state": [],
    "async_agent_tasks": [],
    "recent_work_log": [],
    "recent_verified_work": [],
    "task_focus_state": default_task_focus_state(),
    "compact_checkpoints": [],
    "self_evolution_state": {},
}
```

随后发生三件事：
1. **恢复旧数据**：如果传入了 `restore_tool_metadata`（从快照恢复），覆盖进字典。
2. **写入运行时配置**：`_sync_runtime_tool_metadata()` 写入 `current_model`、`current_provider` 等。
3. **挂载管理器实例**：创建 `QueryEngine` 时传入 `mcp_manager`、`bridge_manager`、`session_id`、`todo_store` 等；创建 engine 后额外挂载 `system_prompt_refresher`、`memory_provider_manager`、`self_evolution_controller`。

### 3.2 阶段二：QueryEngine 持有引用

**文件**：`src/openharness/engine/query_engine.py:89`

```python
self._tool_metadata = tool_metadata or {}
```

QueryEngine 本身不创建新字典，直接持有 UI 层传入的引用。

### 3.3 阶段三：用户消息提交

**文件**：`src/openharness/engine/query_engine.py:402`

```python
remember_user_goal(self._tool_metadata, user_message.text)
```

- 写入 `task_focus_state["goal"]`
- 写入 `task_focus_state["recent_goals"]`

### 3.4 阶段四：工具执行管道（最密集）

**文件**：`src/openharness/engine/query.py`

每次工具调用成功后，`_update_tool_metadata_stage()` 调用 `_record_tool_carryover()`，分三条线写入：

#### A. 状态线：`_carryover_state()`

| 工具名 | 写入的 Key |
|--------|-----------|
| `read_file` | `read_file_state`、`task_focus_state["active_artifacts"]`、`recent_verified_work` |
| `skill_manager` (load/write/patch) | `invoked_skills`、`task_focus_state["active_artifacts"]`、`recent_verified_work` |
| `agent` / `send_message` | `async_agent_state`、`async_agent_tasks`、`recent_verified_work` |
| `plan_mode` (enter/exit) | `permission_mode` |
| `web_fetch` | `task_focus_state["active_artifacts"]`、`recent_verified_work` |
| `web_search` / `glob` / `grep` / `bash` | `recent_verified_work` |

#### B. 日志线：`_carryover_log()`

| 工具名 | 写入的 Key |
|--------|-----------|
| `read_file` / `bash` / `grep` / `skill_manager` / `agent` / `plan_mode` | `recent_work_log` |

#### C. 工具管道其他写入点

| 位置 | 写入的 Key | 说明 |
|------|-----------|------|
| `_resolve_tool_stage()` | `tool_name_repair_notices` | 工具名被自动修复时记录 |
| `_execute_tool_call()` | `tool_call_history` | `record_tool_call_result()` 每次调用后追加 |

### 3.5 阶段五：Skill 命令层

**文件**：`src/openharness/commands/skills.py:50`

```python
bucket = context.engine.tool_metadata.setdefault("invoked_skills", [])
```

Skill 被加载时维护 `invoked_skills` 列表（保持最近使用顺序）。

### 3.6 阶段六：对话压缩服务

**文件**：`src/openharness/services/compact/__init__.py`

每次压缩完成后写入：
- `compact_checkpoints`
- `compact_last`

### 3.7 阶段七：自我进化模块

**文件**：`src/openharness/evolution/self_evolution.py`、`query_engine.py`

`QueryEngine` 在每个 user turn / assistant turn 时调用 `SelfEvolutionController`，Controller 内部读写 `self_evolution_state`。

---

## 4. 生命周期与数据流

```
┌─────────────────────────────────────────────────────────────┐
│  ui/runtime.py                                               │
│  - 创建骨架（空列表/字典）                                    │
│  - 恢复 restore_tool_metadata（如有）                         │
│  - 写入 current_* 运行时配置                                  │
│  - 挂载 mcp_manager / memory_provider_manager 等             │
└──────────────────────────┬──────────────────────────────────┘
                           │ 传入 QueryEngine
┌──────────────────────────▼──────────────────────────────────┐
│  query_engine.py                                             │
│  - __init__ 持有 tool_metadata 引用                           │
│  - submit_message() → remember_user_goal()                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ 创建 QueryContext 传入 run_query
┌──────────────────────────▼──────────────────────────────────┐
│  query.py 工具管道                                           │
│  - _resolve_tool_stage → tool_name_repair_notices           │
│  - _execute_tool_call  → tool_call_history                  │
│  - _update_tool_metadata_stage                             │
│      ├── _carryover_state  → read_file_state / task_focus   │
│      ├── _carryover_log    → recent_work_log                │
│      └── _record_async     → async_agent_tasks              │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌───────────────┐  ┌──────────────┐  ┌────────────────┐
│ compact service│  │ self_evolution│  │ session_storage │
│ 更新 checkpoints│  │ 更新 state   │  │ 持久化到磁盘    │
│ / compact_last │  │              │  │ （过滤不持久化）│
└───────────────┘  └──────────────┘  └────────────────┘
```

---

## 5. 持久化策略

### 5.1 持久化的 Key

由 `ToolMetadataKey.all_persisted_keys()` 定义，包含：

- `permission_mode`
- `read_file_state`
- `invoked_skills`
- `async_agent_state`
- `async_agent_tasks`
- `recent_work_log`
- `recent_verified_work`
- `task_focus_state`
- `compact_checkpoints`
- `compact_last`
- `self_evolution_state`

**文件**：`src/openharness/engine/types.py:64-82`

### 5.2 不持久化的 Key（纯运行时）

- `session_id`（由运行时重新生成）
- `mcp_manager`、`bridge_manager`
- `memory_provider_manager`
- `self_evolution_controller`
- `system_prompt_refresher`
- `todo_store`
- `tool_name_repair_notices`
- `tool_call_history`
- `file_read_cache`
- `current_model` / `current_provider` 等（运行时重新同步）

### 5.3 持久化入口

**保存**：`src/openharness/services/session_storage.py:34-41`

```python
def _persistable_tool_metadata(tool_metadata):
    for key in _PERSISTED_TOOL_METADATA_KEYS:
        if key in tool_metadata:
            payload[key] = _sanitize_metadata(tool_metadata[key])
```

**恢复**：`ui/runtime.py`、`cli.py` 从快照读取后通过 `restore_tool_metadata` 参数回传。

---

## 6. 关键 Enum 定义

**文件**：`src/openharness/engine/types.py`

```python
class ToolMetadataKey(str, Enum):
    PERMISSION_MODE = "permission_mode"
    READ_FILE_STATE = "read_file_state"
    INVOKED_SKILLS = "invoked_skills"
    ASYNC_AGENT_STATE = "async_agent_state"
    ASYNC_AGENT_TASKS = "async_agent_tasks"
    RECENT_WORK_LOG = "recent_work_log"
    RECENT_VERIFIED_WORK = "recent_verified_work"
    TASK_FOCUS_STATE = "task_focus_state"
    COMPACT_CHECKPOINTS = "compact_checkpoints"
    COMPACT_LAST = "compact_last"
    SELF_EVOLUTION_STATE = "self_evolution_state"
    SELF_EVOLUTION_CONTROLLER = "self_evolution_controller"
```

---

## 7. 调试技巧

在代码中快速查看当前 `tool_metadata` 内容：

```python
# 在 query.py 或 query_engine.py 中
import json
print(json.dumps(context.tool_metadata, indent=2, default=str))
```

或针对特定 key：

```python
from openharness.engine.types import ToolMetadataKey
history = context.tool_metadata.get(ToolMetadataKey.TOOL_CALL_HISTORY.value, [])
print(f"Tool call history count: {len(history)}")
```
