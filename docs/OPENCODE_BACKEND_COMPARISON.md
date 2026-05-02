# OpenHarness 与 opencode 后台架构对比

本文对比 OpenHarness 与 opencode 作为 AI coding agent 的后台实现差异，重点关注 agent 编排、提示词设计、工具调用、slash command、子 agent 调度、执行效率、context engineering、context compact 和 harness engineering。

本对比以活跃的 `anomalyco/opencode` 为参考对象，不以已归档的 `opencode-ai/opencode` 为准。本文只讨论 high-level 后台实现，不讨论前端技术栈差异。

## 1. 总体判断

OpenHarness 不是弱版 opencode。OpenHarness 在 skill、hook、memory、后台任务、compact attachments、工具元数据延续、artifact offload 等方面更有 harness 潜力；opencode 的优势在于 session-centered runtime 更统一，agent、command、tool、compact 的边界更清楚，执行链路更短。

| 维度 | opencode | OpenHarness | 判断 |
| --- | --- | --- | --- |
| 核心抽象 | Session-centered backend，TUI/CLI/API 围绕 session service | UI Runtime、QueryEngine、SessionBackend、TaskManager 多中心协作 | opencode 更统一，OpenHarness 能力更丰富但分散 |
| Agent 编排 | Agent profile 是一等对象，primary/subagent/utility agent 清晰 | Agent definitions、AgentTool、swarm backend、query loop 分布在多处 | OpenHarness 需要收敛 agent 生命周期 |
| 工具调用 | Tool registry、processor、permission、repair、truncation 闭环清楚 | ToolRegistry、QueryContext、permission、hook、offload 功能强但链路长 | OpenHarness 功能更强，opencode 边界更清晰 |
| Slash command | Command service 聚合 built-in/config/MCP/skill commands | 单个 registry 承载大量命令逻辑 | OpenHarness 需要拆分 command 层 |
| Context compact | Overflow 驱动，hidden compaction agent，tail window + anchored summary | microcompact、full compact、context collapse、attachments 多机制并存 | OpenHarness 更强但复杂度偏高 |
| 子 agent | TaskTool 创建 child session，继承权限和 session 语义 | subprocess/tmux/in_process 多后端，后台 task 管理更强 | OpenHarness 能力更大，但需要统一 child session 模型 |
| Harness engineering | server/session/event/snapshot 模型清晰 | hook、memory、plugin、task、session store 更丰富 | OpenHarness 有成为更强 harness 的底子 |

核心结论：opencode 是简洁统一的 session agent runtime；OpenHarness 是能力丰富但需要架构收敛的 agent harness。

## 2. 后台 agent 编排

### opencode 的实现特点

opencode 的后台编排围绕 session 展开：

- agent profile 区分 `build`、`plan`、`general`、`explore`、`compaction`、`title`、`summary`。
- `build` 是默认 primary agent。
- `plan` 是只读规划 agent。
- `general` 和 `explore` 是 subagent。
- `compaction`、`title`、`summary` 是 hidden utility agent。
- session loop 负责创建 user message、检查 step limit、处理 overflow compact、创建 assistant message、resolve tools、调用 stream processor。
- 同一个 session 同时只允许一个 running loop，避免状态错乱。
- subagent 通过 TaskTool 创建 child session，而不是另起一套不相关的 agent runtime。

它的优势是 agent、session、message、tool、compact 都围绕 session 一个核心模型转。

### OpenHarness 的实现特点

OpenHarness 当前由多个中心协作：

- `QueryEngine` 管理 conversation history、cost、system prompt、tool registry。
- `run_query()` 是实际 tool-aware loop。
- `QueryContext` 承载 API client、权限、hook、cwd、compact threshold、tool metadata。
- `AgentTool` 通过 backend spawn 子 agent。
- `BackgroundTaskManager` 管理 shell/agent 后台任务。
- `swarm` 提供 subprocess、in_process、tmux 等后端思路。
- `SessionBackend` 和 runtime 负责会话状态、恢复、UI 通知。

OpenHarness 的优势是能力丰富，适合做真正的 harness。问题是 agent run 的语义中心不够单一：一次 agent run 同时涉及 QueryEngine、SessionBackend、TaskManager、SwarmBackend。

### 优化建议

引入明确的 `SessionService` 或 `AgentRunService`：

```text
SessionService
  - create_session()
  - append_user_message()
  - run_agent_turn()
  - compact_if_needed()
  - spawn_child_session()
  - pause/resume/cancel()
  - stream_events()
```

然后让 TUI/CLI、slash command、subagent、compact/title/summary 都围绕 SessionService 运作。TaskManager 只负责 process lifecycle，不负责 agent 语义。

## 3. 提示词设计

### opencode 的提示词策略

opencode 的 prompt 更分层：

```text
provider prompt
+ agent prompt
+ environment/context
+ instruction files
+ user/system additions
+ tool/skill specific description
```

特点：

- 每个 agent 有自己的 prompt。
- `plan`、`build`、`explore`、`compaction` 角色边界清楚。
- hidden utility agents 有独立 prompt，不污染主 agent。
- read 文件时可按需注入相邻 instruction 文件。
- command template 可以指定 agent、model、subtask。

### OpenHarness 的提示词策略

OpenHarness 的 prompt 更强约束、全量注入：

- `system_prompt.py` 提供基础工程行为规范。
- `prompts/context.py` 拼 runtime prompt，包括 fast mode、reasoning settings、skills list、delegation section、tool-use rules、CLAUDE.md、本地规则、issue/PR context、project memory、relevant memories。
- compact 后还会注入 task focus、verified work、recent files、artifact 等附件。

优势是适合复杂 coding task，行为控制强。风险是 prompt 层数多、容易重复注入、token 开销高，并且部分行为通过继续堆系统提示实现。

### 优化建议

把 prompt 分成可缓存、可替换、可预算的模块：

```text
Base Harness Prompt
  - safety
  - engineering behavior
  - tool protocol

Agent Profile Prompt
  - build
  - plan
  - explore
  - review
  - compact
  - title
  - summary

Runtime Context Block
  - cwd
  - git state
  - issue/PR
  - active files
  - task focus

Memory Block
  - project memory
  - user memory
  - session memory

Tool/Skill Block
  - available tools
  - active skill instructions
```

核心目标不是减少提示词能力，而是让稳定规则、agent 角色、动态上下文、记忆、技能各自有边界。

## 4. 工具调用

### opencode 的工具体系

opencode 的工具链清晰：

- 统一定义 tool schema、description、execute。
- Tool registry 负责 built-in、plugin、skill、task tools。
- Stream processor 逐事件处理 tool input、tool call、tool result、tool error、finish step。
- 有 doom-loop 检测，连续重复 tool call 会触发 permission。
- 有 tool name repair，无法修复则转成 invalid tool。
- 有统一 truncate。
- read、edit、bash 都有安全和上下文处理。

### OpenHarness 的工具体系

OpenHarness 的工具链更强但更复杂：

- 每轮把 tool schema 送给 API。
- 多个 tool call 可通过 `asyncio.gather` 并发执行。
- 每个 tool call 都保证返回 tool result，避免后续 API 请求被拒绝。
- `_execute_tool_call()` 负责 hook、tool lookup、pydantic validation、permission check、permission prompt lock、tool execution、output offload、metadata carryover、post hook。
- 大输出会 offload 到 artifact，只把 preview 送回模型。
- `ToolMetadataKey` 追踪 read files、invoked skills、async agents、work log、verified work 等。

OpenHarness 的亮点是 tool metadata carryover 和 output offload 更像真正的 harness。短板是未知 tool name repair 仍待完善，tool 执行链路职责偏多，metadata 更新函数较分散。

### 优化建议

优先补齐以下能力：

1. 实现 `invalid_tool` structured result。
2. 实现 tool name repair，包括大小写修复、alias map、相似名称提示。
3. 增加 doom-loop guard，连续同 tool、同 args、同失败结果达到阈值时提醒模型换策略或请求确认。
4. 将 tool execution pipeline 拆成明确阶段：

```text
resolve_tool
validate_input
pre_hook
check_permission
execute_tool
post_hook
normalize_result
offload_large_output
update_metadata
```

OpenHarness 不应退回简单 truncate，而应把 artifact offload 标准化为 `ToolResultNormalizer`。

## 5. Slash command

### opencode 的 command 设计

opencode 的 slash command 更像 prompt template 和 backend action 的组合：

- built-in command，例如 `init`、`review`。
- config command，从 markdown/frontmatter 读取。
- MCP prompt command。
- skill command。
- command 可指定 agent、model、subtask、arguments。
- TUI 主要负责识别和触发，核心逻辑在 command service/session service。

它的优势是 slash command 是可组合入口，而不是业务逻辑堆放点。

### OpenHarness 的 command 设计

OpenHarness 的 `commands/registry.py` 能力很强，但承担过多：

- command 解析、注册、执行、渲染都集中在一个文件。
- `CommandResult` 同时支持 message、exit、clear_screen、replay_messages、continue_pending、refresh_runtime、submit_prompt、submit_model。
- built-in commands、plugin commands、skill alias、compact、init、memory、model、help 等集中在同一层。
- `/init` 偏静态文件创建，不如模型驱动项目分析。

### 优化建议

拆分 command 层：

```text
commands/
  core.py
  service.py
  builtin/
    session.py
    config.py
    project.py
    memory.py
    debug.py
  templates/
    loader.py
  skills.py
  plugins.py
```

并按语义分类执行位置：

| 类型 | 示例 | 执行位置 |
| --- | --- | --- |
| UI Action | `/clear`, `/help` | UI/runtime |
| Session Action | `/compact`, `/undo`, `/resume` | SessionService |
| Prompt Template | `/review`, `/init` | CommandService -> SessionService |
| Config Action | `/model`, `/permission` | Runtime config |
| Skill Alias | `/read`, `/write` | SkillService |

slash command 不应该成为业务逻辑垃圾桶。

## 6. 子 agent 调度

### opencode 的子 agent 模型

opencode 的 subagent 本质是 child session：

- TaskTool 创建 child session。
- child session 继承父 session 的关键 permission。
- parent 调用 child session 的 prompt loop。
- child 输出 task result。
- 历史、权限、compact、tool、event 都复用 session 机制。

### OpenHarness 的子 agent 模型

OpenHarness 的子 agent 能力更大：

- `AgentTool` 可启动后台 agent。
- `BackgroundTaskManager` 管理进程、输出文件、状态、waiter、completion listener。
- subprocess backend 支持 teammate 子进程。
- in-process backend 有 ContextVar isolation、mailbox、abort controller 设计。
- agent definitions 支持 tools、disallowed_tools、skills、MCP、hooks、model、permission_mode、max_turns、background、memory、isolation 等字段。

问题是 child agent 与主 session 的 message/session 语义还没有完全统一，background task 与 subagent/task tool 的边界容易混，in-process backend 仍需要补齐真实 QueryContext wiring。

### 优化建议

引入统一 `AgentRun`：

```text
AgentRun
  - id
  - parent_session_id
  - child_session_id
  - agent_profile
  - backend: in_process | subprocess | remote
  - status
  - events
  - result
  - artifacts
```

默认规则：

- `task` tool 创建 child session。
- backend 只是执行方式，不改变 agent 语义。
- background agent 也是 child session，只是异步完成。
- 短任务优先 in-process，强隔离或长任务使用 subprocess。
- 子 agent 结果进入主上下文时使用统一摘要格式。

## 7. 执行效率

### opencode 的效率优势

- session loop 状态机短。
- processor 直接消费 stream event。
- step limit 和 max steps prompt 简洁。
- compact 只在 overflow 或必要时触发。
- tool cleanup 和 unfinished tool handling 明确。
- old tool output prune 简单直接。

### OpenHarness 的效率优势

- 多 tool call 并发执行。
- 大输出 offload，避免长日志污染上下文。
- silent-stop auto-continue guard 对真实模型异常更鲁棒。
- permission prompt lock 避免多个审批并发打架。
- BackgroundTaskManager 可将长任务后台化。
- compact attachments 能保留更多任务状态。

### OpenHarness 的效率问题

- runtime system prompt 重建较多，应缓存。
- prompt 注入层多，token 开销可能偏高。
- compact 机制路径多，调试成本高。
- 子 agent subprocess 启动成本高，短任务不划算。
- tool/hook/permission pipeline 缺少统一 trace 展示。

### 优化建议

短期：

- 缓存 runtime system prompt。
- 为 prompt block 做 hash，只有相关输入变化才重建。
- 工具 schema 做稳定缓存。
- 增加 tool execution trace：validation time、permission time、execution time、offload size、hook time。

中期：

- 补齐 in-process child session，用于短任务。
- subprocess child session 用于强隔离、长任务和后台任务。
- compact 前先做 deterministic prune，降低 LLM compact 频率。

## 8. Context engineering

### opencode 的 context 策略

opencode 更克制：

- system prompt 分层。
- instruction 文件按需 resolve。
- read tool 可注入相关 instruction。
- compact 选择 tail window。
- old tool outputs prune。
- skill/tool description 动态生成。

它的哲学是尽量只放当前 session loop 需要的上下文。

### OpenHarness 的 context 策略

OpenHarness 更像全信息 harness：

- runtime prompt 注入 skills、delegation、memory、local rules、PR/issue context。
- `ToolMetadataKey` 追踪 read files、invoked skills、async agents、work log、recent verified work、task focus、active artifacts。
- compact attachments 能把这些状态带过压缩边界。
- session store 可以跨会话检索历史。
- output offload 保留 artifact 指针。

这是长期优势，但上下文不是越多越好。过多 context 会稀释模型注意力。

### 优化建议

建立 Context Budget Manager：

```text
L0 always-on
  - safety
  - tool protocol
  - current task goal

L1 task-critical
  - files touched
  - active plan
  - latest user constraints
  - failing tests or errors

L2 useful
  - project memories
  - relevant historical decisions
  - invoked skills

L3 optional
  - older work log
  - verbose artifacts
  - stale async agent info
```

每个 context block 都应包含：

```text
priority
token_budget
freshness
source
dedupe_key
eviction_policy
```

## 9. Context compact

### opencode 的 compact 模型

opencode compact 清楚直接：

- 检测 overflow。
- 创建 compaction part。
- 使用 hidden compaction agent。
- 保留最近 tail turns。
- 生成 anchored summary。
- prune 旧 tool output，保护重要 skill output。

summary 结构包括 Goal、Constraints & Preferences、Progress、Key Decisions、Next Steps、Critical Context、Relevant Files。

### OpenHarness 的 compact 模型

OpenHarness compact 更强：

- microcompact。
- deterministic session memory compact。
- full LLM compact。
- context collapse。
- prompt-too-long retry。
- compact attachments。
- hook messages。
- metadata checkpoints。
- post-compact message rebuild。

问题是机制多、概念重叠，compact 后消息构造顺序复杂，attachments 需要更明确的预算和优先级。

### 优化建议

统一为三层 compact：

```text
1. Prune
   - 删除或替换旧 tool output
   - 保留 artifact pointer

2. Microcompact
   - 局部压缩最近噪声
   - 不调用或少调用 LLM

3. Full Compact
   - hidden compact agent
   - anchored summary
   - attachments with budget
```

建议固定 summary 结构：

```text
Goal
User Constraints
Current State
Completed Work
Open Problems
Key Decisions
Relevant Files
Commands/Tests Run
Artifacts
Subagents
Next Actions
```

OpenHarness 可以比 opencode 更强的点是 compact 不只是摘要消息，还能携带 structured metadata。

## 10. Harness engineering

opencode 更像优秀 coding agent runtime；OpenHarness 应该成为更强的 agent harness：

```text
agent runtime
+ tool execution harness
+ task/subagent harness
+ memory harness
+ evaluation harness
+ session replay harness
+ trace/observability harness
```

OpenHarness 已经具备基础：

- hooks
- plugins
- skills
- memory
- session storage
- compact metadata
- background task manager
- tool artifacts
- permission system
- silent-stop guard
- 多 agent backend

需要进一步产品化成以下能力：

1. 可回放：一次 agent run 的 prompt、tool call、permission、result、compact 都可重放。
2. 可审计：每次工具执行为什么被允许、耗时、输出大小、是否 offload。
3. 可压测：同一任务可以跑不同 model、agent profile、compact 策略。
4. 可评估：harness 自带真实 repo integration test。
5. 可恢复：中断后恢复 messages、child agents、artifacts、task focus。
6. 可对比：不同 agent profile 的成功率、成本、耗时可比较。

这会成为 OpenHarness 相比 opencode 的差异化能力。

## 11. OpenHarness 已有优势

| 能力 | OpenHarness 优势 |
| --- | --- |
| Skill system | 更强调用户和项目技能，可作为 agent 能力插件 |
| Hooks | 更适合个人或企业自动化流程 |
| Memory | project/user/session/history 能力更丰富 |
| Background task | 后台 agent/shell task 管理能力更强 |
| Tool metadata carryover | 可跨 compact 传递 read files、artifacts、verified work |
| Output offload | 大工具输出 artifact 化，比单纯 truncate 更适合长任务 |
| Silent-stop guard | 对真实模型空响应或异常停止更鲁棒 |
| Compact attachments | 长任务状态保留更强 |
| Permission prompt lock | 多工具审批时更安全 |
| Harness 潜力 | 更适合 eval、trace、replay、multi-agent orchestration |

这些能力不应删除，应该标准化和产品化。

## 12. 应借鉴 opencode 的能力

### P0：短期优先

1. Session-centered backend：把 QueryEngine、SessionBackend、TaskManager、AgentTool 的 agent 语义收敛到 SessionService。
2. Agent profile 分层：primary、subagent、utility agent 清晰分类。
3. Tool name repair 和 invalid tool：改善模型调用错工具名时的体验。
4. Doom-loop detection：连续重复工具调用时触发提醒或确认。
5. Command template 化：`/review`、`/init`、自定义命令更多走 markdown/frontmatter prompt template。
6. Compact anchored summary：让 compact 结果更稳定。

### P1：中期推进

1. child session subagent：子 agent 默认是 child session。
2. command service 拆分：command registry 不再是单文件大杂烩。
3. tool registry 动态描述：task、skill、plugin tools 的 description 根据上下文生成。
4. provider/model compatibility layer：集中处理不同模型的工具调用差异。
5. session event processor：标准化 stream event、tool event、message event，方便 replay 和 trace。

### P2：长期方向

1. server-first architecture：CLI/TUI/API 都只是 client。
2. snapshot/patch workspace tracking：agent 每步改动和成本关联。
3. workflow/model approval bridge：对贵模型、危险工具、工作流切换做细策略。

## 13. 当前冗余和复杂点

| 问题 | 影响 | 建议 |
| --- | --- | --- |
| `commands/registry.py` 过大 | 维护困难，command 类型混杂 | 拆分为 core/service/builtin/templates/skills/plugins |
| compact 机制过多 | 行为难预测，debug 成本高 | 统一为 prune/micro/full 三层 |
| prompt 注入层多 | token 开销和重复风险 | prompt block 化、hash 缓存、预算控制 |
| memory 系统分散 | 容易重复注入或语义冲突 | 建 MemoryService，统一 source/priority/freshness |
| tool metadata 更新函数分散 | 后续扩展困难 | 建 ToolState 或 ContextState 管理器 |
| subprocess/in_process/swarm/task 边界重叠 | agent 生命周期不清晰 | 统一 AgentRun + backend |
| `/init` 偏静态 | 不如模型驱动生成项目规则 | 改为 analyze project -> generate AGENTS.md/config |
| hook 生命周期不完整 | harness 可观测性打折 | 补齐 post_api_request 等关键点 |
| API client/provider 逻辑重复 | 增加维护成本 | 抽象 provider adapter |
| utility agent 不够显式 | compact/title/summary 等行为分散 | 显式定义 hidden utility agents |

## 14. 推荐优化路线图

### 第一阶段：低风险高收益

目标是在不大改架构的前提下补短板。

1. 拆分 `commands/registry.py`。
2. 增加 runtime system prompt 缓存。
3. 实现 tool name repair 和 invalid tool。
4. 实现 doom-loop detection。
5. 补齐 post API/tool lifecycle hook。
6. 给 compact summary 改成稳定 anchored structure。
7. 给 tool execution 增加结构化 trace。

### 第二阶段：架构收敛

目标是把 OpenHarness 的能力统一到 session 和 agent run。

1. 引入 `SessionService`。
2. 引入 `AgentRun` 模型。
3. 子 agent 改为 child session 语义。
4. TaskManager 降级为 lifecycle backend。
5. CommandService 对接 SessionService。
6. CompactService 对接 utility agent。
7. MemoryService 统一 memory block 注入。

### 第三阶段：超越 opencode 的 harness 能力

目标是把 OpenHarness 做成更适合工程验证的 coding agent。

1. Agent run replay。
2. Tool call audit trail。
3. Session diff/snapshot。
4. Multi-agent trace viewer。
5. Model/profile A/B eval。
6. Compact strategy evaluation。
7. Project-specific benchmark harness。
8. CI integration for agent behavior regression。

## 15. 推荐目标架构

```text
OpenHarness
  SessionService
    - message store
    - run loop
    - stream events
    - compact trigger
    - child sessions

  AgentService
    - agent profiles
    - primary/subagent/utility agents
    - model/tool/permission policy

  ToolService
    - registry
    - validation
    - permission
    - hooks
    - execution
    - result normalization
    - artifact offload

  CommandService
    - builtin commands
    - template commands
    - plugin commands
    - skill aliases

  ContextService
    - prompt blocks
    - memory blocks
    - instruction files
    - token budget
    - dedupe

  CompactService
    - prune
    - microcompact
    - full compact
    - structured summary
    - metadata carryover

  TaskService
    - subprocess backend
    - in-process backend
    - remote backend
    - lifecycle only

  HarnessService
    - trace
    - replay
    - eval
    - cost
    - audit
```

核心原则：

- session 是语义中心。
- backend 只是执行方式。
- context 是可预算资源。
- tool 是可审计动作。
- compact 是结构化状态迁移。

## 16. 最终建议

如果目标是做一个像 opencode 一样好用的 agent，OpenHarness 需要减少复杂度。如果目标是做一个比 opencode 更强的 agent harness，OpenHarness 不能只学 opencode 的简洁，还要把自己的 hook、skill、memory、background task、compact metadata、artifact、session history 做成统一平台能力。

建议路线：

1. 不重写。
2. 不照搬 opencode。
3. 先收敛中心模型：SessionService + AgentRun。
4. 再拆 command、tool、compact 的大文件和重复路径。
5. 最后把 harness engineering 做成 OpenHarness 的差异化能力。

最短路径是：用 opencode 的清晰架构整理 OpenHarness 的丰富能力，而不是用 opencode 的简单能力替换 OpenHarness 的高级能力。
