# wolo 使用说明书

`wolo` 是一个独立的工作日志应用，运行在 OpenHarness 的模型、鉴权、消息通道和工具执行能力之上。它仿照 `solo` 的架构，但默认目标从个人日记切换为工作记录：项目进展、会议结论、代码变更、prompt 经验、tool 使用、blocker、风险、决策和 next action。

`wolo` 不依赖 `ohmo` gateway，也不会把工作日志能力注册进普通 `ohmo` 会话；`ohmo`、`solo` 和 `wolo` 的工作目录、配置、进程状态是分开的。

## 1. 它解决什么问题

`wolo` 面向“工作现场里不规整的输入”：

- 你可以直接发送一句工作记录。
- 你可以粘贴会议纪要、PR/issue 片段、周报草稿、命令输出摘要或多天补录。
- 模型负责理解、纠错、拆分、摘要、打标签、识别项目/工具/prompt/blocker/决策。
- 工具负责可靠落盘、查询、导出、报告生成和 gateway 生命周期管理。

核心原则：

1. 工作输入可以混乱，模型负责结构化。
2. prompt 和 tool 也是工作资产，需要像代码和决策一样被记录。
3. 信息不清楚时不猜，进入待确认状态或追问。
4. 输出服务于后续周报、复盘、交接和长期工作记忆。

## 2. 安装与入口

在 OpenHarness 仓库源码目录内，默认用 `uv run` 调用：

```bash
uv sync --extra dev
uv run wolo --help
```

如果想直接输入 `wolo`，需要先让虚拟环境脚本进入 `PATH`：

```bash
source .venv/bin/activate
wolo --help
```

也可以安装为可执行工具后直接使用：

```bash
uv tool install -e .
wolo --help
```

也可以用 Python 模块方式启动：

```bash
python -m wolo --help
```

下文命令示例为了简洁都写成 `wolo ...`。如果你没有激活 `.venv` 或全局安装，请把示例中的 `wolo` 替换成 `uv run wolo`。

## 3. 工作目录

默认工作目录：

```text
~/.wolo
```

可以通过两种方式覆盖：

```bash
wolo --workspace /path/to/workspace ...
```

或设置环境变量：

```bash
export WOLO_WORKSPACE=/path/to/workspace
```

目录结构：

```text
~/.wolo/
  config.json
  state.json
  soul.md
  user.md
  gateway.pid
  data/
    entries.jsonl
    records.jsonl
    todos.jsonl
    decisions.jsonl
    highlights.jsonl
    pending_confirmations.jsonl
    profile_updates.jsonl
    reports.jsonl
  memory/
    MEMORY.md
    *.md
  logs/
    gateway.log
  exports/
    export_*.md
    export_*.json
```

| 文件 | 说明 |
| --- | --- |
| `config.json` | wolo 应用配置，包括模型 profile、启用的消息通道和通道配置 |
| `state.json` | gateway 运行状态快照 |
| `soul.md` | wolo 的工作日志 persona、边界和行为准则 |
| `user.md` | 用户工作上下文：角色、团队、项目、工具、报告偏好 |
| `data/entries.jsonl` | 原始工作输入 |
| `data/records.jsonl` | 模型整理后的结构化工作记录 |
| `data/todos.jsonl` | 从工作记录派生出的待办事项 |
| `data/decisions.jsonl` | 从工作记录沉淀出的关键决策 |
| `data/highlights.jsonl` | 重要事项、prompt/tool 经验、blocker 和风险 |
| `data/pending_confirmations.jsonl` | 需要确认的模糊记录 |
| `data/profile_updates.jsonl` | 待沉淀的工作上下文更新建议 |
| `data/reports.jsonl` | 生成过的周报、月报、年报 |
| `memory/` | 长期工作记忆，例如项目背景、工具链、prompt 模式 |

## 4. 快速开始

初始化工作目录：

```bash
wolo init
```

记录一条原始工作日志：

```bash
wolo record "今天修完 gateway 去重逻辑，主要 blocker 是 session hash 没覆盖 chat_id"
```

让模型整理待处理记录：

```bash
wolo process
```

查看结构化记录：

```bash
wolo view
wolo view --limit 50
```

搜索 prompt 或 tool 相关记录：

```bash
wolo search "prompt tool blocker"
wolo search "gateway" --tags code,review
```

生成周报：

```bash
wolo report weekly
```

查看状态：

```bash
wolo status
```

检查工作目录：

```bash
wolo doctor
```

## 5. CLI 命令详解

### 5.1 `init`

创建工作目录、配置文件、默认 persona 和数据目录。

```bash
wolo init
wolo init --workspace /tmp/my-wolo
```

输出示例：

```text
Initialized wolo at /Users/yulin/.wolo
```

### 5.2 `config`

交互式配置模型 profile 和消息通道。

```bash
wolo config
```

会依次配置：

- OpenHarness provider profile
- 是否启用 Telegram、Slack、Discord、Feishu
- 各通道的 token、app id、allow_from 等字段
- 是否发送 progress 和 tool hint

`provider_profile` 使用 OpenHarness 的 profile 名称，例如 `codex`。鉴权仍然复用 OpenHarness：

```bash
oh auth status
oh setup
```

### 5.3 `record`

写入一条原始工作记录到 `entries.jsonl`。

```bash
wolo record "上午和平台组对齐 Feishu bot 权限，结论是先保留 allow_from 白名单"
```

这一步只保存原始输入，不一定立刻生成结构化记录。后续用 `process` 让模型整理。

### 5.4 `list`

查看原始记录。

```bash
wolo list
wolo list --limit 100
```

输出格式：

```text
2026-05-18T10:00:00+00:00 [local] 上午修复 prompt 路由误判
```

### 5.5 `process`

处理待整理的原始记录：

```bash
wolo process
```

处理逻辑：

1. 找出还没有结构化的 `entries`。
2. 调用 OpenHarness 模型，把原始工作记录整理为结构化 JSON。
3. 如果模型返回 `records` 数组，则按多条工作日志批量入库。
4. 如果模型判断 `needs_clarification=true`，写入待确认文件。
5. 否则写入 `records.jsonl`。

模型重点识别：

- 项目、仓库、系统、服务、工具
- 会议、评审、PR、issue、发布、事故
- prompt 设计、tool 调用、命令、模型配置
- blocker、风险、决策、结论、next action
- 可沉淀的工作记忆或报告素材

### 5.6 `view`

查看结构化工作日志。

```bash
wolo view
wolo view --limit 20
```

输出格式：

```text
2026-05-18 完成 [原始] [项目,代码,tool] 修复 gateway 去重逻辑
```

### 5.7 `search`

按关键词、标签、状态或日期范围搜索工作记录。

```bash
wolo search "gateway blocker"
wolo search "prompt" --tags prompt,tool --limit 20
wolo search "review" --start 2026-05-01 --end 2026-05-18
```

### 5.8 `todos` / `done` / 工作 artifact 查询

`wolo` 的模型整理流程分为两阶段：第一阶段只生成主工作记录，第二阶段再从已落盘记录中派生待办、决策和重要事项。这样可以降低复杂 JSON 失败率；即使 artifact 提取临时失败，主工作记录也会保留，后续可通过工具或 CLI 手动补充。

这些 artifact 供后续查询和周报引用，可通过 CLI 或 agent 语义调用：

```text
/wolo 最近有哪些待办？
/wolo 标记 todo_id 为完成
/wolo 查一下最近的 blocker
/wolo 最近有哪些 prompt/tool 经验？
/wolo wolo 项目最近的重要决策是什么？
```

派生 artifact 类型：

| 类型 | 文件 | 用途 |
| --- | --- | --- |
| Todo | `todos.jsonl` | 待办、负责人动作、后续计划 |
| Decision | `decisions.jsonl` | 关键决策、原因、影响范围 |
| Highlight | `highlights.jsonl` | 重要事项、blocker、风险、prompt/tool 经验 |

### 5.9 `report`

生成周报、月报或年报。

```bash
wolo report weekly
wolo report monthly
wolo report yearly
```

报告提示词会优先输出：

- 已完成事项和可量化交付
- 项目/任务维度的进展
- 关键决策及其依据
- blocker、风险和未解决问题
- prompt/tool 经验、失败模式和可复用做法
- 开放待办和已关闭事项
- 引用来源记录或 artifact 作为证据链
- next actions

### 5.10 `status`

查看数据和 gateway 状态：

```bash
wolo status
```

输出示例：

```text
wolo: entries=12 | records=10 | pending=1 | gateway=stopped | path=/Users/yulin/.wolo/data
```

### 5.11 `start` / `stop`

启动或停止后台 gateway：

```bash
wolo start
wolo stop
```

### 5.12 `doctor`

检查工作目录是否完整：

```bash
wolo doctor
```

## 6. 远程消息用法

`wolo` 支持与 `solo` 相同的消息通道能力。通过 Feishu/Slack/Telegram/Discord 等通道接入后，可以发送：

```text
/wolo record 今天完成 API 超时排查，根因是下游重试放大
/wolo process
/wolo view 10
/wolo report weekly
/wolo status
/wolo backfill 2026-05-17 昨天主要在写 wolo 测试和 README
```

如果 gateway 被配置为默认记录模式，直接发送工作内容也可以入库。

## 7. 工具列表

模型侧可调用的 wolo 专用工具包括：

| 工具 | 用途 |
| --- | --- |
| `wolo_record` | 记录单条工作日志，并尽量补充摘要、标签、状态 |
| `wolo_import_records` | 从多天补录、会议流水账或周报草稿中拆分多条记录 |
| `wolo_clarify` | 对关键工作事实不清楚时只追问一个问题 |
| `wolo_process` | 处理待整理记录和提醒 |
| `wolo_backfill` | 补录缺失日期的工作日志 |
| `wolo_report` | 生成周报、月报、年报 |
| `wolo_view` | 查看最近结构化记录 |
| `wolo_search` | 按项目、prompt、tool、blocker、日期等搜索 |
| `wolo_todos` | 查看待办事项 |
| `wolo_done` | 标记待办完成 |
| `wolo_blockers` | 查看 blocker |
| `wolo_decisions` | 查看关键决策 |
| `wolo_highlights` | 查看重要事项、prompt/tool 经验和风险 |
| `wolo_work_query` | 聚合查询过往工作、重要事项和决策 |
| `wolo_update_record` | 修正已有记录 |
| `wolo_delete_record` | 删除明确指定的记录 |
| `wolo_status` | 查看状态 |
| `wolo_get_now` | 获取当前本地时间 |
| `wolo_profile_update` | 记录可审核的工作上下文更新建议 |
| `wolo_remember` | 写入长期工作记忆 |
| `wolo_suggest_reflection` | 生成工作复盘问题 |
| `wolo_sync_context` | 同步 git/calendar 等外部工作上下文 |
| `wolo_visualize` | 生成活动、标签或状态分布 |
| `wolo_export` | 导出 Markdown/JSON 工作记录 |

## 8. 记录建议

最适合 wolo 的输入不是正式周报，而是每天随手记录的高信号碎片：

```text
上午 review 了 PR #42，主要风险是 auth fallback 太宽，建议改成显式错误。
```

```text
prompt 经验：让模型先列文件边界再 patch，明显减少无关 diff。
```

```text
tool 经验：Feishu gateway 排查时先看 gateway.log，再查 state.json，避免误判为鉴权问题。
```

```text
blocker：OpenAI-compatible client 的 streaming event 没带 usage，周报指标暂时无法自动统计。
```

## 9. 和 solo 的区别

| 维度 | solo | wolo |
| --- | --- | --- |
| 默认目录 | `~/.solo` | `~/.wolo` |
| 环境变量 | `SOLO_WORKSPACE` | `WOLO_WORKSPACE` |
| 命令前缀 | `/solo` | `/wolo` |
| 主要目标 | 个人生活记录与成长复盘 | 工作记录、项目复盘、周报和 prompt/tool 经验沉淀 |
| 默认 persona | 个人日志助手 | 工作日志助手 |
| 报告重点 | 情绪、生活事件、长期个人模式 | 交付、决策、blocker、风险、工具/prompt 经验、next action |

## 10. 隐私与边界

- 不要把公司机密、客户数据、token、密钥或受保护个人信息写入日志。
- wolo 只记录你提供的内容，不应发明项目结论、指标、owner 或工具结果。
- 对会误导后续周报/复盘的模糊信息，wolo 会进入待确认或追问。
- 工作日志默认落在本机 `~/.wolo`，远程通道和模型调用的安全性取决于你的 provider 与 channel 配置。
