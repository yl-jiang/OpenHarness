# self-log 使用说明书

`self-log` 是一个独立的个人日志应用，运行在 OpenHarness 的模型、鉴权、消息通道和工具执行能力之上。它不依赖 `ohmo` gateway，也不会把日志能力注册进普通 `ohmo` 会话；`ohmo` 和 `self-log` 的工作目录、配置、进程状态是分开的。

## 1. 它解决什么问题

`self-log` 面向“现实世界里不规整的输入”：

- 你可以直接发送一句日常记录。
- 你可以粘贴一大段旧日记、流水账或多日期内容。
- 模型负责理解、纠错、拆分、摘要、打标签、判断情绪、识别待确认信息。
- 工具负责可靠落盘，不用复杂正则硬解析用户格式。

核心原则：

1. 人类输入可以混乱，模型负责结构化。
2. 工具只做确定动作：记录、批量入库、查看、生成报告、启动 gateway。
3. 信息不清楚时不猜，进入待确认状态或追问。

## 2. 安装与入口

在 OpenHarness 仓库源码目录内，默认用 `uv run` 调用：

```bash
uv sync --extra dev
uv run self-log --help
```

如果想直接输入 `self-log`，需要先让虚拟环境脚本进入 `PATH`：

```bash
source .venv/bin/activate
self-log --help
```

也可以安装为可执行工具后直接使用：

```bash
uv tool install -e .
self-log --help
```

也可以用 Python 模块方式启动：

```bash
python -m self_log --help
```

下文命令示例为了简洁都写成 `self-log ...`。如果你没有激活 `.venv` 或全局安装，请把示例中的 `self-log` 替换成 `uv run self-log`。

## 3. 工作目录

默认工作目录：

```text
~/.self-log
```

可以通过两种方式覆盖：

```bash
self-log --workspace /path/to/workspace ...
```

或设置环境变量：

```bash
export SELF_LOG_WORKSPACE=/path/to/workspace
```

目录结构：

```text
~/.self-log/
  config.json
  state.json
  gateway.pid
  data/
    entries.jsonl
    records.jsonl
    pending_confirmations.jsonl
    profile_updates.jsonl
    reports.jsonl
  logs/
    gateway.log
```

文件含义：

| 文件 | 说明 |
| --- | --- |
| `config.json` | self-log 应用配置，包括模型 profile、启用的消息通道和通道配置 |
| `state.json` | 运行状态快照 |
| `gateway.pid` | 后台 gateway 进程 PID |
| `data/entries.jsonl` | 原始记录，先保存用户输入 |
| `data/records.jsonl` | 结构化后的日志记录 |
| `data/pending_confirmations.jsonl` | 因信息不清楚而等待确认的记录 |
| `data/profile_updates.jsonl` | 模型建议沉淀的用户画像更新 |
| `data/reports.jsonl` | 生成过的周报、月报、年报 |
| `logs/gateway.log` | gateway 运行日志 |

## 4. 快速开始

初始化工作目录：

```bash
self-log init
```

记录一条原始日志：

```bash
self-log record "今天完成了 self-log 独立化，心情不错"
```

让模型整理待处理记录：

```bash
self-log process
```

查看结构化记录：

```bash
self-log view
self-log view --limit 50
```

生成周报：

```bash
self-log report weekly
```

查看状态：

```bash
self-log status
```

检查工作目录：

```bash
self-log doctor
```

## 5. CLI 命令详解

### 5.1 `init`

创建工作目录、配置文件和数据目录。

```bash
self-log init
self-log init --workspace /tmp/my-self-log
```

输出示例：

```text
Initialized self-log at /Users/yulin/.self-log
```

### 5.2 `config`

交互式配置模型 profile 和消息通道。

```bash
self-log config
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

写入一条原始记录到 `entries.jsonl`。

```bash
self-log record "今天看完了一篇 agent framework 的文章"
```

这一步只保存原始输入，不一定立刻生成结构化记录。后续用 `process` 让模型整理。

### 5.4 `list`

查看原始记录。

```bash
self-log list
self-log list --limit 100
```

输出格式：

```text
2026-05-16T10:00:00+00:00 [local] 今天看完了一篇 agent framework 的文章
```

### 5.5 `process`

处理待整理的原始记录：

```bash
self-log process
```

处理逻辑：

1. 找出还没有结构化的 `entries`。
2. 调用 OpenHarness 模型，把原始记录整理为结构化 JSON。
3. 如果模型返回 `records` 数组，则按多条日志批量入库。
4. 如果模型判断 `needs_clarification=true`，写入待确认文件。
5. 否则写入 `records.jsonl`。

模型整理出的字段包括：

- `corrected_content`
- `summary`
- `tags`
- `emotion`
- `emotion_reason`
- `related_people`
- `related_places`
- `suggested_profile_updates`

### 5.6 `view`

查看结构化日志。

```bash
self-log view
self-log view --limit 20
```

输出格式：

```text
2026-05-16 积极 [原始] [工作,成长] 完成 self-log 独立化
```

### 5.7 `report`

生成报告。

```bash
self-log report weekly
self-log report monthly
self-log report yearly
```

报告会基于 `records.jsonl` 中已经结构化的记录生成，并写入 `reports.jsonl`。

### 5.8 `status`

查看记录数量和 gateway 状态。

```bash
self-log status
```

输出示例：

```text
self-log: entries=12 | records=10 | pending=2 | gateway=stopped | path=/Users/yulin/.self-log/data
```

### 5.9 `doctor`

检查工作目录是否完整。

```bash
self-log doctor
```

输出示例：

```text
workspace: ok
data_dir: ok
logs_dir: ok
config: ok
state: ok
```

### 5.10 `start` / `stop`

启动或停止后台 gateway。

```bash
self-log start
self-log stop
```

指定运行目录和工作目录：

```bash
self-log start --cwd /Users/yulin/Github/OpenHarness --workspace ~/.self-log
self-log stop --workspace ~/.self-log
```

`--cwd` 是 gateway 进程的项目工作目录；`--workspace` 是 self-log 数据和配置目录。

### 5.11 `gateway run`

前台运行 gateway，适合调试：

```bash
self-log gateway run
```

后台运行请优先使用：

```bash
self-log start
```

## 6. 飞书接入

### 6.1 配置

运行：

```bash
self-log config
```

启用 `feishu` 后会要求输入：

| 字段 | 说明 |
| --- | --- |
| `allow_from` | 允许访问的用户或会话 ID。空列表表示拒绝所有来源，`*` 表示允许所有来源 |
| `app_id` | 飞书应用 ID |
| `app_secret` | 飞书应用密钥 |
| `encrypt_key` | 飞书事件订阅加密 key，可为空 |
| `verification_token` | 飞书事件订阅 token，可为空 |
| `react_emoji` | 收到消息后的反应表情，默认 `OK` |
| `bot_open_id` | bot open id，可留空自动检测 |
| `bot_names` | bot 名称关键字，默认包含 `self-log,openharness` |

当前 self-log 的飞书 `group_policy` 固定为 `open`，因为 self-log 是单用途记录应用，启动后该 channel 中收到的消息都会进入 self-log 语义路由。

### 6.2 启动

```bash
self-log start
```

查看状态：

```bash
self-log status
```

查看日志：

```bash
tail -f ~/.self-log/logs/gateway.log
```

### 6.3 在飞书里怎么用

直接发送日常记录：

```text
今天把 self-log 拆成独立 app 了，感觉方向更清晰
```

发送命令：

```text
/self-log help
/self-log process
/self-log view 10
/self-log report weekly
/self-log status
/self-log backfill 2026-05-15 昨天补录一个关键进展
```

粘贴复杂旧日记：

```text
下面是我之前的日记，请逐条入库：
4月18日
周六，去公司加班了
给图图买了枇杷
4月19日
去复查了，指标跟之前差不多
```

预期行为：

1. 模型理解这是批量导入。
2. 模型拆分为多条 records。
3. 工具调用 `self_log_import_records` 批量写入。
4. 每条记录写入 `records.jsonl`。

## 7. 模型与工具如何协作

self-log 的模型路由 agent 会看到一组 self-log 专用工具：

| 工具 | 用途 |
| --- | --- |
| `self_log_record` | 记录一条清楚的日志 |
| `self_log_import_records` | 批量导入模型拆分好的多条日志 |
| `self_log_clarify` | 信息不清楚时追问用户 |
| `self_log_process` | 整理待处理记录 |
| `self_log_backfill` | 补录缺失日期 |
| `self_log_report` | 生成周报、月报、年报 |
| `self_log_view` | 查看最近记录 |
| `self_log_status` | 查看状态 |
| `self_log_profile_update` | 记录高价值用户画像更新建议 |

重要约束：

- 模型负责理解自然语言和拆分记录。
- 工具负责执行和落盘。
- 不应靠复杂日期正则或固定格式假设解析人类输入。
- 不清楚的人物、关系、地点、事件含义应进入澄清流程，而不是猜测入库。

## 8. 配置文件示例

`~/.self-log/config.json` 示例：

```json
{
  "version": 1,
  "provider_profile": "codex",
  "enabled_channels": ["feishu"],
  "channel_configs": {
    "feishu": {
      "allow_from": ["ou_xxx"],
      "app_id": "cli_xxx",
      "app_secret": "xxx",
      "encrypt_key": "",
      "verification_token": "",
      "react_emoji": "OK",
      "group_policy": "open",
      "bot_open_id": "",
      "bot_names": "self-log,openharness"
    }
  },
  "send_progress": true,
  "send_tool_hints": true,
  "log_level": "INFO"
}
```

不建议手写密钥类字段到仓库文件中。`~/.self-log/config.json` 是本地运行配置，不应提交。

## 9. 数据格式说明

### 9.1 原始记录 `entries.jsonl`

每行一条 JSON：

```json
{
  "id": "abc123",
  "content": "今天完成了 self-log 独立化",
  "created_at": "2026-05-16T10:00:00+00:00",
  "channel": "feishu",
  "sender_id": "ou_xxx",
  "chat_id": "oc_xxx",
  "message_id": "om_xxx",
  "metadata": {}
}
```

### 9.2 结构化记录 `records.jsonl`

```json
{
  "id": "rec123",
  "entry_id": "abc123",
  "date": "2026-05-16",
  "raw_content": "今天完成了 self-log 独立化",
  "corrected_content": "今天完成了 self-log 独立化。",
  "summary": "完成 self-log 独立化",
  "tags": "工作,成长",
  "emotion": "积极",
  "emotion_reason": "完成了重要拆分",
  "related_people": "",
  "related_places": "",
  "source": "原始",
  "created_at": "2026-05-16T10:01:00+00:00"
}
```

### 9.3 待确认记录

当模型不确定某些信息时，会写入：

```json
{
  "id": "pending123",
  "entry_id": "abc123",
  "raw_content": "今天和小王聊了很久",
  "clarification_reason": "不知道小王是谁",
  "questions": ["小王是谁？他和你是什么关系？"],
  "created_at": "2026-05-16T10:01:00+00:00"
}
```

## 10. 常见工作流

### 10.1 本地命令行日记

```bash
self-log init
self-log record "今天把 README 写完了"
self-log process
self-log view
```

### 10.2 飞书手机记录

```bash
self-log config
self-log start
```

然后在飞书里直接发：

```text
今天看完一篇技术博客，里面说 When building is cheap, arguing is expensive，很认同
```

### 10.3 批量导入旧日记

把旧日记粘贴到飞书 self-log bot 或本地通过工具入口处理。关键是让模型使用 `self_log_import_records`，不要人为要求固定格式。

### 10.4 周期复盘

```bash
self-log process
self-log report weekly
self-log report monthly
```

## 11. 和 ohmo 的关系

self-log 是独立应用：

- 独立 package：`self_log`
- 独立 CLI：`self-log`
- 独立工作目录：`~/.self-log`
- 独立 gateway：`self-log start`

它不应该：

- 注册到普通 `ohmo` runtime。
- 写入 `~/.ohmo/gateway.json`。
- 依赖 `ohmo gateway` 拦截 `/self-log`。
- 把 self-log 逻辑塞进 `ohmo/self_log/__init__.py`。

如果需要在飞书中使用 self-log，请启动：

```bash
self-log start
```

而不是：

```bash
ohmo gateway start
```

## 12. 排障

### 12.1 `self-log process` 提示没有鉴权

检查 OpenHarness profile：

```bash
oh auth status
oh setup
```

或显式指定 profile：

```bash
self-log process --profile codex
```

### 12.2 飞书没有响应

检查：

```bash
self-log status
tail -f ~/.self-log/logs/gateway.log
```

确认：

- `self-log start` 已启动。
- `config.json` 中 `enabled_channels` 包含 `feishu`。
- 飞书 app 的 `app_id`、`app_secret`、事件订阅配置正确。
- `allow_from` 包含当前用户或会话，或临时设置为 `["*"]` 验证链路。

### 12.3 记录了但没有结构化

原始记录会先进入 `entries.jsonl`。运行：

```bash
self-log process
self-log view
```

### 12.4 批量日记没有逐条入库

理想路径是模型调用 `self_log_import_records`。如果没有逐条入库，优先检查模型调用日志或 gateway 日志，确认是否走了 self-log app，而不是普通聊天 bot。

### 12.5 停止 gateway

```bash
self-log stop
```

如果 PID 文件残留，可先查看：

```bash
cat ~/.self-log/gateway.pid
self-log status
```

不要用模糊的进程名批量杀进程，避免误伤其他服务。

## 13. 维护约定

开发时请保持以下边界：

- self-log 领域逻辑放在 `self_log/`。
- CLI 放在 `self_log/cli.py`。
- gateway 相关逻辑放在 `self_log/gateway/`。
- 模型 prompt 和 OpenHarness client 封装放在 `self_log/agent.py`。
- 数据模型放在 `self_log/models.py`。
- 落盘逻辑放在 `self_log/store.py`。
- 工具定义和工具执行放在 `self_log/tools.py`。
- 不要把 self-log 重新耦合进 `ohmo/gateway` 或普通 `ohmo` runtime。

建议验证命令：

```bash
uv run ruff check self_log tests/test_self_log
uv run pytest -q tests/test_self_log
uv run self-log --help
```
