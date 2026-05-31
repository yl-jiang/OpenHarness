"""AI quality gates for feed digest: scoring, filtering, dedup, clustering, synthesis."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.done_tool import DoneTool
from openharness.utils.log import get_logger

from feed_digest.models import FeedItem
from feed_digest.research import OpenCliCommand, RawEvidence, ResearchAction, ResearchDecision

logger = get_logger(__name__)

_EXTRACT_EVIDENCE_BATCH_SIZE = 1
_EXTRACT_MAX_ITEMS_PER_CALL = 8
_EXTRACT_MAX_CONCURRENT_CALLS = 4


class _JsonOutputInput(BaseModel):
    payload: str = Field(description="The final JSON payload for this feed digest task.")


class _MarkdownOutputInput(BaseModel):
    markdown: str = Field(description="The final Markdown content for this feed digest task.")


class _FeedDigestJsonOutputTool(BaseTool):
    name = "feed_digest_emit_json"
    description = (
        "Emit the final JSON result for the current feed digest task. "
        "Use this instead of plain assistant text."
    )
    input_model = _JsonOutputInput

    def __init__(self, *, expected: str) -> None:
        self._expected = expected

    def to_api_schema(self) -> dict[str, Any]:
        top_level = "object" if self._expected == "object" else "array"
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "payload": {
                        "type": "string",
                        "description": (
                            f"Valid JSON only. The top-level value must be a JSON {top_level}. "
                            "Do not wrap it in markdown fences or extra commentary."
                        ),
                    },
                },
                "required": ["payload"],
            },
        }

    def is_read_only(self, arguments: _JsonOutputInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: _JsonOutputInput, context: ToolExecutionContext) -> ToolResult:
        del context
        raw = arguments.payload.strip()
        if not raw:
            return ToolResult(output="payload must not be empty", is_error=True)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return ToolResult(output=f"payload must be valid JSON: {exc}", is_error=True)
        if self._expected == "object" and not isinstance(parsed, dict):
            return ToolResult(output="payload must be a JSON object", is_error=True)
        if self._expected == "list" and not isinstance(parsed, list):
            return ToolResult(output="payload must be a JSON array", is_error=True)
        return ToolResult(output=json.dumps(parsed, ensure_ascii=False))


class _FeedDigestMarkdownOutputTool(BaseTool):
    name = "feed_digest_emit_markdown"
    description = (
        "Emit the final Markdown result for the current feed digest task. "
        "Use this instead of plain assistant text."
    )
    input_model = _MarkdownOutputInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {
                        "type": "string",
                        "description": (
                            "Final Markdown only. Do not include explanations outside the markdown body."
                        ),
                    },
                },
                "required": ["markdown"],
            },
        }

    def is_read_only(self, arguments: _MarkdownOutputInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: _MarkdownOutputInput, context: ToolExecutionContext) -> ToolResult:
        del context
        markdown = arguments.markdown.strip()
        if not markdown:
            return ToolResult(output="markdown must not be empty", is_error=True)
        return ToolResult(output=markdown)


def _build_completion_registry(expected_json: str | None) -> tuple[ToolRegistry, str]:
    registry = ToolRegistry()
    if expected_json in {"object", "list"}:
        output_tool: BaseTool = _FeedDigestJsonOutputTool(expected=expected_json)
    else:
        output_tool = _FeedDigestMarkdownOutputTool()
    registry.register(output_tool)
    registry.register(DoneTool())
    return registry, output_tool.name


def _completion_system_prompt(system_prompt: str, *, expected_json: str | None) -> str:
    if expected_json in {"object", "list"}:
        expected_shape = "JSON object" if expected_json == "object" else "JSON array"
        contract = (
            "You are running inside the OpenHarness agent loop.\n"
            "Reasoning/thinking is allowed and should remain enabled.\n"
            f"When your result is ready, call `feed_digest_emit_json` with valid {expected_shape} content.\n"
            "Do not place the final JSON in normal assistant text.\n"
            "If the tool reports invalid JSON, fix it and retry.\n"
            "After `feed_digest_emit_json` succeeds, call `done` with a brief summary."
        )
    else:
        contract = (
            "You are running inside the OpenHarness agent loop.\n"
            "Reasoning/thinking is allowed and should remain enabled.\n"
            "When your result is ready, call `feed_digest_emit_markdown` with the final markdown body.\n"
            "Do not place the final markdown in normal assistant text.\n"
            "After `feed_digest_emit_markdown` succeeds, call `done` with a brief summary."
        )
    return f"{system_prompt.rstrip()}\n\n# Output Contract\n{contract}"


_SCORE_SYSTEM = """你是信息质量评审专家。给定一批新闻/文章/开源项目条目，你需要：
1. 为每个条目评分（relevance_score 和 signal_score，均为 0-1 的浮点数）
2. 判断是否为噪音（marketing, repost, low-quality）
3. 输出一个 JSON 数组，每个元素包含：
   - index: 原始顺序索引（从0开始）
   - relevance_score: 与目标领域相关性 [0,1]
   - signal_score: 信息价值 [0,1]
     * 对于开源项目：评技术价值、活跃度、star数量、社区欢迎程度、forkshu l和实用性（0.3以上即可入选）
     * 对于新闻/文章：评是否有新事实/新工具/新趋势
   - is_noise: 是否为营销稿/重复转载/完全无关内容（对知名开源项目不要标噪音）
   - noise_reason: 如果是噪音，说明原因（否则为空字符串）
   - importance_reason: 为什么值得关注（如果相关且有价值）

只输出 JSON 数组，不要其他内容。"""

_DEDUP_SYSTEM = """你是信息去重专家。给定一批已评分条目，找出报道**完全相同的具体事件**的重复条目。

去重标准（严格执行）：
- ✅ 应标为重复：多个来源报道的是**同一篇论文/同一次产品发布/同一个事故/同一条声明**（可视为同一新闻事件）
- ❌ 不应标为重复：仅话题领域相同（如"都是AI相关"）、同一话题的不同视角/评论/分析、不同时间段的进展报道

即使两条内容话题相近，只要它们报道的是不同的具体事件或有不同的实质内容，就**不要**标为重复。

对每个重复组，保留最权威/最详细的条目作为 canonical，其余标记为 duplicate_of（填 canonical 的 url）。

输出 JSON 数组，每个元素：
- url: 条目 URL
- canonical_url: 如果是重复则填 canonical URL，否则填自己的 URL
- cluster_id: 同主题组的唯一 ID（建议用 "cluster_0", "cluster_1" 等）
- cluster_title: 该主题组的简短标题

只输出 JSON 数组，不要其他内容。"""

_SYNTHESIS_SYSTEM = """你是信息整理专家。给定今日精选的信源条目，生成一份聚合报告。

简报格式（Markdown）：

# {title}

> 📅 {date} ｜ 覆盖 N 个数据源 · 聚合 N 条原始信息

---

## 🏆 跨源热度 TOP N

对多个条目进行跨源聚合分析，提炼出最重要的话题。每个话题：

### 1️⃣ 话题标题（必须包含具体人/事/数字）

*N个源提及 · 来源：XXX, YYY*

3-5句深度中文分析，综合多个来源的信息给出全面解读。

🔗 [相关链接1](url1) | [相关链接2](url2)

---

## 关键趋势

2-4个今日值得关注的趋势，每项用 - 开头

---

<sub>数据采集时间：{date} · 由 AI 自动聚合分析 · 仅展示高信号内容</sub>

话题标题规则（严格执行）：
- ❌ 错误（泛泛类别）：AI融资动态、AI安全事件、编程工具更新
- ✅ 正确（具体人/事/数字）：DeepSeek获大基金领投估值450亿美元、Claude Code沙箱逃逸CVE曝光、OpenAI发布GPT-5 Turbo
- 检验标准：读者不看正文，光看标题就能知道发生了什么新闻

要求：
- 根据条目的事实类型组织章节，不要依赖固定网站名称写死结构
- 不要出现采集失败、超时、错误等负面信息
- 宁可少而精，不要堆砌内容
- 输出纯 Markdown，不要 JSON"""

_RESEARCH_PLAN_SYSTEM = """你是资深信息研究员，负责主动使用 OpenCLI 采集高质量简报素材。

OpenCLI 工具使用规范：
- 命令格式：opencli <site> <command> [flags...]
- 查看某命令的完整选项：opencli <site> <command> --help
  示例：opencli hackernews top --help → 返回所有可用 flag 和格式
- 如不确定某命令支持哪些 flag，在 args 中填 ["--help"] 即可触发 help 查询；
  help 输出将作为 evidence 出现在下一轮，再据此规划正确参数
- args 规范：positional 参数直接填值，option 参数使用 --flag value 格式
  示例：["AI agents", "--limit", "10"] 或 ["--type", "renqi", "--limit", "20"]
- catalog 中 args 字段已列出每个命令支持的 flag（含类型和默认值），遇到疑惑时以 --help 为准

你会收到：
- 研究目标和领域
- 可用 OpenCLI catalog（site/command/strategy/browser/args 摘要/description）
- 已有 raw evidence 摘要（包括 --help 查询结果和实际采集内容）
- 本轮最多可发起的 action 数量

你的任务：
1. 根据领域和目标，自主判断哪些站点和命令最可能产出高信号信息
2. 如不确定某命令的 flag 格式，先规划 args=["--help"] 的 action 查询语法，再采集
3. 根据已有 evidence 发现信息缺口并补采
4. 如果信息已经足够，返回 done=true

约束：
- 只能使用 catalog 中列出的 site/command
- source 必须等于 site，便于稳定统计
- args 必须与该命令实际支持的 flag 严格匹配（从 catalog args 字段读取，或先 --help 查询）
- 优先 public adapter；如果目标页面没有专用 adapter，使用 web/read 抓取原始页面 Markdown：
  示例：采集 GitHub Trending → site=web, command=read, args=["--url", "https://github.com/trending", "--stdout", "true"]
- 每轮规划必须覆盖至少 3 个不同的 site，不要把全部 actions 都集中在同一个站点
- 首轮不要设置 done=true；在尚未覆盖足够多的信源时，也不要设置 done=true
- 不要为了数量重复采集同一 query
- 输出一个 JSON 对象，不要其他文本

JSON schema:
{
  "done": false,
  "rationale": "为什么继续或结束",
  "actions": [
    {
      "source": "同 site",
      "site": "catalog site",
      "command": "catalog command",
      "args": ["参数1", "--flag", "value"],
      "reason": "为什么执行这个命令"
    }
  ]
}
"""

_EXTRACT_SYSTEM = """你是信息抽取专家。给定 OpenCLI raw evidence，把其中所有真实、具体、可引用的新闻/项目/论文/讨论抽取成标准条目。

抽取原则：
- 穷举抽取 evidence 中所有有效条目，不做领域或相关性过滤（领域评分由下游独立完成）
- 只抽取 evidence 中明确出现的信息，不编造
- 过滤：纯导航链接、广告、空页面、完全重复的列表项
- 每个条目必须有 title 和 url；没有 url 的不要输出
- content 用 1-3 句说明核心事实
- source 使用 evidence source，不要发明新 source
- 输出 JSON 数组，不要其他文本

每个元素字段：
{
  "source": "来源",
  "title": "标题",
  "url": "https://...",
  "content": "核心事实摘要",
  "published_at": "ISO 时间，未知则空字符串",
  "author": "作者或来源，未知为空",
  "tags": ["可选标签"],
  "key_facts": ["可选事实"]
}
"""


class FeedDigestAIPipeline:
    """Multi-stage AI pipeline for feed digest quality gating."""

    def __init__(self, *, profile: str) -> None:
        self._profile = profile
        self._settings: Any = None
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        from openharness.config import load_settings
        from openharness.ui.runtime import _resolve_api_client_from_settings

        self._settings = load_settings().merge_cli_overrides(active_profile=self._profile)
        self._client = _resolve_api_client_from_settings(self._settings)

    async def _complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        reasoning_json_fallback: str | None = None,
    ) -> str:
        from openharness.config.settings import PermissionSettings
        from openharness.engine.query_engine import QueryEngine
        from openharness.engine.stream_events import AssistantTurnComplete, ToolExecutionCompleted
        from openharness.permissions.checker import PermissionChecker
        from openharness.permissions.modes import PermissionMode

        self._ensure_client()
        registry, output_tool_name = _build_completion_registry(reasoning_json_fallback)
        engine = QueryEngine(
            api_client=self._client,
            tool_registry=registry,
            permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
            cwd=Path.cwd(),
            model=self._settings.model,
            system_prompt=_completion_system_prompt(
                system_prompt,
                expected_json=reasoning_json_fallback,
            ),
            max_tokens=max_tokens,
            max_turns=6,
            require_explicit_done=True,
        )
        tool_output = ""
        final_text = ""
        async for event in engine.submit_message(user_prompt):
            if isinstance(event, ToolExecutionCompleted) and event.tool_name == output_tool_name and not event.is_error:
                tool_output = event.output.strip()
            elif isinstance(event, AssistantTurnComplete):
                candidate = event.message.text.strip()
                if candidate and not event.message.tool_uses:
                    final_text = candidate
        result = tool_output or final_text
        if not result:
            logger.warning(
                "_complete returned empty (model=%s provider=%s)",
                self._settings.model if self._settings else "unknown",
                self._settings.provider if self._settings else "unknown",
            )
        return result

    async def score_and_filter(
        self,
        items: list[FeedItem],
        *,
        domain: str,
        min_relevance: float,
        min_signal: float,
        min_per_source: int = 2,
        batch_size: int = 20,
    ) -> list[FeedItem]:
        """Score items for relevance and signal; return filtered list.

        Guarantees at least ``min_per_source`` items per source so that no
        active source is completely excluded from the digest.
        """
        if not items:
            logger.warning("score_and_filter called with empty items list")
            return []

        passed: list[FeedItem] = []   # above threshold, not noise
        pool: list[FeedItem] = []     # all scored items (including below threshold)

        for batch_start in range(0, len(items), batch_size):
            batch = items[batch_start : batch_start + batch_size]
            batch_text = "\n".join(
                f"[{i}] title={item.title!r}\nurl={item.url}\ncontent={item.content[:300]!r}"
                for i, item in enumerate(batch)
            )
            system = _SCORE_SYSTEM
            user = f"领域：{domain}\n\n条目列表：\n{batch_text}"
            try:
                raw = await self._complete(
                    system_prompt=system,
                    user_prompt=user,
                    reasoning_json_fallback="list",
                )
                scores = _parse_json_list(raw)
                scored_indices: set[int] = set()
                for entry in scores:
                    idx = int(entry.get("index", -1))
                    if idx < 0 or idx >= len(batch):
                        continue
                    scored_indices.add(idx)
                    item = batch[idx]
                    rel = float(entry.get("relevance_score") or 0)
                    sig = float(entry.get("signal_score") or 0)
                    is_noise = bool(entry.get("is_noise"))
                    scored = FeedItem(
                        source=item.source,
                        title=item.title,
                        url=item.url,
                        content=item.content,
                        published_at=item.published_at,
                        author=item.author,
                        domain=item.domain,
                        preset=item.preset,
                        score=(rel + sig) / 2,
                        summary=item.summary,
                        tags=item.tags,
                        key_facts=item.key_facts,
                        importance_reason=str(entry.get("importance_reason") or ""),
                        cluster_id=item.cluster_id,
                        cluster_title=item.cluster_title,
                        duplicate_of=item.duplicate_of,
                        metadata=item.metadata,
                    )
                    pool.append(scored)
                    if not is_noise and rel >= min_relevance and sig >= min_signal:
                        passed.append(scored)
                # Items the AI didn't score at all — add to pool with heuristic
                # scores so per-source guarantee can still draw from them.
                for i, item in enumerate(batch):
                    if i in scored_indices:
                        continue
                    pool.append(
                        FeedItem(
                            source=item.source,
                            title=item.title,
                            url=item.url,
                            content=item.content,
                            published_at=item.published_at,
                            author=item.author,
                            domain=item.domain,
                            preset=item.preset,
                            score=_heuristic_score(item),
                            summary=item.summary,
                            tags=item.tags,
                            key_facts=item.key_facts,
                            importance_reason=item.importance_reason,
                            cluster_id=item.cluster_id,
                            cluster_title=item.cluster_title,
                            duplicate_of=item.duplicate_of,
                            metadata=item.metadata,
                        )
                    )
            except Exception as exc:
                logger.warning("AI score batch failed: %s", exc)
                # Fallback: heuristic sort by star/points signal extracted from content
                for item in batch:
                    heuristic_score = _heuristic_score(item)
                    fallback = FeedItem(
                        source=item.source,
                        title=item.title,
                        url=item.url,
                        content=item.content,
                        published_at=item.published_at,
                        author=item.author,
                        domain=item.domain,
                        preset=item.preset,
                        score=heuristic_score,
                        summary=item.summary,
                        tags=item.tags,
                        key_facts=item.key_facts,
                        importance_reason=item.importance_reason,
                        cluster_id=item.cluster_id,
                        cluster_title=item.cluster_title,
                        duplicate_of=item.duplicate_of,
                        metadata=item.metadata,
                    )
                    passed.append(fallback)
                    pool.append(fallback)

        # Per-source guarantee: if a source has fewer than min_per_source items
        # in `passed`, supplement from pool (sorted by score, highest first).
        passed_urls = {it.url for it in passed}
        sources_in_pool: dict[str, list[FeedItem]] = {}
        for it in pool:
            sources_in_pool.setdefault(it.source, []).append(it)

        passed_per_source: dict[str, int] = {}
        for it in passed:
            passed_per_source[it.source] = passed_per_source.get(it.source, 0) + 1

        for source, source_items in sources_in_pool.items():
            count = passed_per_source.get(source, 0)
            if count >= min_per_source:
                continue
            need = min_per_source - count
            candidates = sorted(
                [it for it in source_items if it.url not in passed_urls],
                key=lambda x: x.score,
                reverse=True,
            )
            for it in candidates[:need]:
                passed.append(it)
                passed_urls.add(it.url)
                logger.info(
                    "Per-source guarantee: added %s item %r (score=%.2f)",
                    source, it.title[:40], it.score,
                )

        return sorted(passed, key=lambda x: x.score, reverse=True)

    async def plan_research_actions(
        self,
        *,
        objective: str,
        domain: str,
        catalog: list[OpenCliCommand],
        evidence: list[RawEvidence],
        max_actions: int,
    ) -> ResearchDecision:
        """Ask the model to choose the next bounded OpenCLI research actions."""
        catalog_text = _format_catalog_by_site(catalog)
        evidence_text = "\n".join(
            (
                f"- source={ev.source} failed={ev.failed} command={ev.command}\n"
                f"  error={ev.error[:200]}\n"
                f"  content={ev.content[:500]}"
            )
            for ev in evidence[-20:]
        )
        user = (
            f"Objective: {objective}\n"
            f"Domain: {domain}\n"
            f"Max actions this round: {max_actions}\n\n"
            f"OpenCLI catalog:\n{catalog_text}\n\n"
            f"Existing evidence:\n{evidence_text or '(none)'}"
        )
        raw = await self._complete(
            system_prompt=_RESEARCH_PLAN_SYSTEM,
            user_prompt=user,
            max_tokens=2048,
            reasoning_json_fallback="object",
        )
        payload = _parse_json_object(raw)
        raw_actions = payload.get("actions") if payload else _parse_research_action_entries(raw)
        logger.info(
            "Planned %d research actions (done=%s): %s",
            len(raw_actions),
            payload.get("done") if payload else "unknown",
            raw_actions,
        )
        actions: list[ResearchAction] = []
        for entry in raw_actions or []:
            if not isinstance(entry, dict):
                logger.warning("Skipping invalid research action entry (not a dict): %r", entry)
                continue
            site = str(entry.get("site") or "")
            command = str(entry.get("command") or "")
            raw_args = entry.get("args") or []
            if not site or not command or not isinstance(raw_args, list):
                logger.warning("Skipping invalid research action entry (missing site/command/args): %r", entry)
                continue
            logger.info(
                "Planned research action: site=%s command=%s args=%s reason=%s",
                site, command, raw_args, entry.get("reason") or "",
            )
            actions.append(
                ResearchAction(
                    source=site,
                    site=site,
                    command=command,
                    args=[str(arg) for arg in raw_args],
                    reason=str(entry.get("reason") or ""),
                )
            )
            if len(actions) >= max_actions:
                break
        return ResearchDecision(
            actions=actions,
            done=bool(payload.get("done")),
            rationale=str(payload.get("rationale") or ""),
        )

    async def extract_items_from_evidence(
        self,
        evidence: list[RawEvidence],
        *,
        domain: str,
        objective: str,
        max_items: int,
    ) -> list[FeedItem]:
        """Normalize raw OpenCLI evidence into FeedItem records through the model."""
        usable = [ev for ev in evidence if not ev.failed and ev.content.strip()]
        if not usable:
            logger.warning("No usable evidence to extract items from")
            return []

        items: list[FeedItem] = []
        seen: set[str] = set()
        batches = [
            usable[idx : idx + _EXTRACT_EVIDENCE_BATCH_SIZE]
            for idx in range(0, len(usable), _EXTRACT_EVIDENCE_BATCH_SIZE)
        ]
        semaphore = asyncio.Semaphore(_EXTRACT_MAX_CONCURRENT_CALLS)

        async def _extract_batch(batch: list[RawEvidence]) -> list[dict[str, Any]]:
            evidence_text = "\n\n".join(
                (
                    f"Evidence {idx}\n"
                    f"source={ev.source}\n"
                    f"command={ev.command}\n"
                    f"content:\n{ev.content[:4000]}"
                )
                for idx, ev in enumerate(batch)
            )
            async with semaphore:
                raw = await self._complete(
                    system_prompt=_EXTRACT_SYSTEM,
                    user_prompt=(
                        f"Max items: {_EXTRACT_MAX_ITEMS_PER_CALL}\n\n"
                        f"Raw evidence:\n{evidence_text}"
                    ),
                    max_tokens=4096,
                    reasoning_json_fallback="list",
                )
            return _parse_json_list(raw)

        batch_entries = await asyncio.gather(*[_extract_batch(batch) for batch in batches])
        for batch, entries in zip(batches, batch_entries, strict=True):
            evidence_by_source = {ev.source: ev for ev in batch}
            # batch_size=1 → every extracted item belongs to this one evidence source.
            # Normalise: LLM sometimes returns "Hacker News" instead of "hackernews".
            canonical_source = batch[0].source if batch else ""
            for entry in entries:
                source = str(entry.get("source") or "").strip()
                if source not in evidence_by_source:
                    source = canonical_source
                title = str(entry.get("title") or "").strip()
                url = str(entry.get("url") or "").strip()
                if not source or not title or not url or url in seen:
                    continue
                seen.add(url)
                ev = evidence_by_source.get(source)
                metadata = dict(entry.get("metadata") or {})
                metadata["evidence_command"] = ev.command if ev else ""
                items.append(
                    FeedItem(
                        source=source,
                        title=title,
                        url=url,
                        content=str(entry.get("content") or "").strip(),
                        published_at=str(entry.get("published_at") or "").strip(),
                        author=str(entry.get("author") or "").strip(),
                        domain=domain,
                        tags=[str(tag) for tag in entry.get("tags") or []],
                        key_facts=[str(fact) for fact in entry.get("key_facts") or []],
                        metadata=metadata,
                    )
                )
                if len(items) >= max_items:
                    break
            if len(items) >= max_items:
                break
        return items

    async def deduplicate(self, items: list[FeedItem]) -> list[FeedItem]:
        """Semantic dedup + clustering via AI."""
        if len(items) <= 1:
            return items
        items_text = "\n".join(
            f"url={item.url}\ntitle={item.title!r}\ncontent={item.content[:200]!r}"
            for item in items
        )
        try:
            raw = await self._complete(
                system_prompt=_DEDUP_SYSTEM,
                user_prompt=f"以下是待去重的条目列表：\n\n{items_text}",
                reasoning_json_fallback="list",
            )
            dedup_map = {entry["url"]: entry for entry in _parse_json_list(raw) if "url" in entry}
        except Exception as exc:
            logger.warning("AI dedup failed: %s — skipping dedup", exc)
            return items

        result: list[FeedItem] = []
        for item in items:
            info = dedup_map.get(item.url, {})
            canonical_url = info.get("canonical_url") or item.url
            duplicate_of = "" if canonical_url == item.url else canonical_url
            result.append(
                FeedItem(
                    source=item.source,
                    title=item.title,
                    url=item.url,
                    content=item.content,
                    published_at=item.published_at,
                    author=item.author,
                    domain=item.domain,
                    preset=item.preset,
                    score=item.score,
                    summary=item.summary,
                    tags=item.tags,
                    key_facts=item.key_facts,
                    importance_reason=item.importance_reason,
                    cluster_id=info.get("cluster_id") or "",
                    cluster_title=info.get("cluster_title") or "",
                    duplicate_of=duplicate_of,
                    metadata=item.metadata,
                )
            )
        return [item for item in result if not item.duplicate_of]

    async def synthesize(
        self,
        items: list[FeedItem],
        *,
        title: str,
        domain: str,
        max_items: int,
        max_trends: int,
        source_stats: list | None = None,
        total_candidates: int = 0,
    ) -> tuple[str, list[str]]:
        """Generate final Markdown digest and extract trend list."""
        if not items:
            return f"# {title}\n\n> 今日无高信号简报，未发现足够高质量内容。", []

        top = items[:max_items]

        # Build source stats summary for header
        num_sources = len(source_stats) if source_stats else 0
        total_raw = total_candidates or sum(s.fetched for s in (source_stats or []))

        parts: list[str] = []
        parts.append("精选条目：")
        parts.extend(
            f"- **{item.title}** [{item.source}]\n  URL: {item.url}\n  "
            f"重要性: {item.importance_reason or '待分析'}\n  摘要: {item.content[:400]}"
            for item in top
        )
        items_text = "\n".join(parts)

        user = (
            f"简报标题：{title}\n"
            f"领域：{domain}\n"
            f"覆盖数据源数量：{num_sources}\n"
            f"原始信息总量：{total_raw}\n"
            f"最多展示 {max_items} 个条目、{max_trends} 个趋势\n\n"
            f"精选条目：\n{items_text}"
        )
        try:
            markdown = await self._complete(
                system_prompt=_SYNTHESIS_SYSTEM,
                user_prompt=user,
                max_tokens=min(self._settings.max_tokens if self._settings else 4096, 4096),
            )
        except Exception as exc:
            logger.warning("AI synthesis failed: %s", exc)
            markdown = ""

        if not markdown.strip():
            logger.warning("AI synthesis returned empty — falling back to template")
            markdown = _fallback_markdown(title, top)

        trends: list[str] = []
        in_trends = False
        for line in markdown.splitlines():
            if "关键趋势" in line:
                in_trends = True
                continue
            if in_trends:
                if line.startswith("## "):
                    break
                if line.startswith("- "):
                    trends.append(line[2:].strip())
        return markdown, trends[:max_trends]


def _heuristic_score(item: FeedItem) -> float:
    """Score item by signals in content when AI scoring is unavailable."""
    import re
    content = item.content
    score = 0.1
    # GitHub: boost by star count
    stars_m = re.search(r"Stars:\s*(\d+)", content)
    if stars_m:
        stars = int(stars_m.group(1))
        score = min(0.9, 0.1 + stars / 50000)
    # HackerNews: boost by points
    points_m = re.search(r"Points:\s*(\d+)", content)
    if points_m:
        points = int(points_m.group(1))
        score = min(0.9, 0.1 + points / 500)
    return score


def _parse_json_list(raw: str) -> list[dict[str, Any]]:
    """Extract JSON array from model output."""
    raw = raw.strip()
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return _parse_partial_json_list(raw)


def _parse_partial_json_list(raw: str) -> list[dict[str, Any]]:
    """Parse complete object entries from a truncated JSON array."""
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    idx = raw.find("[")
    if idx < 0:
        return []
    idx += 1
    while idx < len(raw):
        while idx < len(raw) and raw[idx] in " \t\r\n,":
            idx += 1
        if idx >= len(raw) or raw[idx] == "]":
            break
        try:
            parsed, end = decoder.raw_decode(raw[idx:])
        except json.JSONDecodeError:
            break
        if isinstance(parsed, dict):
            items.append(parsed)
        idx += end
    return items


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Extract JSON object from model output."""
    raw = raw.strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_research_action_entries(raw: str) -> list[dict[str, Any]]:
    """Recover complete action objects from a truncated research-plan object."""
    decoder = json.JSONDecoder()
    entries: list[dict[str, Any]] = []
    for idx, char in enumerate(raw):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(raw[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("site") and parsed.get("command"):
            entries.append(parsed)
    return entries


def _format_catalog_by_site(catalog: list[OpenCliCommand]) -> str:
    """Compact per-site catalog: one line per site listing all commands.

    Required positional args are annotated with ``(arg*)`` so the model knows
    they are mandatory. Optional flags are intentionally omitted — the model
    should use ``args=["--help"]`` to discover those when needed.
    """
    by_site: dict[str, list[OpenCliCommand]] = {}
    for cmd in catalog:
        by_site.setdefault(cmd.site, []).append(cmd)

    lines: list[str] = []
    for site, cmds in sorted(by_site.items(), key=lambda kv: (kv[1][0].strategy != "public", kv[0])):
        strategy = cmds[0].strategy or "unknown"
        access = "browser" if cmds[0].browser else "no-browser"
        cmd_strs: list[str] = []
        for cmd in sorted(cmds, key=lambda c: c.name):
            required_positional = [
                arg["name"] for arg in cmd.args
                if arg.get("required") and arg.get("positional")
            ]
            if required_positional:
                cmd_strs.append(f"{cmd.name}({','.join(required_positional)}*)")
            else:
                cmd_strs.append(cmd.name)
        lines.append(f"- {site} [{strategy}, {access}]: {', '.join(cmd_strs)}")
    return "\n".join(lines)


def _fallback_markdown(title: str, items: list[FeedItem]) -> str:
    lines = [f"# {title}", ""]
    lines.extend(["## 🏆 精选条目", ""])
    for item in items:
        desc = item.content.split("\n")[0][:160].strip() if item.content else ""
        lines.append(f"**[{item.title}]({item.url})** [{item.source}]")
        if desc:
            lines.append(f"> {desc}")
        lines.append("")

    return "\n".join(lines)
