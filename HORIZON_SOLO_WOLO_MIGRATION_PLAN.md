# Horizon 通用信源简报移植到 solo/wolo 的方案与执行计划

## 结论

Horizon 不应被理解成只能做“AI 热点新闻”的单一功能，而应作为一个可扩展的 **通用信源检索、收集、整理、归档、推送模式**：第一批默认 preset 可以是 AI 新闻，但模块边界必须支持后续扩展到开发者工具、产品、金融、行业情报、学术、开源项目动态等其他领域。

推荐新增独立的 **Feed Digest** 能力：默认随 `solo/wolo gateway run/start` 和 `solo/wolo onboard run/start` 集成启动；默认每天下午 9:30 左右按配置的领域和信源抓取内容，然后通过 AI client 做相关性评分、噪音过滤、跨源/语义去重、关键信息提炼、趋势整理和最终摘要，生成“少而精”的简报并归档。`gateway` 场景每次成功生成后推送到 IM；`onboard` 场景不通知，只在新的 Feed Digests 标签页展示归档。

Feed Digest 和 `solo`/`wolo` 本地生活/工作记录流必须分离。现有 todo reminder、heartbeat、report、daily question、工作/生活记录相关能力继续保持当前机制，不进入信源简报；后续如果需要个人生活日报或工作日报，应使用独立配置和独立 job。

## 已阅读到的关键事实

### Horizon

- 主流程是：确定时间窗口、抓取多源内容、跨源去重、AI 分析、按分数过滤、语义去重、补充背景、生成每日摘要、保存并推送。见 `../Horizon/src/orchestrator.py:50-190`。
- 外部源抓取是并发 `asyncio.gather`，覆盖 GitHub、Hacker News、RSS、Reddit、Telegram、Twitter、OpenBB、OSS Insight。见 `../Horizon/src/orchestrator.py:228-293`。
- 内容统一模型是 `ContentItem`，包含 source、title、url、content、published_at，以及 score/summary/tags。见 `../Horizon/src/models.py:22-40`。
- AI provider 配置独立维护，支持多 provider、语言、并发和限流。见 `../Horizon/src/models.py:56-72`。
- Webhook 配置支持 generic、Feishu/Lark、DingTalk、Slack、Discord、多 layout、多语言过滤。见 `../Horizon/src/models.py:228-293`。
- Horizon 的文件存储支持 `${VAR}` 环境变量展开、config、summary 和 subscribers。见 `../Horizon/src/storage/manager.py:15-151`。
- Webhook 推送构建 summary / overview / item 消息，并做平台级错误码检查。见 `../Horizon/src/services/webhook.py:440-775`。

### solo/wolo

- `solo` 是独立个人日志应用，工作目录、配置、进程状态和 `ohmo` 分离。见 `solo/README.md:3-19`、`solo/README.md:51-117`。
- `wolo` 是独立工作日志应用，仿照 `solo` 架构，但聚焦项目进展、会议、代码变更、prompt/tool 经验、blocker、风险、决策和 next action。见 `wolo/README.md:3-21`、`wolo/README.md:54-129`。
- 两者配置都已有 `provider_profile`、`enabled_channels`、`channel_configs`、`heartbeat` 和日志级别。见 `solo/core/models.py:13-32`、`wolo/core/models.py:13-32`。
- 两者 CLI 已挂 `gateway`、`heartbeat`、`onboard` 子命令，并支持 `telegram/slack/discord/feishu` 交互通道。见 `solo/cli.py:29-42`、`wolo/cli.py:29-42`。
- gateway 启动时创建 `MessageBus`、`ChannelManager`、bridge、heartbeat，并自动注册 todo cron 和启动 cron scheduler daemon。见 `solo/gateway/service.py:47-65`、`solo/gateway/service.py:118-154`、`solo/gateway/service.py:385-414`；`wolo/gateway/service.py:47-65`、`wolo/gateway/service.py:118-154`、`wolo/gateway/service.py:385-414`。
- cron scheduler 已有 app-local `cron_jobs.json`、`cron_history.jsonl`、PID 文件、one-shot reminder/agent_task、定时 command job 和 Feishu DM 通知。见 `solo/gateway/cron_scheduler.py:48-170`、`solo/gateway/cron_scheduler.py:251-339`；`wolo/gateway/cron_scheduler.py:48-170`、`wolo/gateway/cron_scheduler.py:251-339`。
- heartbeat 已经会收集 pending confirmation、due todo、cron failed、scheduler down、`HEARTBEAT.md` 任务；`wolo` 额外收集 blocker。见 `solo/gateway/heartbeat.py:85-315`、`wolo/gateway/heartbeat.py:89-315`。
- 两者处理器已能生成 report、daily question，并把 report 存入 store。见 `solo/processor.py:285-380`、`wolo/processor.py:275-371`。
- 数据层已有 entries、records、reports、todos 等持久化能力，且 `wolo` 额外有 decisions/highlights/experiments。见 `solo/core/store.py:357-445`、`wolo/core/store.py:396-520`。

## 领域边界

本方案把定时投送分成两条互不混合的流：

```text
Feed Digest flow
    configured external sources
        GitHub / Hacker News / RSS / Reddit / Telegram / Twitter / OpenBB / OSS Insight / future sources
    -> source collect
    -> cheap normalize and URL dedupe
    -> AI relevance scoring and noise filtering
    -> AI semantic dedupe and clustering
    -> AI key-fact extraction and trend synthesis
    -> compact domain-aware summary
    -> archive report_type="feed_digest" with domain/preset metadata
    -> gateway mode: push archived digest to IM
    -> onboard mode: show archived digest in Feed Digests tab

solo/wolo local task flow
    records / pending confirmations / todos / decisions / blockers / highlights
    -> existing solo/wolo processors, reports, heartbeat, todo cron, daily question
    -> existing schedules and config
```

关键约束：

- Feed Digest 不读取 `solo`/`wolo` records、entries、todos、decisions、blockers、highlights。
- 工作/生活记录不进入 Feed Digest 的排名、去重和摘要。
- 两条流可以各自配置启停、时间、推送目标和归档展示。
- 默认信源简报推送时间为 `21:30 Asia/Shanghai`，不占用现有 todo reminder、heartbeat 或 report schedule。
- `src/openharness` 不修改；OpenHarness 保持核心调度引擎和通用 agent infrastructure，不承载具体业务功能。

## 建设内容

构建一个复用 Horizon 流水线模式、但不锁死到单一新闻领域的通用信源简报能力：

```text
gateway run/start or onboard run/start
    |
    v
feed digest bootstrap
    |
    +--> config feed_digest.enabled
    +--> preset/domain/source config
    |
    v
app-local cron job at 21:30
    |
    v
feed digest runner
    collect -> normalize -> AI score/filter -> AI semantic dedupe -> AI extract/synthesize -> compact render -> archive final digest
    |
    +--> gateway mode: push archived final digest to IM
    |
    +--> onboard mode: expose archived final digest in Feed Digests tab
```

不新增后台 daemon；复用 `solo`/`wolo` 现有 app-local cron scheduler、cron history、heartbeat failure 监控和 IM 通道。

## 不建设内容

第一阶段不做以下事情：

- 不把外部信源简报和 `solo`/`wolo` 本地工作/生活记录混合摘要。
- 不把模块命名或模型锁死为 AI 新闻；AI 新闻只是默认 preset。
- 不改动 `src/openharness`，不向核心调度引擎加入信源业务功能。
- 不替换现有 todo reminder、heartbeat、report、daily question 等定时任务。
- 不移植 Email 订阅/退订和 SMTP/IMAP 轮询；`solo`/`wolo` 的远程入口已经由 gateway channel 承接。
- 不移植 GitHub Pages/Jekyll 输出；简报落在 app workspace 的 reports 归档和消息通道。
- 不移植 Horizon MCP server；`solo`/`wolo` 当前不需要把 feed digest stage 暴露给外部 MCP。
- 不复制 Horizon 的 AI provider client；统一复用 OpenHarness provider profile、鉴权和模型客户端。
- 不把 onboard 变成通知系统；onboard 只展示归档，不负责 IM 推送。

## 推荐方案

### 1. 信源简报能力用独立业务模块，不放进 `src/openharness`

推荐新增一个非核心的 top-level 业务包：

- `feed_digest/models.py`
- `feed_digest/sources.py`
- `feed_digest/engine.py`
- `feed_digest/ai_pipeline.py`
- `feed_digest/render.py`
- `feed_digest/config.py`
- `feed_digest/presets.py`

`solo`/`wolo` 只做集成层：

- `solo/feed_digest.py`
- `wolo/feed_digest.py`
- `solo/gateway/feed_digest_cron.py`
- `wolo/gateway/feed_digest_cron.py`

理由：信源抓取、去重、打分和渲染是独立业务域，不属于 OpenHarness 核心调度引擎，也不属于 `solo`/`wolo` 的个人/工作日志域。抽成 `feed_digest` 可以让两个 app 复用一套信源逻辑，同时为后续领域扩展保留空间。

Horizon 是 `feed_digest` 的首个模式来源和迁移参考，不是包名和能力上限。

### 2. 数据模型对齐“外部信源条目”，不是 app-local 记录

核心模型应保持领域中立：

- `FeedItem`：`source`、`domain`、`title`、`url`、`content`、`published_at`、`author`、`score`、`summary`、`tags`、`metadata`。
- `FeedDigestResult`：`date`、`domain`、`preset`、`items_count`、`selected_count`、`markdown`、`warnings`、`source_stats`。
- `FeedSource` protocol：每个信源实现 `collect(since, until, domain, query)`。
- `FeedPreset`：描述默认领域、默认 source、source weight、query、prompt profile 和输出标题。

不定义 records/todos/decisions/blockers 的 adapter。那些属于 `solo`/`wolo` 本地任务流。

### 3. 第一阶段 preset 和信源范围

第一阶段只内置一个默认 preset，但架构按多 preset 设计：

- `ai_news`：默认启用，面向 AI 相关热点新闻和技术动态。

第一阶段建议启用低风险、高价值、凭据要求少的源：

- GitHub trending/search 或 Horizon 现有 GitHub collector。
- Hacker News。
- RSS。
- OSS Insight 或其他无需用户额外登录的公开源。

Telegram、Twitter/Apify、OpenBB、Reddit 等源作为后续源插件，因为它们更依赖外部凭据、可用性和内容质量控制。

### 4. AI 降噪、评分、去重和提炼复用现有 app provider profile

不引入 Horizon 的 AI client。评分、去重、提炼、整理和摘要使用 `solo`/`wolo` 当前配置中的 `provider_profile`，通过现有 OpenHarness 模型能力执行：

- `solo` 用 `SoloConfig.provider_profile`。
- `wolo` 用 `WoloConfig.provider_profile`。
- prompt 由 preset 决定，不能混入“个人复盘”或“工作交接”语气。

Feed Digest 的核心目标是信息密度，不是覆盖数量；默认策略应是“宁可少推，不推噪音”。建议把 AI client 放在以下质量门控位置：

| 阶段 | 目的 | 输出 |
| --- | --- | --- |
| 相关性评分 | 判断条目是否匹配 preset/domain，过滤营销稿、重复转载、低信号内容 | `relevance_score`、`noise_reason` |
| 信息价值评分 | 判断是否有新事实、新趋势、新工具、新事件或高影响变化 | `signal_score`、`importance_reason` |
| 语义去重 | 合并同一事件的多源报道、重复 repo、重复讨论串 | `canonical_item_id`、`duplicate_of` |
| 主题聚类 | 把零散条目归并为少量主题，避免逐条流水账 | `cluster_id`、`cluster_title` |
| 关键事实抽取 | 从正文中抽取可验证事实、数字、项目名、时间、影响范围 | `key_facts` |
| 趋势提炼 | 从多个 cluster 中提炼趋势、分歧和真正值得关注的变化 | `trend_insights` |
| 最终压缩 | 控制输出长度，只保留最有价值的主题和条目 | Markdown 简报 |

AI 阶段必须有明确预算和阈值：

- 先用本地规则做 URL normalize、明显重复 URL 去重、空内容过滤，减少无谓模型调用。
- 再用 AI 对候选条目评分，低于 `min_signal_score` 或被判定为噪音的条目不进入最终摘要。
- 对同一事件的多源内容只保留一个 canonical item，在来源统计中记录被合并的来源。
- 最终简报默认只展示少量高价值主题，例如 3-5 个趋势、5-10 个重点条目；多余内容只进入归档 metadata，不推给用户。
- 每个被选中条目必须包含“为什么值得关注”，否则不应出现在最终消息里。
- 如果当天没有足够高质量内容，应生成“今日无高信号简报”并归档，而不是为了凑数推低质量内容。

输出结构建议保持通用：

```text
# 信源简报 YYYY-MM-DD

## 今日总览
...

## 最值得关注
...

## 关键趋势
...

## 为什么重要
...

## 条目
1. ...

## 来源统计
...
```

`ai_news` preset 可以把标题渲染为 `# AI 热点简报 YYYY-MM-DD`，但这是 preset 层展示，不是模块边界。

失败时不伪造内容，向 cron history 写 failed，并由现有 heartbeat/cron failure 机制暴露。

### 5. 定时与推送保持独立

新增 feed digest job：

- job name：`solo-feed-digest` / `wolo-feed-digest`
- 默认 schedule：`30 21 * * *`
- 默认 timezone：`Asia/Shanghai`
- report type：`feed_digest`
- metadata：写入 `preset`、`domain`、`sources`、`generated_by`
- gateway mode：每次成功生成后推送归档后的最终简报到 IM
- onboard mode：只归档，不推送

现有本地任务继续使用原来的 job，例如 todo reminder、heartbeat、report、daily question。feed job 不读取也不更新这些 job 的状态。

### 6. 配置使用独立 `feed_digest`

在 `SoloConfig` / `WoloConfig` 中新增：

```python
feed_digest: FeedDigestConfig
```

建议字段：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `True` | 是否注册信源简报 job |
| `schedule` | `"30 21 * * *"` | 简报推送 cron 表达式 |
| `timezone` | `"Asia/Shanghai"` | 调度时区 |
| `lookback_hours` | `24` | 信源扫描窗口 |
| `presets` | `["ai_news"]` | 启用的简报 preset；后续可扩展 |
| `default_preset` | `"ai_news"` | 第一阶段默认生成的 preset |
| `sources` | `["github", "hackernews", "rss"]` | 默认信源，可被 preset 覆盖 |
| `max_candidates` | `80` | 抓取后进入 AI 评分的候选上限 |
| `max_items` | `10` | 最终简报展示的最大条数，默认少而精 |
| `max_trends` | `5` | 最终简报展示的最大趋势数 |
| `min_relevance_score` | `0.65` | 与 preset/domain 相关性的最低阈值 |
| `min_signal_score` | `0.7` | 信息价值最低阈值 |
| `dedupe_similarity_threshold` | `0.86` | AI/embedding 语义去重阈值 |
| `allow_empty_digest` | `True` | 无高质量内容时归档空简报，不强行凑数 |
| `archive_enabled` | `True` | 是否归档最终简报 |
| `im_push_enabled` | `True` | gateway 场景是否推送到 IM；onboard 场景忽略 |

不要复用 `daily_digest` 这个名字，避免和本地生活/工作日报混淆。也不要使用只表达 AI 新闻的配置名，避免后续扩展被命名锁死。

### 7. 归档与 onboard 标签页

最终简报必须先归档，再用于 gateway 推送和 onboard 展示：

- `solo`：以 `report_type="feed_digest"` 写入 `SoloStore` reports，并在 metadata 中保存 `preset` / `domain`。
- `wolo`：以 `report_type="feed_digest"` 写入 `WoloStore` reports，并在 metadata 中保存 `preset` / `domain`。
- `period_start` / `period_end` 使用信源覆盖窗口。
- `content` 保存最终要给用户看的 Markdown；gateway IM 推送和 onboard 展示必须读取同一份归档内容。

onboard 新增独立标签页：

- 前端新增 `onboard/frontend/src/pages/FeedDigests.tsx`。
- Sidebar 新增 `Feed Digests` 导航项。
- 路由新增 `/feeds` 和 `/feeds/{id}`。
- API 层新增语义化方法 `api.feedDigests(app)`，底层请求 `/api/{app}/feed-digests`。
- 后端 routes 增加 `/feed-digests`、`/feed-digests/{id}`、`DELETE /feed-digests/{id}`，内部复用 reports 存储并限定 `report_type="feed_digest"`。
- 页面交互参考 `Reports.tsx`：列表按 period/date 倒序，支持按 preset/domain 过滤，点击打开 Markdown 内容，支持删除归档。

### 8. CLI 只保留调试/手动补跑入口

CLI 不是用户必须使用的入口。可以保留以下命令用于调试、补跑和测试：

```bash
solo feed-digest run --preset ai_news --date YYYY-MM-DD
wolo feed-digest run --preset ai_news --date YYYY-MM-DD
```

默认入口是：

- `solo gateway run/start`
- `wolo gateway run/start`
- `solo onboard run/start`
- `wolo onboard run/start`

## Horizon 能力映射表

| Horizon 能力 | 移植方式 | 是否第一阶段做 |
| --- | --- | --- |
| 多源 collect | 迁入 `feed_digest`，第一阶段启用 GitHub/HN/RSS 等低风险源 | 是 |
| `ContentItem` 统一模型 | 改为 `FeedItem`，保留 source/title/url/content/published_at/score/summary/tags，并增加 domain/preset metadata | 是 |
| URL 跨源去重 | 参考 Horizon cross-source duplicate 逻辑，保留 URL normalize | 是 |
| AI score/filter | 复用现有 provider profile 调模型，做相关性评分、信息价值评分和噪音过滤，prompt 由 preset 决定 | 是 |
| 语义去重和聚类 | 在 URL 去重后增加 AI/embedding 语义去重，合并同一事件的多源报道 | 是 |
| 关键事实抽取 | 从高信号条目抽取事实、数字、项目名、影响范围和“为什么重要” | 是 |
| Daily summary | 改为通用信源简报，按高信号主题压缩输出，归档 `report_type="feed_digest"` | 是 |
| Webhook delivery | 不搬复杂 webhook 模板；gateway 通过现有 IM 通道推送归档消息 | 部分 |
| Email subscription | 不移植 | 否 |
| GitHub Pages | 不移植 | 否 |
| MCP stage entry | 不移植 | 否 |
| 本地 records/todos/decisions/blockers 摘要 | 不属于信源简报迁移，保持 solo/wolo 当前机制 | 否 |

## 执行计划

### 阶段 1：建立通用信源业务模块和边界测试

修改文件：

- `feed_digest/__init__.py`
- `feed_digest/models.py`
- `feed_digest/sources.py`
- `feed_digest/engine.py`
- `feed_digest/ai_pipeline.py`
- `feed_digest/render.py`
- `feed_digest/config.py`
- `feed_digest/presets.py`
- `tests/test_feed_digest/test_engine.py`

步骤：

1. 定义 `FeedItem`、`FeedDigestResult`、`FeedDigestConfig`、`FeedPreset` 和 `FeedSource` protocol。
2. 实现 feed engine：source collect、URL normalize、候选截断、AI pipeline、Markdown render。
3. 实现 `feed_digest/ai_pipeline.py`：相关性评分、信息价值评分、噪音过滤、语义去重、主题聚类、关键事实抽取、趋势提炼和最终压缩。
4. 实现 `ai_news` 默认 preset，但 engine 和 AI pipeline 不依赖该 preset。
5. 明确 engine 输入只接受外部信源，不接受 `solo`/`wolo` records/todos/decisions/blockers。
6. 明确不新增、不修改任何 `src/openharness/**` 文件。

验证：

- 覆盖 collect、URL 去重、source stats、max_candidates、max_items、empty feed、source failure warning。
- 覆盖 AI 评分阈值、噪音过滤、语义重复合并、cluster 生成、key facts 抽取、趋势提炼和最终输出长度上限。
- 覆盖低质量候选全被过滤时生成“无高信号简报”，不强行凑数。
- 覆盖 preset 选择、domain metadata、source weight。
- 添加边界测试：records/todos 不能作为 FeedItem source 进入 engine。
- 运行 `uv run pytest -q tests/test_feed_digest`。

### 阶段 2：solo 信源简报集成

修改文件：

- `solo/core/models.py`
- `solo/config.py`
- `solo/feed_digest.py`
- `solo/gateway/feed_digest_cron.py`
- `solo/gateway/service.py`
- `solo/cli.py`
- `solo/README.md`
- `tests/test_solo/test_feed_digest.py`

步骤：

1. 新增 `SoloFeedDigestConfig`，挂到 `SoloConfig.feed_digest`。
2. `solo/feed_digest.py` 调用 `feed_digest` engine，使用 `SoloConfig.provider_profile` 驱动 AI pipeline 完成评分、去重、提炼和总结。
3. 生成最终 Markdown 后保存为 `report_type="feed_digest"`，metadata 写入 `preset`、`domain`、`sources`。
4. `solo gateway run/start` 默认注册 `solo-feed-digest`，成功后推送归档消息到 IM。
5. `solo onboard run/start` 在 `solo/cli.py` 调用 onboard server 前注册同一个 feed job，但不配置 IM 推送。
6. 不修改 solo 现有 todo reminder、heartbeat、report、daily question job。

验证：

- 覆盖 config enabled/disabled、默认 21:30 schedule、归档 report、metadata、gateway 推送、onboard 不推送。
- 覆盖本地 records/todos 不会进入简报摘要。
- 运行 `uv run pytest -q tests/test_solo/test_feed_digest.py tests/test_feed_digest`。

### 阶段 3：wolo 信源简报集成

修改文件：

- `wolo/core/models.py`
- `wolo/config.py`
- `wolo/feed_digest.py`
- `wolo/gateway/feed_digest_cron.py`
- `wolo/gateway/service.py`
- `wolo/cli.py`
- `wolo/README.md`
- `tests/test_wolo/test_feed_digest.py`

步骤：

1. 新增 `WoloFeedDigestConfig`，挂到 `WoloConfig.feed_digest`。
2. `wolo/feed_digest.py` 调用 `feed_digest` engine，使用 `WoloConfig.provider_profile` 驱动 AI pipeline 完成评分、去重、提炼和总结。
3. 生成最终 Markdown 后保存为 `report_type="feed_digest"`，metadata 写入 `preset`、`domain`、`sources`。
4. `wolo gateway run/start` 默认注册 `wolo-feed-digest`，成功后推送归档消息到 IM。
5. `wolo onboard run/start` 在 `wolo/cli.py` 调用 onboard server 前注册同一个 feed job，但不配置 IM 推送。
6. 不修改 wolo 现有 todo reminder、heartbeat、report、daily question、blocker 相关 job。

验证：

- 覆盖 config enabled/disabled、默认 21:30 schedule、归档 report、metadata、gateway 推送、onboard 不推送。
- 覆盖本地 records/todos/decisions/highlights/blockers 不会进入简报摘要。
- 运行 `uv run pytest -q tests/test_wolo/test_feed_digest.py tests/test_feed_digest`。

### 阶段 4：cron 注册与 IM 推送

修改文件：

- `solo/gateway/feed_digest_cron.py`
- `wolo/gateway/feed_digest_cron.py`
- `solo/gateway/service.py`
- `wolo/gateway/service.py`

步骤：

1. 实现 `ensure_feed_digest_job(app, workspace, mode, notify, schedule, timezone, preset)`，模式对齐 `ensure_todo_reminder_job`。
2. gateway 启动时，如果 `feed_digest.enabled` 为 true，就注册 feed job，并把 `im_push_enabled`、preset 和通知目标写入 job metadata。
3. 复用现有 scheduler daemon 启动逻辑，不新增 daemon。
4. job 执行 feed runner；runner 先归档最终 Markdown，再把同一份内容返回给 scheduler。
5. gateway mode 每次成功生成都推送到 IM；如果没有 IM notify target，job 仍归档并写 history，但把 warning 写入 history/heartbeat。
6. onboard mode 每次成功生成只归档，不推送。
7. 确认现有 todo reminder、heartbeat、report 等 job 不被改名、不被重排、不被 feed job 依赖。

验证：

- cron 注册测试：config enabled/disabled、gateway/onboard mode、已有 job 更新 schedule/timezone/preset、无 notify 不崩溃。
- scheduler smoke test：临时 workspace 执行一次 feed job，确认 report 归档、history success、gateway mode 触发 IM mock、onboard mode 不触发。
- 回归现有 todo cron/heartbeat 测试，确认不受 feed job 影响。
- 运行 `uv run pytest -q tests/test_solo tests/test_wolo tests/test_feed_digest`。

### 阶段 5：onboard Feed Digests 标签页

修改文件：

- `onboard/services/solo_service.py`
- `onboard/services/wolo_service.py`
- `onboard/api/solo_routes.py`
- `onboard/api/wolo_routes.py`
- `onboard/frontend/src/api/client.ts`
- `onboard/frontend/src/api/types.ts`
- `onboard/frontend/src/App.tsx`
- `onboard/frontend/src/components/Sidebar.tsx`
- `onboard/frontend/src/pages/FeedDigests.tsx`

步骤：

1. service 增加 `list_feed_digests()` / `get_feed_digest()` / `delete_feed_digest()`，内部复用 reports 并限定 `report_type="feed_digest"`。
2. routes 增加 `/feed-digests`、`/feed-digests/{id}`、`DELETE /feed-digests/{id}`。
3. frontend types 增加 `FeedDigest`，包含 `preset` 和 `domain` metadata。
4. 新增 `FeedDigests.tsx`，布局参考 `Reports.tsx`，但不提供“生成工作/生活报告”入口。
5. Sidebar 增加 `Feed Digests` 标签，App 增加 `/feeds` 路由。

验证：

- 添加 onboard service/route 测试：solo/wolo 均能列出 feed digest、按 preset/domain 过滤、打开详情、删除归档。
- 运行 `cd onboard/frontend && npm run build`。

### 阶段 6：文档和变更记录

修改文件：

- `CHANGELOG.md`
- `solo/README.md`
- `wolo/README.md`
- `onboard/README.md`

步骤：

1. 在 `CHANGELOG.md` 的 `[Unreleased]` 记录 `solo`/`wolo` 通用信源简报默认集成、归档、gateway IM 推送和 onboard Feed Digests 标签页。
2. README 写清：默认 21:30、使用 `feed_digest` 配置、默认 preset 是 `ai_news`、`gateway run/start` 默认推送到 IM、`onboard run/start` 默认归档但不通知。
3. README 明确：Feed Digest 和本地生活/工作记录是两条独立流；现有定时任务维持当前机制。
4. README 明确扩展方式：新增 preset、source、source weight、prompt profile 和页面过滤，不需要改 `src/openharness`。

验证：

- 运行 `uv run ruff check solo wolo feed_digest tests/test_solo tests/test_wolo tests/test_feed_digest`。
- 运行 `uv run pytest -q`。
- 运行 `cd onboard/frontend && npm run build`。

### 阶段 7：后续领域和信源扩展

第一阶段稳定后再扩展：

- 增加 preset：`developer_tools`、`product`、`finance`、`academic`、`open_source`。
- 增加更重的信源：Telegram、Twitter/Apify、OpenBB、Reddit、专业 RSS、内部知识源。
- 为不同 preset 配置不同 source weight、query、prompt profile 和推送标题。
- 支持同一天多个 preset 独立归档和推送，互不覆盖。

扩展规则：

1. 新 preset 必须显式配置启用。
2. 外部源失败不能导致整条简报失败，只进入 warnings。
3. 领域扩展仍不能读取 `solo`/`wolo` 本地 records/todos/decisions/blockers。
4. 新领域只新增 preset/source，不改 `src/openharness`。

## 测试路径

| 测试类型 | 覆盖内容 |
| --- | --- |
| feed_digest unit | collect、URL 去重、候选截断、source stats、empty feed、source warning |
| AI quality gate test | 相关性评分、信息价值评分、噪音过滤、语义去重、主题聚类、关键事实抽取、趋势提炼、输出长度上限 |
| low-signal test | 候选质量不足时生成“无高信号简报”，不为了凑数输出低质量条目 |
| preset test | `ai_news` 默认 preset、domain metadata、source weight、未来 preset 可插拔 |
| boundary test | records/todos/decisions/blockers 不进入 feed digest |
| solo integration | `feed_digest` config、默认 21:30、归档 `feed_digest`、metadata、gateway 推送、onboard 不推送 |
| wolo integration | `feed_digest` config、默认 21:30、归档 `feed_digest`、metadata、gateway 推送、onboard 不推送 |
| manual run test | `feed-digest run --preset ai_news --date` 作为补跑/调试入口可用，不是主入口 |
| cron test | job 注册、重复注册幂等、schedule/timezone/preset 更新、history success/failure、归档成功 |
| gateway test | gateway 启动时注册 feed job、成功后推送 IM、且不影响 todo reminder |
| onboard test | onboard 启动时注册 feed job、只归档不推送、Feed Digests 标签页可列出和打开归档 |
| failure test | 信源失败、模型失败、无 notify、Feishu 配置缺失、scheduler 未运行 |

## 回滚方案

- 运行时回滚：把 `feed_digest.enabled` 改为 `false`，重新启动 gateway/onboard；或删除 workspace 下 `data/cron_jobs.json` 中的 `solo-feed-digest` / `wolo-feed-digest` job。
- 推送回滚：保持 `feed_digest.enabled=true`，只把 `feed_digest.im_push_enabled` 改为 `false`，继续归档但停止 gateway IM 推送。
- 代码回滚：移除 `feed_digest`、`solo`/`wolo` feed 配置字段、feed cron 注册、feed runner 和 onboard Feed Digests 标签页。已生成的 `report_type="feed_digest"` 记录是普通 reports，不影响旧功能读取。
- 数据回滚：不需要迁移或删除已有 records/todos/decisions/highlights；Feed Digest 只新增 reports 和 cron history。

## 风险与应对

| 风险 | 应对 |
| --- | --- |
| 模块被再次锁死为 AI 新闻 | 包名、配置名、report type、job name、页面名全部使用 `feed_digest` / `Feed Digests`；`ai_news` 只作为 preset |
| 外部信源和本地记录被混合 | 配置名、report type、job name、页面名全部区别于 local reports，并增加边界测试 |
| 模型调用失败导致每日无推送 | cron history 写 failed，heartbeat 已会收集 failed cron jobs 并提醒 |
| 信源失败导致无内容 | 单源失败进入 warnings；多源全失败才标记 job failed |
| 简报变成信息堆砌 | 默认 `max_items=10`、`max_trends=5`，并要求每个条目提供“为什么重要”；低信号日允许空简报 |
| AI 评分不稳定 | 使用结构化输出、固定阈值、preset prompt 和单元测试 fixture；保留评分原因便于调试 |
| 模型成本过高 | 先用本地规则和候选上限降载，再对候选调用 AI；语义去重后再做最终总结 |
| gateway 推送内容和 onboard 展示内容不一致 | 强制先归档最终 Markdown，再让 gateway 推送同一份归档内容 |
| onboard 被误做成通知系统 | 明确 onboard 只展示归档；通知只发生在 gateway mode |
| Feishu DM 以外通道不能被 cron 直接通知 | 第一阶段明确只复用现有 IM notify target；第二阶段再提炼 shared notifier 接 ChannelManager |
| 外部信源不稳定 | 默认启用低风险公开源；高风险源放到后续显式配置 |
| 复制 Horizon 配置导致配置爆炸 | 不引入 Horizon 全量 config；只在 `SoloConfig` / `WoloConfig` 增加一个 `feed_digest` 节点 |

## 关键决策

1. **Horizon 提供通用模式，不定义上限**：第一阶段默认 `ai_news`，但模块设计为通用信源简报。
2. **Feed Digest 不是本地日报**：records、todos、decisions、blockers、highlights 保持在 solo/wolo 原有任务流。
3. **OpenHarness 核心零改动**：不新增 `src/openharness/daily_digest`、`src/openharness/news_digest` 或 `src/openharness/feed_digest`，不把具体信源功能放进核心调度引擎。
4. **信源业务独立成 `feed_digest`**：复用给 solo/wolo，避免复制两份 source、去重和 preset 逻辑。
5. **AI 质量门控是核心能力**：Feed Digest 必须用 AI client 做评分、噪音过滤、语义去重、事实抽取和趋势提炼；信息贵精不贵多。
6. **默认入口是 gateway/onboard 启动**：用户不需要单独运行 feed-digest 命令；命令只用于补跑和调试。
7. **信源简报定时独立**：默认 `21:30 Asia/Shanghai`，不复用也不改动现有 solo/wolo 定时任务。
8. **先归档再分发**：gateway IM 推送和 onboard Feed Digests 标签页必须展示同一份最终归档消息。
9. **第一阶段不做复杂 webhook**：gateway 复用当前 IM 通知路径，避免把 Horizon 的多平台模板系统整体搬入。

## 最脆弱假设

这个方案假设用户需要的是“外部信源简报平台”，Horizon 只是第一套可迁移的实现模式。如果后续目标变成“把个人/工作记录也做成每日生活/工作摘要”，应新增独立的 local digest 配置、job、归档类型和 onboard 页面，不应复用 `feed_digest`。

## 最小可行版本

只做以下内容即可上线一个可用版本：

1. `feed_digest`：接入 GitHub/HN/RSS，内置 `ai_news` preset，并通过 AI pipeline 完成评分、过滤、语义去重、关键事实抽取、趋势提炼和压缩总结。
2. `solo/feed_digest.py` / `wolo/feed_digest.py`：调用 `feed_digest`，保存 `report_type="feed_digest"` 归档，并写入 preset/domain metadata。
3. `solo/gateway/feed_digest_cron.py` / `wolo/gateway/feed_digest_cron.py`：每日 21:30 注册 feed job。
4. `gateway run/start` 默认注册并推送归档简报到 IM；`onboard run/start` 默认注册但只归档。
5. onboard 新增 `Feed Digests` 标签页，读取 `report_type="feed_digest"` 归档。

这个版本不混入工作/生活记录，不锁死 AI 新闻领域，不需要外部重凭据源，不需要 webhook 模板，不需要新 daemon，风险最低。
