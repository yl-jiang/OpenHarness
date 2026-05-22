# OpenHarness Showcase

This page collects concrete ways to use OpenHarness without overselling the project. Each example is intended to be small, reproducible, and easy to extend.

## 1. Repository-aware coding assistant

Use OpenHarness as a lightweight local coding agent for reading code, making edits, and running validation commands.

```bash
uv run oh
```

Example prompt:

```text
Review this repo, identify the highest-risk bug, patch it, and run the relevant tests.
```

## 2. Headless automation for scripts and CI

The print mode is useful when you want structured output in shell pipelines or automation jobs.

```bash
uv run oh -p "Summarize the purpose of this repository" --output-format json
uv run oh -p "List files that define the permission system" --output-format stream-json
```

## 3. Skill and plugin playground

OpenHarness can load Markdown skills and Claude-style plugin layouts, which makes it useful for experimentation with custom workflows.

Examples:

- Put a custom skill in `~/.openharness/skills/`.
- Install a plugin into `~/.openharness/plugins/`.
- Use the same workflow conventions across multiple local projects.

## 4. Multi-agent and background task experiments

The repo includes team coordination primitives, background task management, and task inspection tools.

Example prompts:

```text
Spawn a worker to audit the test suite while you inspect the CLI command registry.
```

```text
Create a background task that runs the slow integration script and report back when it finishes.
```

## 5. Provider compatibility testbed

OpenHarness is useful when you need to compare Anthropic-compatible backends behind one harness.

Typical scenarios:

- Default Anthropic setup.
- Moonshot/Kimi through an Anthropic-compatible endpoint.
- Vertex-compatible and Bedrock-compatible gateways.
- Internal proxies that expose an Anthropic-style API surface.

See the provider compatibility table in [`README.md`](../README.md#-provider-compatibility).

## 6. Personal journal with solo

`solo` captures daily life notes from CLI or chat channels (Feishu/Telegram/Slack/Discord). The model handles structuring, tagging, and summarizing messy input.

```bash
solo init
solo record "Today I finished reading a good book"
solo process
solo view
solo report weekly
```

From Feishu/Telegram, just send text directly — solo's gateway structures it automatically:

```text
今天和朋友吃了火锅，聊了很多关于工作的事
```

## 7. Work logging with wolo

`wolo` records work fragments — project progress, meeting notes, prompt/tool experiences, blockers, decisions — and turns them into structured weekly reports.

```bash
wolo init
wolo record "Fixed gateway dedup logic; root cause was session hash not covering chat_id"
wolo process
wolo report weekly
```

wolo also tracks work artifacts (todos, decisions, highlights) extracted from records:

```text
/wolo 最近有哪些待办？
/wolo 查一下最近的 blocker
/wolo 这周 prompt/tool 方面有什么经验？
```

## 8. Dry-run safe preview

Use `--dry-run` to inspect what OpenHarness would do without executing anything:

```bash
oh --dry-run -p "Review this bug fix and grep for failing tests"
oh --dry-run -p "/plugin list"
oh --dry-run --output-format json
```

Useful for CI validation, pre-flight checks, and debugging configuration issues.

## 9. Documentation-first onboarding

If you are evaluating the project rather than contributing code, start here:

- [`README.md`](../README.md) for install, usage, and architecture.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) for contributor workflow.
- [`CHANGELOG.md`](../CHANGELOG.md) for visible repo changes.

## How to contribute a showcase entry

Good showcase additions are:

- Based on a real workflow you ran.
- Short enough to reproduce locally.
- Honest about prerequisites and limitations.
- Focused on what OpenHarness makes easier, not on generic LLM claims.
