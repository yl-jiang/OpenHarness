# solo 使用说明书

`solo` 是一个独立的个人日志应用，运行在 OpenHarness 的模型、鉴权、消息通道和工具执行能力之上。它不依赖 `ohmo` gateway，也不会把日志能力注册进普通 `ohmo` 会话；`ohmo` 和 `solo` 的工作目录、配置、进程状态是分开的。

## 1. 它解决什么问题

`solo` 面向“现实世界里不规整的输入”：

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
uv run solo --help
```

如果想直接输入 `solo`，需要先让虚拟环境脚本进入 `PATH`：

```bash
source .venv/bin/activate
solo --help
```

也可以安装为可执行工具后直接使用：

```bash
uv tool install -e .
solo --help
```

也可以用 Python 模块方式启动：

```bash
python -m solo --help
```

下文命令示例为了简洁都写成 `solo ...`。如果你没有激活 `.venv` 或全局安装，请把示例中的 `solo` 替换成 `uv run solo`。

## 3. 工作目录

默认工作目录：

```text
~/.solo
```

可以通过两种方式覆盖：

```bash
solo --workspace /path/to/workspace ...
```

或设置环境变量：

```bash
export SOLO_WORKSPACE=/path/to/workspace
```

目录结构：

```text
~/.solo/
  config.json
  state.json
  HEARTBEAT.md
  soul.md
  user.md
  gateway.pid
  data/
    entries.jsonl
    records.jsonl
    pending_confirmations.jsonl
    profile_updates.jsonl
    reports.jsonl
  attachments/
    entries/
      <entry_id>/
        <copied source files>
  sessions/
    latest-<session_key_hash>.json
    session-<session_id>.json
  memory/
    MEMORY.md
    *.md
  logs/
    gateway.log
```

文件含义：

| 文件 | 说明 |
| --- | --- |
| `config.json` | solo 应用配置，包括模型 profile、启用的消息通道和通道配置 |
| `state.json` | 运行状态快照 |
| `HEARTBEAT.md` | 可选的周期任务清单；启用 heartbeat 后会随 app 状态一起被检查 |
| `soul.md` | 助手的人设、核心原则和行为准则 |
| `user.md` | 用户个人资料快照（姓名、职业、重要人物等） |
| `gateway.pid` | 后台 gateway 进程 PID |
| `data/` | 存储各类 jsonl 数据文件 |
| `attachments/` | 远程通道发来的图片/文件会复制到这里，供后续按 record 追溯原始材料；模型通过 `solo_view` / `solo_search` / `solo_show` 也能拿到这些路径 |
| `sessions/` | 会话快照；用于恢复远程频道上下文，也作为 auto-dream 的 session 输入 |
| `memory/` | 存储跨会话的长期记忆 markdown 文件 |
| `logs/` | gateway 运行日志 |

开启 OpenHarness `memory.auto_dream_enabled` 后，solo 会基于自己 workspace 下的 `memory/` 和 `sessions/` 参与 auto-dream。

## 4. 快速开始

初始化工作目录：

```bash
solo init
```

记录一条原始日志：

```bash
solo record "今天完成了 solo 独立化，心情不错"
```

让模型整理待处理记录：

```bash
solo process
```

查看结构化记录：

```bash
solo view
solo view --limit 50
solo show <record_id>
```

如果记录绑定了图片或文件，`solo view` / `solo search` 会显示附件摘要，`solo show <record_id>` 会给出可追溯的绝对存储路径；模型可继续对图片调用 `image_to_text`，对 UTF-8 文本附件调用 `read_file`。

生成周报：

```bash
solo report weekly
```

查看状态：

```bash
solo status
```

检查工作目录：

```bash
solo doctor
```

## 5. CLI 命令详解

### 5.1 `init`

创建工作目录、配置文件和数据目录。

```bash
solo init
solo init --workspace /tmp/my-solo
```

输出示例：

```text
Initialized solo at /Users/yulin/.solo
```

### 5.2 `config`

交互式配置模型 profile 和消息通道。

```bash
solo config
```

会依次配置：

- OpenHarness provider profile
- 是否启用 Telegram、Slack、Discord、Feishu
- 各通道的 token、app id、allow_from 等字段
- 是否发送 progress 和 tool hint
- 是否启用 heartbeat，以及 heartbeat 间隔

`provider_profile` 使用 OpenHarness 的 profile 名称，例如 `codex`。鉴权仍然复用 OpenHarness：

```bash
oh auth status
oh setup
```

### 5.3 `record`

写入一条原始记录到 `entries.jsonl`。

```bash
solo record "今天看完了一篇 agent framework 的文章"
```

这一步只保存原始输入，不一定立刻生成结构化记录。后续用 `process` 让模型整理。

### 5.4 `list`

查看原始记录。

```bash
solo list
solo list --limit 100
```

输出格式：

```text
2026-05-16T10:00:00+00:00 [local] 今天看完了一篇 agent framework 的文章
```

### 5.5 `process`

处理待整理的原始记录：

```bash
solo process
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
solo view
solo view --limit 20
```

输出格式：

```text
2026-05-16 积极 [原始] [工作,成长] 完成 solo 独立化
```

如果要查看某条结构化记录绑定的原始图片/文件、来源消息元数据和落盘路径，可以用：

```bash
solo show <record_id>
```

### 5.7 `report`

生成报告。

```bash
solo report weekly
solo report monthly
solo report yearly
```

报告会基于 `records.jsonl` 中已经结构化的记录生成，并写入 `reports.jsonl`。

### 5.8 `status`

查看记录数量和 gateway 状态。

```bash
solo status
```

输出示例：

```text
solo: entries=12 | records=10 | pending=2 | gateway=stopped | path=/Users/yulin/.solo/data
```

### 5.9 `doctor`

检查工作目录是否完整。

```bash
solo doctor
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
solo start
solo stop
```

指定运行目录和工作目录：

```bash
solo start --cwd /Users/yulin/Github/OpenHarness --workspace ~/.solo
solo stop --workspace ~/.solo
```

`--cwd` 是 gateway 进程的项目工作目录；`--workspace` 是 solo 数据和配置目录。

### 5.11 `heartbeat`

`solo` 的 heartbeat 是 app-local 的周期唤醒机制，只在 `solo start` / `solo gateway run` 运行时生效；它不依赖也不修改 OpenHarness 核心。默认关闭，启用后会定期汇总待确认记录、未完成个人待办和可选的 `HEARTBEAT.md` 任务，然后通过 solo agent 执行并投递到最近活跃的已启用消息通道。

```bash
solo heartbeat status
solo heartbeat trigger
```

`HEARTBEAT.md` 可以放在 solo workspace 根目录，例如 `~/.solo/HEARTBEAT.md`，用于补充需要周期检查的自然语言任务。

### 5.12 `gateway run`

前台运行 gateway，适合调试：

```bash
solo gateway run
```

后台运行请优先使用：

```bash
solo start
```

### 5.13 `onboard` (WebUI)

`solo` 内置了 onboard WebUI 子命令，可以启动统一 Web 仪表盘来浏览日志记录、查看统计、生成报告和实时聊天。Onboard 同时展示 solo 和 wolo 两个应用的数据。

```bash
# 前台启动
solo onboard run

# 后台启动
solo onboard start

# 查看状态
solo onboard status

# 停止
solo onboard stop
```

启动后终端输出 access token 和 direct link，在浏览器打开即可使用。详见 `onboard/README.md`。

## 6. 飞书接入

### 6.1 配置

运行：

```bash
solo config
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
| `bot_names` | bot 名称关键字，默认包含 `solo,openharness` |

当前 solo 的飞书 `group_policy` 固定为 `open`，因为 solo 是单用途记录应用，启动后该 channel 中收到的消息都会进入 solo 语义路由。

### 6.2 启动

```bash
solo start
```

查看状态：

```bash
solo status
```

查看日志：

```bash
tail -f ~/.solo/logs/gateway.log
```

### 6.3 在飞书里怎么用

直接发送日常记录：

```text
今天把 solo 拆成独立 app 了，感觉方向更清晰
```

发送命令：

```text
/solo help
/solo process
/solo view 10
/solo report weekly
/solo status
/solo backfill 2026-05-15 昨天补录一个关键进展
```

粘贴复杂旧日记：

```text
下面是我之前的日记，请逐条入库：
4月18日
周六，去公司加班了
给小朋友买了水果
4月19日
去检查了，结果跟之前差不多
```

预期行为：

1. 模型理解这是批量导入。
2. 模型拆分为多条 records。
3. 工具调用 `solo_import_records` 批量写入。
4. 每条记录写入 `records.jsonl`。

## 7. 模型与工具如何协作

solo 的模型路由 agent 会看到一组 solo 专用工具：

| 工具 | 用途 |
| --- | --- |
| `solo_record` | 记录一条清楚的日志 |
| `solo_import_records` | 批量导入模型拆分好的多条日志 |
| `solo_clarify` | 信息不清楚时追问用户 |
| `solo_process` | 整理待处理记录 |
| `solo_backfill` | 补录缺失日期 |
| `solo_report` | 生成周报、月报、年报 |
| `solo_view` | 查看最近记录 |
| `solo_search` | 语义/关键词搜索历史记录 |
| `solo_update_record` | 修改已入库记录的字段（如纠错） |
| `solo_delete_record` | 永久删除一条记录（需慎重使用） |
| `solo_status` | 查看状态 |
| `solo_get_now` | 获取当前日期时间、星期及本地时区信息 |
| `solo_profile_update` | 记录高价值用户画像更新建议 |
| `solo_remember` | 将长期稳定的用户背景信息写入 memory 目录 |
| `solo_suggest_reflection` | 基于最近记录生成深度复盘问题 |
| `solo_sync_context` | 同步外部上下文（日历、Git 等） |
| `solo_visualize` | 生成情绪分布图等可视化反馈 |
| `solo_export` | 导出日志为 Markdown 文件 |

重要约束：

- 模型负责理解自然语言和拆分记录。
- 工具负责执行和落盘。
- 不应靠复杂日期正则或固定格式假设解析人类输入。
- 不清楚的人物、关系、地点、事件含义应进入澄清流程，而不是猜测入库。

## 8. 配置文件示例

`~/.solo/config.json` 示例：

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
      "bot_names": "solo,openharness"
    }
  },
  "send_progress": true,
  "send_tool_hints": true,
  "heartbeat": {
    "enabled": false,
    "interval_s": 1800,
    "keep_recent_messages": 8
  },
  "log_level": "INFO"
}
```

不建议手写密钥类字段到仓库文件中。`~/.solo/config.json` 是本地运行配置，不应提交。

## 9. 数据格式说明

### 9.1 原始记录 `entries.jsonl`

每行一条 JSON：

```json
{
  "id": "abc123",
  "content": "今天完成了 solo 独立化",
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
  "raw_content": "今天完成了 solo 独立化",
  "corrected_content": "今天完成了 solo 独立化。",
  "summary": "完成 solo 独立化",
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
solo init
solo record "今天把 README 写完了"
solo process
solo view
```

### 10.2 飞书手机记录

```bash
solo config
solo start
```

然后在飞书里直接发：

```text
今天看完一篇技术博客，里面说 When building is cheap, arguing is expensive，很认同
```

### 10.3 批量导入旧日记

把旧日记粘贴到飞书 solo bot 或本地通过工具入口处理。关键是让模型使用 `solo_import_records`，不要人为要求固定格式。

### 10.4 周期复盘

```bash
solo process
solo report weekly
solo report monthly
```

## 11. 和 ohmo 的关系

solo 是独立应用：

- 独立 package：`solo`
- 独立 CLI：`solo`
- 独立工作目录：`~/.solo`
- 独立 gateway：`solo start`

它不应该：

- 注册到普通 `ohmo` runtime。
- 写入 `~/.ohmo/gateway.json`。
- 依赖 `ohmo gateway` 拦截 `/solo`。
- 把 solo 逻辑塞进 `ohmo/solo/__init__.py`。

如果需要在飞书中使用 solo，请启动：

```bash
solo start
```

而不是：

```bash
ohmo gateway start
```

## 12. 排障

### 12.1 `solo process` 提示没有鉴权

检查 OpenHarness profile：

```bash
oh auth status
oh setup
```

或显式指定 profile：

```bash
solo process --profile codex
```

### 12.2 飞书没有响应

检查：

```bash
solo status
tail -f ~/.solo/logs/gateway.log
```

确认：

- `solo start` 已启动。
- `config.json` 中 `enabled_channels` 包含 `feishu`。
- 飞书 app 的 `app_id`、`app_secret`、事件订阅配置正确。
- `allow_from` 包含当前用户或会话，或临时设置为 `["*"]` 验证链路。

### 12.3 记录了但没有结构化

原始记录会先进入 `entries.jsonl`。运行：

```bash
solo process
solo view
```

### 12.4 批量日记没有逐条入库

理想路径是模型调用 `solo_import_records`。如果没有逐条入库，优先检查模型调用日志或 gateway 日志，确认是否走了 solo app，而不是普通聊天 bot。

### 12.5 停止 gateway

```bash
solo stop
```

如果 PID 文件残留，可先查看：

```bash
cat ~/.solo/gateway.pid
solo status
```

不要用模糊的进程名批量杀进程，避免误伤其他服务。

## 13. 维护约定

开发时请保持以下边界：

- solo 领域逻辑放在 `solo/`。
- CLI 放在 `solo/cli.py`。
- gateway 相关逻辑放在 `solo/gateway/`。
- 模型 prompt 和 OpenHarness client 封装放在 `solo/agent.py`。
- 数据模型定义放在 `solo/models.py`。
- 业务流程编排（Processor）放在 `solo/processor.py`。
- 存储与持久化逻辑放在 `solo/store.py`。
- 工具定义与执行逻辑放在 `solo/tools.py`。
- 长期记忆（Memory）逻辑放在 `solo/memory.py`。
- 目录与路径管理放在 `solo/workspace.py`。
- 应用配置管理放在 `solo/config.py`。
- 不要把 solo 重新耦合进 `ohmo/gateway` 或普通 `ohmo` runtime。

建议验证命令：

```bash
uv run ruff check solo tests/test_solo
uv run pytest -q tests/test_solo
uv run solo --help
```
