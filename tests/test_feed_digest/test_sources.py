from __future__ import annotations

from types import SimpleNamespace

from feed_digest.config import FeedDigestConfig
from feed_digest.sources import (
    BrowserCommandSource,
    GitHubTrendingSource,
    ITHomeSource,
    NewsNowSource,
    NEWSNOW_SOURCE_GROUPS,
    SoPilotSource,
    HackerNewsSource,
    get_source,
)


def test_github_trending_html_parser_filters_query() -> None:
    html = """
    <article class="Box-row">
      <h2 class="h3 lh-condensed">
        <a href="/acme/ai-agent" class="Link">acme / ai-agent</a>
      </h2>
      <p class="col-9 color-fg-muted my-1 tmp-pr-4">AI coding agent for autonomous workflows.</p>
      <a href="/acme/ai-agent/stargazers">1,234</a>
      <span itemprop="programmingLanguage">Python</span>
      <span>321 stars today</span>
    </article>
    <article class="Box-row">
      <h2 class="h3 lh-condensed">
        <a href="/acme/infra-kit" class="Link">acme / infra-kit</a>
      </h2>
      <p class="col-9 color-fg-muted my-1 tmp-pr-4">Infrastructure helpers.</p>
      <a href="/acme/infra-kit/stargazers">222</a>
    </article>
    """

    items = GitHubTrendingSource()._parse_trending_html(
        html,
        domain="AI & Machine Learning",
        query="AI & Machine Learning",
        max_items=10,
    )

    assert [item.title for item in items] == ["acme / ai-agent"]
    assert items[0].url == "https://github.com/acme/ai-agent"
    assert "Stars: 1,234" in items[0].content
    assert "Language: Python" in items[0].content


def test_ithome_parser_extracts_articles() -> None:
    html = """
    <ul class="bl">
      <li>
        <a href="https://www.ithome.com/0/956/927.htm" class="img"></a>
        <div class="c" data-ot="2026-05-29T10:46:04.2130000+08:00">
          <h2><a href="https://www.ithome.com/0/956/927.htm" class="title">英伟达发布 AI 平台</a></h2>
          <div class="m">面向企业的 AI 新平台。</div>
        </div>
      </li>
    </ul>
    """

    items = ITHomeSource()._parse_tag_html(
        html,
        domain="AI & Machine Learning",
        query="AI & Machine Learning",
        max_items=5,
    )

    assert len(items) == 1
    assert items[0].source == "ithome"
    assert items[0].title == "英伟达发布 AI 平台"
    assert items[0].author == "IT之家"
    assert items[0].content == "面向企业的 AI 新平台。"


def test_newsnow_parser_keeps_matching_entries() -> None:
    payload = {
        "updatedTime": 1748500000000,
        "items": [
            {
                "title": "OpenAI 发布新模型",
                "url": "https://example.com/openai",
                "pubDate": "2026-05-29T09:30:00+08:00",
                "extra": {"info": "热度上升", "hover": "AI 模型"},
            },
            {
                "title": "世界杯赛程更新",
                "url": "https://example.com/sports",
                "extra": {"info": "体育"},
            },
        ],
    }

    items = NewsNowSource(query_terms=["AI", "OpenAI"])._parse_source_payload(
        payload,
        source_id="weibo",
        domain="AI & Machine Learning",
        query="AI & Machine Learning",
        max_items=5,
    )

    assert len(items) == 1
    assert items[0].source == "newsnow"
    assert items[0].metadata["newsnow_source_id"] == "weibo"
    assert "NewsNow source: weibo" in items[0].content
    assert "热度上升" in items[0].content


def test_newsnow_expands_source_groups() -> None:
    source_ids = NewsNowSource._resolve_source_ids(["ithome"], ["developer", "ai_core"])

    assert source_ids[0] == "ithome"
    assert "juejin" in source_ids
    assert "github-trending-today" in source_ids
    assert len(source_ids) == len(set(source_ids))
    assert NEWSNOW_SOURCE_GROUPS["ai_news"]


def test_sopilot_parser_extracts_hot_tweet_cards() -> None:
    html = """
    <div class="rounded-lg border bg-card text-card-foreground shadow-sm hover:shadow-lg transition-shadow duration-200">
      <div class="p-4 md:max-h-[320px] overflow-hidden">
        <div class="flex items-start gap-3 h-full">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <a target="_blank" href="https://x.com/agenticnews">Agentic News</a>
              <span class="text-sm text-gray-500 dark:text-gray-400">@<!-- -->agenticnews</span>
              <span class="text-xs text-gray-500 dark:text-gray-400" title="5/29/2026, 7:15:53 AM">· <!-- -->2小时前<!-- -->发布</span>
            </div>
            <p class="mt-1 text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap line-clamp-6">
              Anthropic 发布新的 AI Agent 工作流实践。
            </p>
            <div class="flex items-center gap-6 mt-3">
              <div class="flex items-center gap-1 text-gray-500 dark:text-gray-400"><span class="text-sm">33</span></div>
              <div class="flex items-center gap-1 text-gray-500 dark:text-gray-400"><span class="text-sm">12</span></div>
              <div class="flex items-center gap-1 text-gray-500 dark:text-gray-400"><span class="text-sm">5</span></div>
              <div class="flex items-center gap-1 text-gray-500 dark:text-gray-400"><span class="text-sm">1.9万</span></div>
            </div>
            <div>爆速</div><div class="text-xl font-semibold text-red-500">1.1万/h</div>
            <div>起爆概率</div><div class="text-2xl font-bold text-red-500">100%</div>
            <div>预测浏览量</div><div class="text-xl font-semibold text-purple-500">2.5万</div>
            <a target="_blank" rel="noopener noreferrer" href="/hot-tweets?tweetId=2060258812314157242">生成评论</a>
          </div>
        </div>
      </div>
    </div>
    """

    items = SoPilotSource(query_terms=["AI", "Agent"])._parse_hot_tweets_html(
        html,
        base_url="https://sopilot.net/zh/hot-tweets",
        domain="AI & Machine Learning",
        query="AI & Machine Learning",
        max_items=5,
    )

    assert len(items) == 1
    assert items[0].source == "sopilot"
    assert items[0].url.endswith("tweetId=2060258812314157242")
    assert "Handle: @agenticnews" in items[0].content
    assert "Views: 1.9万" in items[0].content
    assert "Probability: 100%" in items[0].content


def test_sopilot_rss_parser_extracts_items() -> None:
    xml = """
    <rss version="2.0">
      <channel>
        <item>
          <title>Agentic News (@agenticnews)</title>
          <link>https://sopilot.net/hot-tweets?tweetId=123</link>
          <description><![CDATA[Anthropic 发布新的 AI Agent 工作流实践。]]></description>
          <pubDate>Fri, 29 May 2026 09:15:53 +0800</pubDate>
        </item>
      </channel>
    </rss>
    """

    items = SoPilotSource(query_terms=["AI", "Agent"])._parse_rss_feed(
        xml,
        domain="AI & Machine Learning",
        query="AI & Machine Learning",
        max_items=5,
    )

    assert len(items) == 1
    assert items[0].source == "sopilot"
    assert items[0].author == "Agentic News"
    assert items[0].metadata["sopilot_handle"] == "agenticnews"
    assert items[0].metadata["sopilot_rss"] is True


def test_hackernews_firebase_story_parser_marks_mode() -> None:
    story = {
        "id": 123,
        "title": "Anthropic ships a new AI coding workflow",
        "by": "dang",
        "score": 321,
        "descendants": 42,
        "time": 1748500000,
        "url": "https://example.com/hn-ai",
        "text": "Agent workflows and model tooling.",
    }

    item = HackerNewsSource(query_terms=["AI", "Agent"])._item_from_firebase_story(
        story,
        endpoint="beststories",
        domain="AI & Machine Learning",
        query="AI & Machine Learning",
    )

    assert item is not None
    assert item.source == "hackernews"
    assert item.metadata["hackernews_mode"] == "best"
    assert "Points: 321" in item.content


def test_browser_command_source_parses_json_items(monkeypatch) -> None:
    def fake_run(argv, capture_output, text, timeout, check):  # noqa: ANN001
        assert argv == ["opencli", "browser", "export"]
        assert capture_output is True
        assert text is True
        assert timeout == 30
        assert check is False
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"title":"OpenAI 发布新模型","url":"https://example.com/a",'
                '"summary":"AI source summary","author":"demo"}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("feed_digest.sources.subprocess.run", fake_run)
    source = BrowserCommandSource(
        command="opencli browser export",
        timeout_seconds=30,
        source_name="opencli_browser",
        query_terms=["AI"],
    )

    items = source._run_command_json()
    assert items[0]["title"] == "OpenAI 发布新模型"

    collected = source._coerce_item(
        items[0],
        domain="AI & Machine Learning",
        query="AI & Machine Learning",
    )
    assert collected is not None
    assert collected.source == "opencli_browser"
    assert collected.metadata["browser_command_source"] == "opencli_browser"


def test_get_source_supports_new_sources() -> None:
    assert isinstance(get_source("ithome"), ITHomeSource)
    assert isinstance(get_source("newsnow"), NewsNowSource)
    assert isinstance(get_source("sopilot"), SoPilotSource)
    assert isinstance(get_source("browser", {"command": ["opencli", "browser"]}), BrowserCommandSource)


def test_default_ai_news_domain_includes_new_sources() -> None:
    domain = FeedDigestConfig().domains["ai_news"]
    assert "ithome" in domain.sources
    assert "newsnow" in domain.sources
    assert "sopilot" in domain.sources
    assert domain.source_configs["ithome"]["tag"] == "AI"
    assert domain.source_configs["hackernews"]["mode"] == "top_best"
    assert domain.source_configs["newsnow"]["groups"] == ["ai_news"]
    assert domain.source_configs["sopilot"]["prefer_rss"] is True


def test_backfill_adds_missing_source_config_keys_non_destructively() -> None:
    config = FeedDigestConfig(
        domains={
            "ai_news": {
                "title": "AI 热点",
                "domain": "",
                "sources": ["github", "hackernews", "rss", "newsnow", "sopilot"],
                "source_weights": {"github": 9.9},
                "source_configs": {"newsnow": {"query_terms": ["custom"]}},
            }
        }
    )

    domain = config.domains["ai_news"]
    assert domain.domain == "AI & Machine Learning"
    assert domain.source_weights["github"] == 9.9
    assert domain.source_configs["newsnow"]["query_terms"] == ["custom"]
    assert domain.source_configs["newsnow"]["groups"] == ["ai_news"]
    assert domain.source_configs["hackernews"]["mode"] == "top_best"
    assert domain.source_configs["sopilot"]["prefer_rss"] is True
