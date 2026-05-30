"""AI quality gates for feed digest: scoring, filtering, dedup, clustering, synthesis."""
from __future__ import annotations

import json
import re
from typing import Any

from openharness.utils.log import get_logger

from feed_digest.models import FeedItem

logger = get_logger(__name__)

_SCORE_SYSTEM = """你是信息质量评审专家。给定一批新闻/文章/开源项目条目，你需要：
1. 为每个条目评分（relevance_score 和 signal_score，均为 0-1 的浮点数）
2. 判断是否为噪音（marketing, repost, low-quality）
3. 输出一个 JSON 数组，每个元素包含：
   - index: 原始顺序索引（从0开始）
   - relevance_score: 与目标领域相关性 [0,1]
   - signal_score: 信息价值 [0,1]
     * 对于开源项目：评技术价值、活跃度、明星数量和实用性（0.3以上即可入选）
     * 对于新闻/文章：评是否有新事实/新工具/新趋势
   - is_noise: 是否为营销稿/重复转载/完全无关内容（对知名开源项目不要标噪音）
   - noise_reason: 如果是噪音，说明原因（否则为空字符串）
   - importance_reason: 为什么值得关注（如果相关且有价值）

只输出 JSON 数组，不要其他内容。"""

_DEDUP_SYSTEM = """你是信息去重专家。给定一批已评分条目，找出报道同一事件/项目/主题的重复条目。
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

（如果有 GitHub 来源的条目，单独列出）
## 📦 GitHub Trending 项目

每个项目：
- **项目名** ⭐ Stars — 2-3句中文分析（技术价值、适用场景、社区活跃度）

---

（如果有 HuggingFace 来源的条目，单独列出）
## 📄 HuggingFace 热门论文

每篇论文：
- **论文标题** 👍 票数 — 100-200字中文深度总结（研究动机、方法创新、核心结论、实际意义）

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
- GitHub 项目和 HuggingFace 论文必须从精选条目中按 source 字段区分，单独成节
- 其余条目跨源聚合为热度 TOP 话题
- 不要出现采集失败、超时、错误等负面信息
- 宁可少而精，不要堆砌内容
- 输出纯 Markdown，不要 JSON"""


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
        self, *, system_prompt: str, user_prompt: str, max_tokens: int = 4096
    ) -> str:
        from openharness.api.client import (
            ApiMessageCompleteEvent,
            ApiMessageRequest,
            ApiReasoningDeltaEvent,
            ApiTextDeltaEvent,
        )
        from openharness.engine.messages import ConversationMessage

        self._ensure_client()
        request = ApiMessageRequest(
            model=self._settings.model,
            messages=[ConversationMessage.from_user_text(user_prompt)],
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            tools=[],
        )
        chunks: list[str] = []
        reasoning_parts: list[str] = []
        async for event in self._client.stream_message(request):
            if isinstance(event, ApiTextDeltaEvent):
                chunks.append(event.text)
            elif isinstance(event, ApiReasoningDeltaEvent):
                reasoning_parts.append(event.text)
            elif isinstance(event, ApiMessageCompleteEvent):
                final_text = event.message.text.strip()
                if final_text:
                    return final_text
        result = "".join(chunks).strip()
        if not result and reasoning_parts:
            result = "".join(reasoning_parts).strip()
            logger.info(
                "_complete using reasoning content as fallback (%d chars)",
                len(result),
            )
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
                raw = await self._complete(system_prompt=system, user_prompt=user)
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
        # Separate GitHub / HuggingFace items for dedicated sections
        github_items = [it for it in top if it.source == "github"]
        hf_items = [it for it in top if it.source == "huggingface"]
        other_items = [it for it in top if it.source not in ("github", "huggingface")]

        # Build source stats summary for header
        num_sources = len(source_stats) if source_stats else 0
        total_raw = total_candidates or sum(s.fetched for s in (source_stats or []))

        parts: list[str] = []
        if other_items:
            parts.append("跨源聚合条目：")
            parts.extend(
                f"- **{item.title}** [{item.source}]\n  URL: {item.url}\n  "
                f"重要性: {item.importance_reason or '待分析'}\n  摘要: {item.content[:300]}"
                for item in other_items
            )
        if github_items:
            parts.append("\nGitHub Trending 条目：")
            parts.extend(
                f"- **{item.title}** ⭐ {item.metadata.get('stars', '')}\n  URL: {item.url}\n  "
                f"摘要: {item.content[:300]}"
                for item in github_items
            )
        if hf_items:
            parts.append("\nHuggingFace 论文条目：")
            parts.extend(
                f"- **{item.title}** 👍 {item.metadata.get('upvotes', '')}\n  URL: {item.url}\n  "
                f"摘要: {item.content[:400]}"
                for item in hf_items
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
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _fallback_markdown(title: str, items: list[FeedItem]) -> str:
    github_items = [it for it in items if it.source == "github"]
    hf_items = [it for it in items if it.source == "huggingface"]
    other_items = [it for it in items if it.source not in ("github", "huggingface")]

    lines = [f"# {title}", ""]

    if other_items:
        lines.extend(["## 🏆 精选条目", ""])
        for item in other_items:
            desc = item.content.split("\n")[0][:120].strip() if item.content else ""
            lines.append(f"**[{item.title}]({item.url})** [{item.source}]")
            if desc:
                lines.append(f"> {desc}")
            lines.append("")

    if github_items:
        lines.extend(["---", "", "## 📦 GitHub Trending 项目", ""])
        for item in github_items:
            stars = item.metadata.get("stars", "")
            desc = item.content.split("\n")[0][:120].strip() if item.content else ""
            lines.append(f"- **[{item.title}]({item.url})** ⭐ {stars}")
            if desc:
                lines.append(f"  {desc}")
            lines.append("")

    if hf_items:
        lines.extend(["---", "", "## 📄 HuggingFace 热门论文", ""])
        for item in hf_items:
            upvotes = item.metadata.get("upvotes", "")
            desc = item.content.split("\n")[0][:200].strip() if item.content else ""
            lines.append(f"- **[{item.title}]({item.url})** 👍 {upvotes}")
            if desc:
                lines.append(f"  {desc}")
            lines.append("")

    return "\n".join(lines)
