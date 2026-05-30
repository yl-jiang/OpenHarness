"""Feed source implementations for feed digest collection."""
from __future__ import annotations

import asyncio
import html
import json
import re
import shlex
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from openharness.utils.log import get_logger

from feed_digest.models import FeedItem

logger = get_logger(__name__)

NEWSNOW_SOURCE_GROUPS: dict[str, list[str]] = {
    "ai_core": ["ithome", "github-trending-today", "hackernews", "producthunt"],
    "china_social": ["zhihu", "weibo", "bilibili-hot-search", "baidu", "toutiao"],
    "developer": ["v2ex-share", "juejin", "sspai", "coolapk", "nowcoder"],
    "ai_news": [
        "ithome",
        "github-trending-today",
        "hackernews",
        "producthunt",
        "zhihu",
        "weibo",
        "bilibili-hot-search",
        "v2ex-share",
        "juejin",
        "sspai",
        "coolapk",
        "nowcoder",
    ],
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_url(url: str) -> str:
    """Remove fragments and trailing slashes for dedup."""
    return url.split("#")[0].rstrip("/").lower()


def _clean_text(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _coerce_published_at(raw: Any) -> str:
    if raw is None:
        return _utcnow_iso()
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    text = str(raw).strip()
    if not text:
        return _utcnow_iso()
    if text.isdigit():
        return _coerce_published_at(int(text))

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    for fmt in ("%m/%d/%Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return text


def _build_query_terms(query: str, extra_terms: list[str] | None = None) -> list[str]:
    raw_terms: list[str] = []
    for part in re.split(r",|/|\||\s*&\s*|\s+and\s+|\s+or\s+", query, flags=re.IGNORECASE):
        cleaned = _clean_text(part)
        if not cleaned:
            continue
        raw_terms.append(cleaned)
        if " " in cleaned and len(cleaned.split()) <= 3:
            raw_terms.extend(piece for piece in cleaned.split() if len(piece) >= 3)
    if extra_terms:
        raw_terms.extend(_clean_text(term) for term in extra_terms if _clean_text(term))

    seen: set[str] = set()
    deduped: list[str] = []
    for term in raw_terms:
        lowered = term.lower()
        if len(lowered) < 2 or lowered in {"and", "or", "the", "for"}:
            continue
        if lowered not in seen:
            seen.add(lowered)
            deduped.append(term)
    return deduped


def _matches_query(text: str, query: str, extra_terms: list[str] | None = None) -> bool:
    haystack = _clean_text(text).lower()
    if not haystack:
        return False
    terms = _build_query_terms(query, extra_terms)
    if not terms:
        return True
    return any(term.lower() in haystack for term in terms)


class GitHubTrendingSource:
    """GitHub trending repos via HTML trending page with API fallback."""

    def __init__(
        self,
        *,
        prefer_html: bool = True,
        days: int = 7,
        min_stars: int = 50,
        query_terms: list[str] | None = None,
    ) -> None:
        self._prefer_html = prefer_html
        self._days = days
        self._min_stars = min_stars
        self._query_terms = query_terms or []

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del until
        try:
            if self._prefer_html:
                try:
                    items = await asyncio.to_thread(
                        self._fetch_trending_html,
                        domain,
                        query,
                        max_items,
                    )
                    if items:
                        return items[:max_items]
                except Exception as exc:
                    logger.warning("GitHub trending HTML fetch failed: %s", exc)

            queries = [q for q in query.split(",") if q.strip()][:3]
            if not queries:
                queries = [domain]
            items: list[FeedItem] = []
            for q in queries:
                url = (
                    "https://api.github.com/search/repositories"
                    f"?q={urllib.parse.quote(q)}+created:>{since[:10]}+stars:>{self._min_stars}"
                    "&sort=stars&order=desc&per_page=20"
                )
                try:
                    result = await asyncio.to_thread(_gh_fetch, url)
                    for repo in result.get("items") or []:
                        combined = (
                            f"{repo.get('full_name', '')} "
                            f"{repo.get('description', '')} "
                            f"{' '.join(repo.get('topics') or [])}"
                        )
                        if not _matches_query(combined, query, self._query_terms):
                            continue
                        pushed_at = repo.get("pushed_at") or _utcnow_iso()
                        items.append(
                            FeedItem(
                                source="github",
                                title=f"{repo.get('full_name', '')} - {repo.get('description', '')[:100]}",
                                url=str(repo.get("html_url", "")),
                                content=(
                                    f"{repo.get('description', '')}\n"
                                    f"Stars: {repo.get('stargazers_count', 0)} | "
                                    f"Language: {repo.get('language', 'N/A')} | "
                                    f"Topics: {', '.join(repo.get('topics') or [])[:200]}"
                                ),
                                published_at=pushed_at,
                                author=str(repo.get("owner", {}).get("login", "")),
                                domain=domain,
                                metadata={"stars": str(repo.get("stargazers_count", 0))},
                            )
                        )
                except Exception as exc:
                    logger.warning("GitHub fetch failed for query %r: %s", q, exc)
                    continue
            seen_urls: set[str] = set()
            deduped: list[FeedItem] = []
            for item in items:
                key = _normalize_url(item.url)
                if key not in seen_urls:
                    seen_urls.add(key)
                    deduped.append(item)
            return deduped[:max_items]
        except Exception as exc:
            logger.warning("GitHubTrendingSource.collect failed: %s", exc)
            return []

    def _fetch_trending_html(self, domain: str, query: str, max_items: int) -> list[FeedItem]:
        html_text = _simple_fetch_html(
            "https://github.com/trending?since=daily",
            headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        return self._parse_trending_html(html_text, domain=domain, query=query, max_items=max_items)

    def _parse_trending_html(
        self, html_text: str, *, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        items: list[FeedItem] = []
        seen: set[str] = set()
        for block in re.findall(
            r'<article[^>]*class="[^"]*\bBox-row\b[^"]*"[^>]*>([\s\S]*?)</article>',
            html_text,
        ):
            path_match = re.search(r'<a[^>]*href="/([^"/]+/[^"]+)"[^>]*class="Link"', block)
            if path_match is None:
                continue
            repo_path = path_match.group(1).strip()
            repo_name = repo_path.replace("/", " / ", 1)
            repo_url = f"https://github.com/{repo_path}"
            description_match = re.search(
                r'<p[^>]*class="[^"]*color-fg-muted[^"]*"[^>]*>([\s\S]*?)</p>',
                block,
            )
            description = _clean_text(description_match.group(1)) if description_match else ""
            combined = f"{repo_name} {description}"
            if not _matches_query(combined, query, self._query_terms):
                continue

            stars_match = re.search(
                rf'href="/{re.escape(repo_path)}/stargazers"[^>]*>([\s\S]*?)</a>',
                block,
            )
            stars = _clean_text(stars_match.group(1)) if stars_match else ""
            today_match = re.search(r"([\d,]+)\s+stars?\s+today", block, re.IGNORECASE)
            today_stars = today_match.group(1) if today_match else ""
            language_match = re.search(
                r'<span itemprop="programmingLanguage">([^<]+)</span>',
                block,
            )
            language = _clean_text(language_match.group(1)) if language_match else ""
            if repo_url in seen:
                continue
            seen.add(repo_url)
            content_parts = []
            if stars:
                content_parts.append(f"Stars: {stars}")
            if today_stars:
                content_parts.append(f"Today: {today_stars}")
            if language:
                content_parts.append(f"Language: {language}")
            content = " | ".join(content_parts)
            if description:
                content = f"{content}\n{description}" if content else description
            items.append(
                FeedItem(
                    source="github",
                    title=repo_name,
                    url=repo_url,
                    content=content,
                    published_at=_utcnow_iso(),
                    author=repo_path.split("/", 1)[0],
                    domain=domain,
                    metadata={"stars": stars, "today_stars": today_stars},
                )
            )
            if len(items) >= max_items:
                break
        return items


class HackerNewsSource:
    """Hacker News search/top/best stories via Algolia and Firebase APIs."""

    DEFAULT_MODE = "search"

    def __init__(
        self,
        *,
        mode: str = DEFAULT_MODE,
        query_terms: list[str] | None = None,
        stories_limit: int = 50,
    ) -> None:
        self._mode = mode
        self._query_terms = query_terms or []
        self._stories_limit = stories_limit

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until
        try:
            mode = self._mode.lower().strip()
            if mode == "search":
                return await self._collect_search(domain=domain, query=query, max_items=max_items)
            if mode in {"top", "best", "top_best"}:
                return await asyncio.to_thread(
                    self._collect_firebase,
                    mode,
                    domain,
                    query,
                    max_items,
                )
            logger.warning("Unknown HackerNewsSource mode %r, falling back to search", self._mode)
            return await self._collect_search(domain=domain, query=query, max_items=max_items)
        except Exception as exc:
            logger.warning("HackerNewsSource.collect failed: %s", exc)
            return []

    async def _collect_search(self, *, domain: str, query: str, max_items: int) -> list[FeedItem]:
        queries = [q.strip() for q in query.split(",") if q.strip()][:2]
        if not queries:
            queries = [domain]
        items: list[FeedItem] = []
        seen_urls: set[str] = set()
        for q in queries:
            url = (
                "https://hn.algolia.com/api/v1/search"
                f"?query={urllib.parse.quote(q)}&tags=story&hitsPerPage=20"
            )
            try:
                result = await asyncio.to_thread(_simple_fetch_json, url)
                for hit in result.get("hits") or []:
                    item = self._item_from_algolia_hit(hit, domain=domain, query=query)
                    if item is None:
                        continue
                    key = _normalize_url(item.url)
                    if key in seen_urls:
                        continue
                    seen_urls.add(key)
                    items.append(item)
            except Exception as exc:
                logger.warning("HN search fetch failed for query %r: %s", q, exc)
                continue
        return items[:max_items]

    def _collect_firebase(self, mode: str, domain: str, query: str, max_items: int) -> list[FeedItem]:
        endpoints = ["topstories", "beststories"] if mode == "top_best" else [f"{mode}stories"]
        items: list[FeedItem] = []
        seen_urls: set[str] = set()
        for endpoint in endpoints:
            try:
                story_ids = _simple_fetch_json(
                    f"https://hacker-news.firebaseio.com/v0/{endpoint}.json"
                )
            except Exception as exc:
                logger.warning("HN %s list fetch failed: %s", endpoint, exc)
                continue
            if not isinstance(story_ids, list):
                continue
            for story_id in story_ids[: self._stories_limit]:
                try:
                    raw_story = _simple_fetch_json(
                        f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
                    )
                except Exception as exc:
                    logger.warning("HN %s item %s fetch failed: %s", endpoint, story_id, exc)
                    continue
                item = self._item_from_firebase_story(
                    raw_story,
                    endpoint=endpoint,
                    domain=domain,
                    query=query,
                )
                if item is None:
                    continue
                key = _normalize_url(item.url)
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                items.append(item)
                if len(items) >= max_items:
                    return items
        return items

    def _item_from_algolia_hit(
        self,
        hit: dict[str, Any],
        *,
        domain: str,
        query: str,
    ) -> FeedItem | None:
        title = str(hit.get("title", ""))
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        story_text = str(hit.get("story_text") or "")
        if not title or not story_url:
            return None
        if not _matches_query(
            f"{title} {story_text}",
            query,
            self._query_terms,
        ):
            return None
        return FeedItem(
            source="hackernews",
            title=title,
            url=story_url,
            content=(
                f"Points: {hit.get('points', 0)} | "
                f"Comments: {hit.get('num_comments', 0)}\n"
                f"{story_text[:500]}"
            ),
            published_at=hit.get("created_at") or _utcnow_iso(),
            author=str(hit.get("author", "")),
            domain=domain,
            metadata={"hackernews_mode": "search"},
        )

    def _item_from_firebase_story(
        self,
        story: dict[str, Any] | None,
        *,
        endpoint: str,
        domain: str,
        query: str,
    ) -> FeedItem | None:
        if not isinstance(story, dict):
            return None
        title = str(story.get("title", "")).strip()
        if not title:
            return None
        text = _clean_text(str(story.get("text") or ""))
        if not _matches_query(f"{title} {text}", query, self._query_terms):
            return None
        item_id = story.get("id", "")
        story_url = str(story.get("url") or f"https://news.ycombinator.com/item?id={item_id}")
        return FeedItem(
            source="hackernews",
            title=title,
            url=story_url,
            content=(
                f"Points: {story.get('score', 0)} | "
                f"Comments: {story.get('descendants', 0)}\n"
                f"{text[:500]}"
            ),
            published_at=_coerce_published_at(story.get("time")),
            author=str(story.get("by", "")),
            domain=domain,
            metadata={"hackernews_mode": endpoint.replace("stories", "")},
        )


class RSSSource:
    """RSS/Atom feed source. Reads from configured RSS feed URLs."""

    DEFAULT_FEEDS = [
        "https://feeds.feedburner.com/oreilly/radar",
        "https://www.jiqizhixin.com/rss",
    ]

    def __init__(self, feed_urls: list[str] | None = None) -> None:
        self._feeds = feed_urls or self.DEFAULT_FEEDS

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until, query
        items: list[FeedItem] = []
        for feed_url in self._feeds[:3]:
            try:
                feed_items = await asyncio.to_thread(self._fetch_feed, feed_url, domain)
                items.extend(feed_items)
            except Exception as exc:
                logger.warning("RSS fetch failed for %r: %s", feed_url, exc)
                continue
        seen: set[str] = set()
        deduped: list[FeedItem] = []
        for item in items:
            key = _normalize_url(item.url)
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return deduped[:max_items]

    @staticmethod
    def _fetch_feed(url: str, domain: str) -> list[FeedItem]:
        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "OpenHarness-FeedDigest/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8", errors="replace")
            return RSSSource._parse_feed(content, domain)
        except Exception as exc:
            logger.warning("RSS _fetch_feed failed for %r: %s", url, exc)
            return []

    @staticmethod
    def _parse_feed(xml_content: str, domain: str) -> list[FeedItem]:
        """Very simple RSS/Atom parser using regex (avoids lxml dependency)."""
        items: list[FeedItem] = []
        entry_pattern = re.compile(
            r"<(?:item|entry)[^>]*>(.*?)</(?:item|entry)>",
            re.DOTALL | re.IGNORECASE,
        )
        for match in entry_pattern.finditer(xml_content):
            block = match.group(1)
            title = _extract_xml_text(block, "title")
            link = _extract_xml_text(block, "link") or _extract_xml_attr(block, "link", "href")
            summary = (
                _extract_xml_text(block, "summary")
                or _extract_xml_text(block, "description")
                or ""
            )
            pub_date = (
                _extract_xml_text(block, "pubDate")
                or _extract_xml_text(block, "published")
                or _utcnow_iso()
            )
            author = _extract_xml_text(block, "author") or ""
            if not title or not link:
                continue
            items.append(
                FeedItem(
                    source="rss",
                    title=title[:200],
                    url=link[:500],
                    content=re.sub(r"<[^>]+>", "", summary)[:800],
                    published_at=pub_date,
                    author=author[:100],
                    domain=domain,
                )
            )
        return items[:30]


class V2EXSource:
    """V2EX hot topics via public JSON API."""

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until, query
        try:
            data = await asyncio.to_thread(
                _simple_fetch_json, "https://www.v2ex.com/api/topics/hot.json"
            )
            items: list[FeedItem] = []
            for entry in (data or [])[:max_items * 2]:
                title = entry.get("title", "")
                url = entry.get("url", "")
                if not title or not url:
                    continue
                ts = entry.get("last_modified", 0)
                pub = (
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    if ts
                    else _utcnow_iso()
                )
                member = entry.get("member") or {}
                node = entry.get("node") or {}
                items.append(
                    FeedItem(
                        source="v2ex",
                        title=title,
                        url=url,
                        content=(
                            f"Replies: {entry.get('replies', 0)} | "
                            f"Node: {node.get('title', '')} | "
                            f"{(entry.get('content') or '')[:300]}"
                        ),
                        published_at=pub,
                        author=str(member.get("username", "")),
                        domain=domain,
                    )
                )
            return items[:max_items]
        except Exception as exc:
            logger.warning("V2EXSource.collect failed: %s", exc)
            return []


class HuggingFacePapersSource:
    """HuggingFace daily papers via public API."""

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until, query
        try:
            data = await asyncio.to_thread(
                _simple_fetch_json, "https://huggingface.co/api/daily_papers"
            )
            # Sort by upvotes descending before slicing
            entries = sorted(
                (data or []),
                key=lambda e: (e.get("paper") or {}).get("upvotes", 0),
                reverse=True,
            )
            items: list[FeedItem] = []
            for entry in entries[:max_items]:
                paper = entry.get("paper") or {}
                pid = paper.get("id", "") or entry.get("paper_id", "")
                title = paper.get("title", "")
                if not title or not pid:
                    continue
                upvotes = paper.get("upvotes", 0)
                abstract = (paper.get("abstract") or "")[:500]
                authors = ", ".join(
                    a.get("name", "") for a in (paper.get("authors") or [])[:3]
                )
                items.append(
                    FeedItem(
                        source="huggingface",
                        title=title,
                        url=f"https://huggingface.co/papers/{pid}",
                        content=f"Upvotes: {upvotes} | Authors: {authors}\n{abstract}",
                        published_at=entry.get("publishedAt") or _utcnow_iso(),
                        author=authors,
                        domain=domain,
                        metadata={"upvotes": str(upvotes)},
                    )
                )
            return items
        except Exception as exc:
            logger.warning("HuggingFacePapersSource.collect failed: %s", exc)
            return []


class ArXivSource:
    """ArXiv papers via Atom API, filtered by categories."""

    DEFAULT_CATEGORIES = ["cs.AI", "cs.CL", "cs.LG"]

    def __init__(self, categories: list[str] | None = None) -> None:
        self._categories = categories or self.DEFAULT_CATEGORIES

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until, query
        try:
            items = await asyncio.to_thread(self._fetch_arxiv, max_items)
            for item in items:
                item.domain = domain
            return items
        except Exception as exc:
            logger.warning("ArXivSource.collect failed: %s", exc)
            return []

    def _fetch_arxiv(self, max_items: int) -> list[FeedItem]:
        cat_query = "+OR+".join(f"cat:{c}" for c in self._categories)
        url = (
            f"https://export.arxiv.org/api/query?search_query={cat_query}"
            f"&sortBy=submittedDate&sortOrder=descending&max_results={max_items}"
        )
        import urllib.request as _req

        req = _req.Request(url, headers={"User-Agent": "OpenHarness-FeedDigest/1.0"})
        with _req.urlopen(req, timeout=20) as resp:
            xml_data = resp.read().decode("utf-8", errors="replace")

        root = ET.fromstring(xml_data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items: list[FeedItem] = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
            link = ""
            for link_el in entry.findall("atom:link", ns):
                if link_el.get("type") == "text/html":
                    link = link_el.get("href", "")
                    break
            summary_el = entry.find("atom:summary", ns)
            abstract = (summary_el.text or "").strip().replace("\n", " ")[:500] if summary_el is not None else ""
            pub_el = entry.find("atom:published", ns)
            published = pub_el.text or _utcnow_iso() if pub_el is not None else _utcnow_iso()
            author_names = [
                (a.find("atom:name", ns).text or "")
                for a in entry.findall("atom:author", ns)[:3]
                if a.find("atom:name", ns) is not None
            ]
            if not title or not link:
                continue
            items.append(
                FeedItem(
                    source="arxiv",
                    title=title,
                    url=link,
                    content=f"Authors: {', '.join(author_names)}\n{abstract}",
                    published_at=published,
                    author=", ".join(author_names),
                    domain="",
                )
            )
        return items[:max_items]


class Kr36Source:
    """36kr article scraper via HTML parsing."""

    DEFAULT_CATEGORY = "AI"

    def __init__(self, category: str = DEFAULT_CATEGORY) -> None:
        self._category = category

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until, query
        try:
            items = await asyncio.to_thread(self._fetch_36kr, max_items)
            for item in items:
                item.domain = domain
            return items
        except Exception as exc:
            logger.warning("Kr36Source.collect failed: %s", exc)
            return []

    def _fetch_36kr(self, max_items: int) -> list[FeedItem]:
        import urllib.request as _req

        url = f"https://36kr.com/information/{self._category}/"
        req = _req.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with _req.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        items: list[FeedItem] = []
        seen: set[str] = set()

        # Match article blocks inside <a> tags, then extract title + description
        for m in re.finditer(
            r'<a[^>]*href="(/p/\d+)"[^>]*>([\s\S]*?)</a>',
            html,
            re.IGNORECASE,
        ):
            path, block = m.group(1), m.group(2)
            title_match = re.search(
                r'<p[^>]*class="article-item-title[^"]*"[^>]*>([^<]+)</p>',
                block,
            )
            if not title_match:
                continue
            title = title_match.group(1).strip()
            if not title:
                continue

            desc_match = re.search(
                r'<p[^>]*class="article-item-desc[^"]*"[^>]*>([\s\S]*?)</p>',
                block,
            )
            if desc_match is None:
                desc_match = re.search(
                    r'<p[^>]*class="[^"]*(?:desc|summary|brief)[^"]*"[^>]*>([\s\S]*?)</p>',
                    block,
                    re.IGNORECASE,
                )
            desc = _clean_text(desc_match.group(1)) if desc_match else ""

            full_url = f"https://36kr.com{path}"
            if full_url not in seen:
                seen.add(full_url)
                items.append(
                    FeedItem(
                        source="36kr",
                        title=title,
                        url=full_url,
                        content=desc,
                        published_at=_utcnow_iso(),
                        author="36kr",
                        domain="",
                    )
                )

        # Fallback pattern (broader, no description)
        if not items:
            for m in re.finditer(r'href="(/p/\d+)"[^>]*>\s*([^<]{10,})\s*</a>', html):
                path, title = m.group(1), m.group(2).strip()
                full_url = f"https://36kr.com{path}"
                if full_url not in seen and title:
                    seen.add(full_url)
                    items.append(
                        FeedItem(
                            source="36kr",
                            title=title,
                            url=full_url,
                            content="",
                            published_at=_utcnow_iso(),
                            author="36kr",
                            domain="",
                        )
                    )

        return items[:max_items]


class ITHomeSource:
    """IT之家 AI/tag page scraper via HTML parsing."""

    DEFAULT_TAG = "AI"

    def __init__(self, tag: str = DEFAULT_TAG, query_terms: list[str] | None = None) -> None:
        self._tag = tag
        self._query_terms = query_terms or []

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until
        try:
            items = await asyncio.to_thread(self._fetch_tag_page, domain, query, max_items)
            for item in items:
                item.domain = domain
            return items
        except Exception as exc:
            logger.warning("ITHomeSource.collect failed: %s", exc)
            return []

    def _fetch_tag_page(self, domain: str, query: str, max_items: int) -> list[FeedItem]:
        tag_path = urllib.parse.quote(self._tag)
        html_text = _simple_fetch_html(f"https://www.ithome.com/tag/{tag_path}/")
        return self._parse_tag_html(html_text, domain=domain, query=query, max_items=max_items)

    def _parse_tag_html(
        self, html_text: str, *, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        items: list[FeedItem] = []
        seen: set[str] = set()
        for match in re.finditer(
            r'<li>\s*<a[^>]+href="([^"]+)"[^>]*class="img"[\s\S]*?'
            r'<div class="c"[^>]*data-ot="([^"]*)"[\s\S]*?'
            r'<a[^>]+class="title"[^>]*>([^<]+)</a>[\s\S]*?'
            r'<div class="m">([\s\S]*?)</div>',
            html_text,
        ):
            url, published_at, title, summary = match.groups()
            title = _clean_text(title)
            summary = _clean_text(summary)
            combined = f"{title} {summary}"
            if not _matches_query(combined, query, self._query_terms):
                continue
            normalized = _normalize_url(url)
            if normalized in seen:
                continue
            seen.add(normalized)
            items.append(
                FeedItem(
                    source="ithome",
                    title=title,
                    url=url,
                    content=summary,
                    published_at=_coerce_published_at(published_at),
                    author="IT之家",
                    domain=domain,
                )
            )
            if len(items) >= max_items:
                break
        return items


class NewsNowSource:
    """NewsNow source via its public JSON endpoint."""

    DEFAULT_BASE_URL = "https://newsnow.busiyi.world"
    DEFAULT_SOURCE_IDS = ["ithome", "github-trending-today", "hackernews", "v2ex-share"]

    def __init__(
        self,
        *,
        source_ids: list[str] | None = None,
        source_groups: list[str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        query_terms: list[str] | None = None,
        max_sources: int | None = 8,
    ) -> None:
        self._source_ids = self._resolve_source_ids(source_ids, source_groups)
        self._base_url = base_url.rstrip("/")
        self._query_terms = query_terms or []
        self._max_sources = max_sources

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until
        if not self._source_ids:
            return []

        source_ids = (
            self._source_ids[: self._max_sources]
            if self._max_sources is not None and self._max_sources > 0
            else self._source_ids
        )
        per_source = max(3, max_items // max(len(source_ids), 1) + 2)
        tasks = [
            asyncio.to_thread(self._fetch_one_source, source_id, domain, query, per_source)
            for source_id in source_ids
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        items: list[FeedItem] = []
        seen: set[str] = set()
        for result in raw_results:
            if isinstance(result, Exception):
                logger.warning("NewsNow source fetch failed: %s", result)
                continue
            for item in result:
                key = _normalize_url(item.url)
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
                if len(items) >= max_items:
                    return items
        return items

    def _fetch_one_source(
        self, source_id: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        url = f"{self._base_url}/api/s?id={urllib.parse.quote(source_id, safe='')}"
        payload = _simple_fetch_json(url)
        return self._parse_source_payload(
            payload, source_id=source_id, domain=domain, query=query, max_items=max_items
        )

    def _parse_source_payload(
        self,
        payload: Any,
        *,
        source_id: str,
        domain: str,
        query: str,
        max_items: int,
    ) -> list[FeedItem]:
        entries = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return []

        items: list[FeedItem] = []
        for entry in entries[: max_items * 3]:
            title = _clean_text(str(entry.get("title", "")))
            url = str(entry.get("url") or entry.get("mobileUrl") or "")
            if not title or not url:
                continue
            extra = entry.get("extra") or {}
            content_parts: list[str] = [f"NewsNow source: {source_id}"]
            hover = _clean_text(str(extra.get("hover") or ""))
            info = _clean_text(str(extra.get("info") or ""))
            summary = _clean_text(
                str(entry.get("summary") or entry.get("description") or entry.get("desc") or "")
            )
            if info:
                content_parts.append(info)
            if hover:
                content_parts.append(hover)
            if summary:
                content_parts.append(summary)
            content = "\n".join(content_parts)
            if not _matches_query(f"{title} {content}", query, self._query_terms):
                continue
            items.append(
                FeedItem(
                    source="newsnow",
                    title=title,
                    url=url,
                    content=content,
                    published_at=_coerce_published_at(
                        entry.get("pubDate") or payload.get("updatedTime")
                    ),
                    author=str(entry.get("author") or source_id),
                    domain=domain,
                    metadata={"newsnow_source_id": source_id},
                )
            )
            if len(items) >= max_items:
                break
        return items

    @staticmethod
    def _resolve_source_ids(
        source_ids: list[str] | None,
        source_groups: list[str] | None,
    ) -> list[str]:
        requested = list(source_ids or [])
        for group in source_groups or []:
            requested.extend(NEWSNOW_SOURCE_GROUPS.get(group, []))
        if not requested:
            requested = list(NewsNowSource.DEFAULT_SOURCE_IDS)
        deduped: list[str] = []
        seen: set[str] = set()
        for source_id in requested:
            normalized = str(source_id).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(normalized)
        return deduped


class SoPilotSource:
    """SoPilot hot tweets parser via RSS with HTML fallback."""

    DEFAULT_URL = "https://sopilot.net/zh/hot-tweets"
    DEFAULT_RSS_URL = "https://sopilot.net/rss/hottweets"
    _CARD_MARKER = (
        '<div class="rounded-lg border bg-card text-card-foreground '
        'shadow-sm hover:shadow-lg transition-shadow duration-200">'
    )

    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        rss_url: str = DEFAULT_RSS_URL,
        prefer_rss: bool = True,
        query_terms: list[str] | None = None,
    ) -> None:
        self._url = url
        self._rss_url = rss_url
        self._prefer_rss = prefer_rss
        self._query_terms = query_terms or []

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until
        try:
            if self._prefer_rss and self._rss_url:
                try:
                    rss_text = await asyncio.to_thread(_simple_fetch_html, self._rss_url)
                    rss_items = self._parse_rss_feed(
                        rss_text,
                        domain=domain,
                        query=query,
                        max_items=max_items,
                    )
                    if rss_items:
                        return rss_items
                except Exception as exc:
                    logger.warning("SoPilot RSS fetch failed: %s", exc)

            html_text = await asyncio.to_thread(_simple_fetch_html, self._url)
            return self._parse_hot_tweets_html(
                html_text, base_url=self._url, domain=domain, query=query, max_items=max_items
            )
        except Exception as exc:
            logger.warning("SoPilotSource.collect failed: %s", exc)
            return []

    def _parse_rss_feed(
        self,
        xml_content: str,
        *,
        domain: str,
        query: str,
        max_items: int,
    ) -> list[FeedItem]:
        entries = RSSSource._parse_feed(xml_content, domain)
        items: list[FeedItem] = []
        for entry in entries:
            combined = f"{entry.title} {entry.content}"
            if not _matches_query(combined, query, self._query_terms):
                continue
            title_match = re.match(r"^(.*?)\s+\(@([^)]*)\)$", entry.title)
            author = title_match.group(1).strip() if title_match else entry.author or entry.title
            handle = title_match.group(2).strip() if title_match else ""
            items.append(
                FeedItem(
                    source="sopilot",
                    title=entry.title,
                    url=entry.url,
                    content=entry.content,
                    published_at=entry.published_at,
                    author=author,
                    domain=domain,
                    metadata={"sopilot_handle": handle, "sopilot_rss": True},
                )
            )
            if len(items) >= max_items:
                break
        return items

    def _parse_hot_tweets_html(
        self, html_text: str, *, base_url: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        items: list[FeedItem] = []
        for block in html_text.split(self._CARD_MARKER)[1:]:
            if "/hot-tweets?tweetId=" not in block or "https://x.com/" not in block:
                continue

            detail_match = re.search(r'href="(/hot-tweets\?tweetId=\d+)"', block)
            author_match = re.search(r'href="(https://x\.com/[^"]+)">([\s\S]*?)</a>', block)
            handle_match = re.search(r"@<!-- -->([^<]+)</span>", block)
            text_match = re.search(
                r'<p class="mt-1 text-sm [^"]*?whitespace-pre-wrap[^"]*">([\s\S]*?)</p>',
                block,
            )
            if detail_match is None or author_match is None or text_match is None:
                continue

            detail_url = urllib.parse.urljoin(base_url, detail_match.group(1))
            author_url, author_name = author_match.groups()
            author_name = _clean_text(author_name)
            handle = _clean_text(handle_match.group(1)) if handle_match else ""
            tweet_text = _clean_text(text_match.group(1))
            if not _matches_query(
                f"{author_name} {handle} {tweet_text}",
                query,
                self._query_terms,
            ):
                continue

            metric_values = re.findall(
                r'<div class="flex items-center gap-1 text-gray-500 dark:text-gray-400">'
                r'[\s\S]*?<span class="text-sm">([^<]+)</span>',
                block,
            )
            likes, reposts, replies, views = (metric_values + ["", "", "", ""])[:4]

            def _extract_label(label: str) -> str:
                label_match = re.search(
                    rf">{label}</div><div[^>]*>([^<]+)</div>",
                    block,
                )
                return _clean_text(label_match.group(1)) if label_match else ""

            speed = _extract_label("爆速")
            probability = _extract_label("起爆概率")
            forecast = _extract_label("预测浏览量")
            published_match = re.search(
                r'<span class="text-xs text-gray-500 dark:text-gray-400" title="([^"]+)">',
                block,
            )
            published_at = _coerce_published_at(
                published_match.group(1) if published_match else _utcnow_iso()
            )

            content_parts = []
            if handle:
                content_parts.append(f"Handle: @{handle}")
            metric_parts = []
            if likes:
                metric_parts.append(f"Likes: {likes}")
            if reposts:
                metric_parts.append(f"Reposts: {reposts}")
            if replies:
                metric_parts.append(f"Replies: {replies}")
            if views:
                metric_parts.append(f"Views: {views}")
            if speed:
                metric_parts.append(f"Velocity: {speed}")
            if probability:
                metric_parts.append(f"Probability: {probability}")
            if forecast:
                metric_parts.append(f"Forecast: {forecast}")
            if metric_parts:
                content_parts.append(" | ".join(metric_parts))
            content_parts.append(tweet_text)

            items.append(
                FeedItem(
                    source="sopilot",
                    title=f"{author_name}: {tweet_text[:80]}",
                    url=detail_url,
                    content="\n".join(content_parts),
                    published_at=published_at,
                    author=author_name or author_url,
                    domain=domain,
                    metadata={"sopilot_author_url": author_url, "sopilot_handle": handle},
                )
            )
            if len(items) >= max_items:
                break
        return items


class BrowserCommandSource:
    """Optional browser/OpenCLI-backed source via a configured JSON-producing command."""

    def __init__(
        self,
        *,
        command: str | list[str] | None = None,
        timeout_seconds: int = 60,
        source_name: str = "browser",
        query_terms: list[str] | None = None,
    ) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds
        self._source_name = source_name
        self._query_terms = query_terms or []

    async def collect(
        self, *, since: str, until: str, domain: str, query: str, max_items: int
    ) -> list[FeedItem]:
        del since, until
        if not self._command:
            logger.warning("BrowserCommandSource is configured without a command")
            return []
        try:
            payload = await asyncio.to_thread(self._run_command_json)
        except Exception as exc:
            logger.warning("BrowserCommandSource.collect failed: %s", exc)
            return []

        entries = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return []

        items: list[FeedItem] = []
        seen: set[str] = set()
        for entry in entries:
            item = self._coerce_item(entry, domain=domain, query=query)
            if item is None:
                continue
            key = _normalize_url(item.url)
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= max_items:
                break
        return items

    def _run_command_json(self) -> Any:
        argv = self._command if isinstance(self._command, list) else shlex.split(str(self._command))
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout).strip()
            raise RuntimeError(stderr or f"command exited with {result.returncode}")
        stdout = (result.stdout or "").strip()
        if not stdout:
            return []
        return json.loads(stdout)

    def _coerce_item(self, entry: Any, *, domain: str, query: str) -> FeedItem | None:
        if not isinstance(entry, dict):
            return None
        title = _clean_text(str(entry.get("title", "")))
        url = str(entry.get("url") or "")
        if not title or not url:
            return None
        content = _clean_text(
            str(
                entry.get("content")
                or entry.get("summary")
                or entry.get("description")
                or entry.get("text")
                or ""
            )
        )
        if not _matches_query(f"{title} {content}", query, self._query_terms):
            return None
        metadata = dict(entry.get("metadata") or {})
        metadata.setdefault("browser_command_source", self._source_name)
        return FeedItem(
            source=self._source_name,
            title=title,
            url=url,
            content=content,
            published_at=_coerce_published_at(entry.get("published_at") or entry.get("publishedAt")),
            author=_clean_text(str(entry.get("author") or "")),
            domain=domain,
            metadata=metadata,
        )


def _gh_fetch(url: str) -> dict[str, Any]:
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "OpenHarness-FeedDigest/1.0",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _simple_fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 15,
) -> Any:
    import urllib.request

    req_headers = {"User-Agent": "OpenHarness-FeedDigest/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _simple_fetch_html(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> str:
    import urllib.request

    req_headers = {"User-Agent": "Mozilla/5.0 (OpenHarness FeedDigest)"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_xml_text(block: str, tag: str) -> str:
    match = re.search(
        rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>",
        block,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return re.sub(r"<[^>]+>", "", match.group(1)).strip()
    return ""


def _extract_xml_attr(block: str, tag: str, attr: str) -> str:
    match = re.search(rf'<{tag}[^>]+{attr}=["\']([^"\']+)["\']', block, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def get_source(name: str, config: dict | None = None) -> object:
    """Get a source instance by name."""
    cfg = config or {}
    if name == "github":
        return GitHubTrendingSource(
            prefer_html=cfg.get("prefer_html", True),
            days=cfg.get("days", 7),
            min_stars=cfg.get("min_stars", 50),
            query_terms=cfg.get("query_terms"),
        )
    if name == "hackernews":
        return HackerNewsSource(
            mode=cfg.get("mode", HackerNewsSource.DEFAULT_MODE),
            query_terms=cfg.get("query_terms"),
            stories_limit=cfg.get("stories_limit", 50),
        )
    if name == "rss":
        # Support both "feeds" and legacy "rss_feeds" key
        feeds = cfg.get("feeds") or cfg.get("rss_feeds")
        return RSSSource(feed_urls=feeds)
    if name == "v2ex":
        return V2EXSource()
    if name == "huggingface":
        return HuggingFacePapersSource()
    if name == "arxiv":
        return ArXivSource(categories=cfg.get("categories"))
    if name == "36kr":
        return Kr36Source(category=cfg.get("category", Kr36Source.DEFAULT_CATEGORY))
    if name == "ithome":
        return ITHomeSource(
            tag=cfg.get("tag", ITHomeSource.DEFAULT_TAG),
            query_terms=cfg.get("query_terms"),
        )
    if name == "newsnow":
        return NewsNowSource(
            source_ids=cfg.get("source_ids"),
            source_groups=cfg.get("groups"),
            base_url=cfg.get("base_url", NewsNowSource.DEFAULT_BASE_URL),
            query_terms=cfg.get("query_terms"),
            max_sources=cfg.get("max_sources", 8),
        )
    if name == "sopilot":
        return SoPilotSource(
            url=cfg.get("url", SoPilotSource.DEFAULT_URL),
            rss_url=cfg.get("rss_url", SoPilotSource.DEFAULT_RSS_URL),
            prefer_rss=cfg.get("prefer_rss", True),
            query_terms=cfg.get("query_terms"),
        )
    if name == "browser":
        return BrowserCommandSource(
            command=cfg.get("command"),
            timeout_seconds=cfg.get("timeout_seconds", 60),
            source_name=cfg.get("source_name", "browser"),
            query_terms=cfg.get("query_terms"),
        )
    raise ValueError(f"Unknown feed source: {name!r}")
