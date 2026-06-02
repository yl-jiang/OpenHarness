"""Work-log query runner backed by the OpenHarness QueryEngine."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.api.client import SupportsStreamingMessages
from openharness.config import load_settings
from openharness.config.settings import PermissionSettings
from openharness.engine.messages import ConversationMessage, ImageBlock, TextBlock, sanitize_conversation_messages
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete, ReasoningDelta, ToolExecutionCompleted, ToolExecutionStarted
from openharness.engine.types import ToolMetadataKey
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.api.recording_client import wrap_with_model_call_recorder
from openharness.skills import load_skill_registry
from openharness.ui.runtime import _resolve_api_client_from_settings, _resolve_vision_config
from openharness.utils.log import get_logger

from wolo.core.memory import load_memory_prompt
from wolo.core.session import load_conversation, save_conversation
from wolo.core.store import WoloStore
from wolo.tools import WoloToolRegistry, build_oh_registry
from wolo.core.workspace import get_memory_dir, get_sessions_dir, get_skills_dir, get_soul_path, get_user_path, get_workspace_root

logger = get_logger(__name__)

_FALLBACK_MESSAGE = "这里是 wolo 工作记录专用 bot，请发送想要记录的工作内容。"

_WOLO_TOOL_ROUTER_PROMPT = """你是 wolo app 的语义路由 agent。用户通过飞书等渠道发送工作记录、项目进展、会议纪要、prompt/tool 经验、补录等内容，由你决定如何处理。

每条消息必须**调用工具**完成动作，不要只用文字回答。

---

## 决策流程

**第一步：判断意图**

| 意图 | 处理方式 |
|------|----------|
| 明确要记录工作 / 项目进展 / 会议 / 代码 / prompt / tool / blocker / 决策（单一日期） | → wolo_record |
| 一条消息包含**跨日期**的多件事（如"昨天做了X，今天做了Y"） | → wolo_import_records（按日期拆分为多条，每条设正确的 date） |
| 补录多天工作日志、粘贴会议流水账、周报草稿 | → wolo_import_records（由你拆分，不要要求用户整理） |
| 补录单条昨天/前天的工作记录（用户没有提供结构化字段） | → wolo_backfill（快速存入 + 自动结构化） |
| 浏览最近几条记录（无特定筛选条件） | → wolo_view |
| 按关键词/日期/标签/状态精确过滤记录 | → wolo_search |
| 询问过往工作/做过什么/综合回顾（开放性问题） | → wolo_work_query（聚合 records + decisions + highlights） |
| 查某条记录对应的原图 / 原文件 / 来源消息 | → wolo_show |
| 查状态/数量/路径 | → wolo_status |
| 查 LLM 调用次数 / 模型使用统计 | → wolo_llm_usage |
| 查当前时间/日期/时区 | → wolo_get_now |
| 查待办/完成项 | → wolo_todos 或 wolo_done |
| 更新待办状态/信息 | → wolo_update_todo |
| 查 blocker/风险 | → wolo_blockers |
| 查关键决策 | → wolo_decisions |
| 查重要事项/prompt/tool 经验 | → wolo_highlights |
| 一次性提醒（只发消息不执行任务，如"2分钟后提醒我喝水"） | → wolo_remind |
| 未来某时间代你执行任务并发送结果（如"明天12点生成一份周报"） | → wolo_schedule |
| 周期性/重复性检查（如"每小时提醒我喝水"、"每30分钟看一下CI"） | → wolo_heartbeat_task |
| 查看所有待执行的提醒/定时任务 | → wolo_jobs |
| 取消某个提醒或定时任务 | → wolo_jobs 获取 job name，再 wolo_cancel |
| 要周报/月报/年报/工作复盘 | → wolo_report |
| 要新闻简报 / AI热点 / 资讯简报 / feed digest / 最新资讯 | → wolo_fetch_digest |
| 导出记录为 Markdown/JSON | → wolo_export |
| 生成可视化报告（情绪分布/标签云/活跃度热力图） | → wolo_visualize |
| 处理/整理待确认记录 | → wolo_process |
| 同步外部上下文（git/calendar） | → wolo_sync_context |
| 问候/测试/闲聊/意图不清 | → wolo_clarify |

---

## wolo_clarify 触发原则

**必须澄清（禁止猜测入库）：**
- 意图不明：问候语、单字、"hi/ok/?"、闲聊、测试消息 → 引导用户发送要记录的内容
- 只有补录意图但没有实际内容：用户说"帮我记一下/忘记记了"但没说具体是什么事
- 记录主体完全模糊：只有"他/她/他们处理了"但完全不知道项目/任务/owner，且会影响事实理解
- 引用当前无法理解的上下文："就是上次说的那个 PR"、"那个结果出来了"但无从判断是什么

**不需要澄清（直接入库）：**
- 工作事实可理解，即使项目或同事名第一次出现
- 口语化、碎片化但主体明确（"修完 gateway flaky test，卡在 mock profile"）
- prompt/tool 名不完整但不影响理解核心结果
- 记录细节不全，但用户明显是在记工作流水账

**原则：宁可让工作记录稍微不完整，也不要频繁打断用户；只在缺失信息会导致项目事实误导时才询问。**
**每次只问一个问题，问最关键的那个。**

---

## 跨日期消息拆分原则

当用户的一条消息中涉及**不同日期发生的事情**时，必须拆分为多条记录（使用 `wolo_import_records`），每条记录设置正确的 `date`。

**判断标准：**
- 出现"昨天/前天/上周X/X号"等时间词 + "今天/刚才/现在"等混合 → 拆分
- 描述的是同一件事的连续过程（如"昨天开始做X，今天做完了"）→ 也拆分，因为每个时间点都是独立的事实记录

**拆分示例：**
- 用户说："昨天晚上加班到12点修 gateway bug，今天上午跟 PM 对了优先级"
  → 拆为2条：record_1(date=昨天, period=深夜, content="加班到12点修 gateway bug"), record_2(date=今天, period=上午, content="跟 PM 对了优先级")
- 用户说："上周三做了 A，上周五做了 B，今天做了 C"
  → 拆为3条，各自设正确日期

**注意：**
- 每条拆分记录的 `content` 应使用第一人称当天视角重写（不要说"昨天"，而是说"今天"或直接描述事件）
- `corrected_content` 也要以该条记录自身日期为视角

---

## 其他规则

- 调用 wolo_record 时尽量填写 corrected_content、summary、tags、emotion 等结构化字段，tags 优先包含项目/会议/代码/prompt/tool/blocker/决策/交付等工作标签
- 如果消息中包含明确待办、关键决策、重要事项、prompt/tool 经验、blocker 或风险，必须同时填写 todos、decisions、highlights 参数，方便后续查询和周报引用
- `wolo_view` / `wolo_search` / `wolo_work_query` 会显示已绑定的 attachments；如果需要继续读取历史附件：图片用 `image_to_text`，UTF-8 文本附件用 `read_file`，其他二进制文件先返回路径
- 发现值得长期保留的工作背景信息（项目目标、团队分工、仓库、工具链、prompt 模式、汇报偏好）→ 调用 wolo_remember 写入 memory（直接持久化）
- 对于需要审核的结构化资料更新建议 → 使用 wolo_profile_update
- **一次性提醒** vs **定时任务** vs **周期任务**区分：
  - `wolo_remind`：一次性发消息提醒用户做某事（系统不执行任何操作，只发通知）
  - `wolo_schedule`：一次性在未来某时间代用户执行任务并把结果发回（系统执行操作）
  - `wolo_heartbeat_task`：周期性/重复性执行检查（每30分钟自动执行一次）
  - 判断标准：只提醒不执行 → remind；代为执行 → schedule；重复/周期性 → heartbeat_task
  - 若用户没说清提醒内容或未来时间，用 `wolo_clarify` 追问
- 取消提醒/定时任务时：先调用 `wolo_jobs` 列出待执行任务，再带 job_name 调用 `wolo_cancel` 取消
- 工具参数中不要填写当前日期，工具会自行计算

---

## 回复约束

- 每次工具执行完毕后，你的文字回复**必须且只能**回应用户最近一条消息的内容。
- 语气自然温暖，像同事之间的对话。可以自然表达已经记下，但不要只说「已记录」「已入库」「已保存」这类机械确认语；还要给出贴合情境的轻量反馈。
- 如果用户消息上方出现了「Relevant Historical Records」区块，只在确实相关时顺带引用；不要为了引用而牵强跳回旧话题。
- 确认收到工作记录时，可以简短表达关注或跟进（如"这个排查有结果了吗""blocker 需要帮忙 push 吗"），但不要啰嗦。
- **严禁**在工具执行后跳回之前的历史讨论话题。
"""

_MAX_TURNS = 10
_SESSION_MAX_MESSAGES = 80

# Friendly Chinese labels for tool actions, keyed by the name suffix after the
# ``solo_`` / ``wolo_`` prefix (both apps share the same action vocabulary).
_TOOL_LABELS: dict[str, str] = {
    "record": "📝 记录内容",
    "import_records": "📝 批量记录",
    "backfill": "📝 补录记录",
    "view": "📖 浏览记录",
    "search": "🔍 搜索记录",
    "work_query": "🔍 综合回顾",
    "decisions": "🧭 查看决策",
    "highlights": "✨ 查看高光",
    "blockers": "🚧 查看阻塞",
    "playbook": "📘 查看打法",
    "show": "🖼️ 查看来源",
    "status": "📊 查看状态",
    "llm_usage": "🤖 模型调用统计",
    "get_now": "🕐 查询时间",
    "remind": "⏰ 设置提醒",
    "schedule": "📅 定时任务",
    "heartbeat_task": "🔁 周期任务",
    "jobs": "📋 查看任务",
    "cancel": "🚫 取消任务",
    "report": "📑 生成报告",
    "report_list": "📑 报告列表",
    "report_show": "📑 查看报告",
    "report_search": "📑 搜索报告",
    "fetch_digest": "📡 获取资讯简报",
    "export": "📤 导出记录",
    "visualize": "📈 生成可视化",
    "process": "⚙️ 整理记录",
    "sync_context": "🔄 同步上下文",
    "todos": "✅ 待办清单",
    "done": "✅ 完成待办",
    "update_todo": "✏️ 更新待办",
    "update_record": "✏️ 更新记录",
    "delete_record": "🗑️ 删除记录",
    "clarify": "💬 请你补充",
    "remember": "🧠 记入长期记忆",
    "profile_update": "🪪 更新资料",
    "suggest_reflection": "💡 复盘建议",
    "experiments": "🧪 查看实验",
    "patterns": "🧩 查看模式",
}

# Friendly labels for common argument keys.
_ARG_LABELS: dict[str, str] = {
    "content": "内容",
    "corrected_content": "整理后内容",
    "summary": "摘要",
    "query": "关键词",
    "keyword": "关键词",
    "domain": "领域",
    "date": "日期",
    "tags": "标签",
    "tag": "标签",
    "status": "状态",
    "limit": "数量",
    "report_type": "报告类型",
    "message": "提醒内容",
    "task": "任务内容",
    "delay_minutes": "延迟(分钟)",
    "when": "时间",
    "cron": "周期",
    "interval_minutes": "间隔(分钟)",
    "job_name": "任务名",
    "title": "标题",
    "todo_id": "待办",
    "record_id": "记录",
    "project": "项目",
    "format": "格式",
    "kind": "类型",
    "name": "名称",
}

# Arguments that are noise for end-users and should never be shown.
_HIDDEN_ARGS: frozenset[str] = frozenset({"source_context", "metadata", "session_key"})

_MAX_HINT_ARGS = 3
_MAX_ARG_LEN = 60


def _stringify_arg(value: Any) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
    elif isinstance(value, (list, tuple)):
        text = "、".join(_stringify_arg(v) for v in value if v not in (None, ""))
    elif isinstance(value, dict):
        text = "、".join(f"{k}:{_stringify_arg(v)}" for k, v in value.items())
    else:
        text = str(value)
    text = text.strip()
    if len(text) > _MAX_ARG_LEN:
        text = text[: _MAX_ARG_LEN - 1] + "…"
    return text


def _format_tool_hint(tool_name: str, tool_input: dict[str, Any] | None) -> str:
    """Render a human-friendly tool-call hint with key arguments.

    Shows the friendly action label plus the most relevant arguments so the
    Feishu user sees *what* is being executed, not just the tool name.
    """
    suffix = tool_name.split("_", 1)[1] if "_" in tool_name else tool_name
    header = _TOOL_LABELS.get(suffix, f"🛠️ {tool_name}")

    lines: list[str] = []
    for key, value in (tool_input or {}).items():
        if key in _HIDDEN_ARGS or value in (None, "", [], {}):
            continue
        text = _stringify_arg(value)
        if not text:
            continue
        label = _ARG_LABELS.get(key, key)
        lines.append(f"  · {label}：{text}")
        if len(lines) >= _MAX_HINT_ARGS:
            break

    if lines:
        return header + "\n" + "\n".join(lines)
    return header


def _read_file(path: Path) -> str | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    return content or None


def _build_time_context() -> str:
    """Build a short time-context prefix for the user message.

    Kept out of the system prompt so the static system prompt can benefit from
    KV-Cache prefix sharing across turns.
    """
    from datetime import datetime

    local_now = datetime.now().astimezone()
    return (
        f"## Current Local Time\n"
        f"- Date: {local_now.strftime('%Y-%m-%d')}\n"
        f"- Time: {local_now.strftime('%H:%M:%S')}\n"
        f"- Timezone: {local_now.tzname()} (UTC{local_now.strftime('%z')})\n"
        f"- Weekday: {local_now.strftime('%A')}\n"
        f"\n"
        f"When the user mentions time without an explicit date (e.g. '10:00站会', '加班到很晚'), "
        f"assume it refers to TODAY in the above local timezone, not UTC.\n"
        f"\n---\n\n"
    )


def _is_image_file(path: str) -> bool:
    """Check if a file path refers to an image based on MIME type."""
    mime, _ = mimetypes.guess_type(path)
    return bool(mime and mime.startswith("image/"))


def _build_similar_records_context(store: WoloStore, user_text: str, *, max_results: int = 5) -> str:
    """Search historical records for BM25-similar entries and return a compact context block.

    Kept out of the system prompt (like time context) so the static prompt
    benefits from KV-Cache sharing across turns.
    """
    if not user_text.strip():
        return ""
    try:
        records = store.search_records(query=user_text, limit=max_results)
    except Exception:
        logger.debug("_build_similar_records_context search failed, skipping")
        return ""
    if not records:
        logger.debug("_build_similar_records_context no similar records found")
        return ""
    lines = [
        "## Relevant Historical Records",
        "",
        "The following past records are semantically similar to the user's current message.",
        "Use them to detect patterns, avoid contradictions, or reference related past events.",
        "",
    ]
    for record in records:
        summary = record.summary or record.corrected_content[:60]
        tag_part = f" #{record.tags}" if record.tags else ""
        lines.append(f"- [{record.date}] {summary} [{record.emotion}]{tag_part}")
    lines.append("")
    logger.debug("_build_similar_records_context found %d similar records", len(records))
    return "\n".join(lines)


def _build_user_message(text: str, media: list[str] | None) -> str | ConversationMessage:
    """Build a user message, optionally embedding image blocks from media paths.

    Returns a plain string if no image media is present (preserving existing behavior),
    or a ConversationMessage with TextBlock + ImageBlock content when images are provided.
    """
    if not media:
        return text

    image_paths = [p for p in media if _is_image_file(p)]
    if not image_paths:
        return text

    content: list[TextBlock | ImageBlock] = [TextBlock(text=text)]
    for img_path in image_paths:
        try:
            content.append(ImageBlock.from_path(img_path))
        except Exception:
            logger.warning("Failed to encode image attachment: %s", img_path)
    return ConversationMessage.from_user_content(content)


def _build_system_prompt(workspace: Path) -> str:
    """Build the system prompt by combining routing rules with persona files and memory."""
    sections = [_WOLO_TOOL_ROUTER_PROMPT.strip()]
    skills_prompt = _build_skills_prompt(workspace)
    if skills_prompt:
        sections.append(skills_prompt)

    soul = _read_file(get_soul_path(workspace))
    if soul:
        sections.append(soul)

    user = _read_file(get_user_path(workspace))
    if user:
        sections.extend(["# User Profile", user])

    memory = load_memory_prompt(workspace)
    if memory:
        sections.append(memory)

    return "\n\n".join(sections)


def _build_skills_prompt(workspace: Path) -> str | None:
    registry = load_skill_registry(None, extra_skill_dirs=[get_skills_dir(workspace)])
    skills = [skill for skill in registry.list_skills() if not skill.disable_model_invocation]
    if not skills:
        return None
    lines = [
        "# Available Skills",
        "",
        "The following skills are available via the `skill_manager` tool.",
        'When a user\'s request matches a skill, call `skill_manager(action="load", name="<skill_name>")` before proceeding.',
        "",
    ]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def _autodream_context(workspace: Path) -> dict[str, str]:
    return {
        "memory_dir": str(get_memory_dir(workspace)),
        "session_dir": str(get_sessions_dir(workspace)),
        "app_label": "wolo work memory",
        "runner_module": "ohmo",
    }


class WoloQueryRunner:
    """Run the wolo agent loop using OpenHarness QueryEngine with persistent conversation history."""

    def __init__(
        self,
        store: WoloStore,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        self._store = store
        base_client = api_client or _resolve_api_client_from_settings(settings)
        self._client = wrap_with_model_call_recorder(base_client, self._store.record_llm_call)

    async def stream_run(
        self,
        user_text: str,
        session_key: str = "",
        *,
        media: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
        progress_callback: Callable[[str], Any] | None = None,
    ):
        """Async generator yielding ``(kind, text)`` tuples during execution.

        Yields:
            ``("progress", text)`` — transient status/thinking hint
            ``("tool_hint", text)`` — tool-use notification
            ``("final", text)``    — the final reply (always last)
        """
        registry = WoloToolRegistry(
            self._store,
            source_context=source_context,
            progress_callback=progress_callback,
        )
        oh_registry = build_oh_registry(registry)

        workspace = get_workspace_root(self._store.workspace)
        skill_dirs = (str(get_skills_dir(workspace)),)
        prior_messages, session_id = load_conversation(workspace, session_key) if session_key else ([], None)
        # Limit session history to prevent topic drift in long conversations.
        # Keep only the most recent messages; older context is preserved in the
        # session store and remains available via search tools.
        if len(prior_messages) > _SESSION_MAX_MESSAGES:
            logger.info(
                "session window trimmed session_key=%s total=%d kept=%d",
                session_key, len(prior_messages), _SESSION_MAX_MESSAGES,
            )
            prior_messages = prior_messages[-_SESSION_MAX_MESSAGES:]
        if not session_id:
            session_id = uuid4().hex[:12]

        engine = QueryEngine(
            api_client=self._client,
            tool_registry=oh_registry,
            permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
            cwd=Path.cwd(),
            model=self._settings.model,
            system_prompt=_build_system_prompt(workspace),
            max_tokens=self._settings.max_tokens,
            max_turns=_MAX_TURNS,
            settings=self._settings,
            tool_metadata={
                "session_id": session_id,
                "extra_skill_dirs": skill_dirs,
                "user_skills_dir": str(get_skills_dir(workspace)),
                "skill_registry_cwd": None,
                ToolMetadataKey.VISION_MODEL_CONFIG.value: _resolve_vision_config(self._settings),
                "autodream_context": _autodream_context(workspace),
            },
        )
        engine.tool_metadata["system_prompt_refresher"] = lambda: engine.set_system_prompt(_build_system_prompt(workspace))
        if prior_messages:
            engine.load_messages(sanitize_conversation_messages(prior_messages))

        # Prefix the user message with volatile context so the *system prompt*
        # remains static and can be fully KV-Cache shared across turns.
        similar_context = _build_similar_records_context(self._store, user_text)
        user_message = _build_user_message(_build_time_context() + similar_context + user_text, media)

        yield ("progress", "🤔 正在思考...")
        last_text = ""
        tool_outputs: list[str] = []
        tool_errors: list[str] = []
        # Tools whose output should be sent verbatim (not summarized by LLM).
        _PASSTHROUGH_TOOLS = {"wolo_report", "wolo_visualize"}
        passthrough_output: str = ""
        engine_error: str = ""
        try:
            async for event in engine.submit_message(user_message):
                if isinstance(event, ReasoningDelta):
                    yield ("reasoning", event.text)
                elif isinstance(event, AssistantTextDelta):
                    yield ("delta", event.text)
                elif isinstance(event, ToolExecutionStarted):
                    yield ("tool_hint", _format_tool_hint(event.tool_name, event.tool_input))
                elif isinstance(event, AssistantTurnComplete):
                    candidate = event.message.text.strip()
                    if candidate and not event.message.tool_uses:
                        last_text = candidate
                elif isinstance(event, ToolExecutionCompleted):
                    if event.is_error:
                        tool_errors.append(f"{event.tool_name}: {event.output.strip()[:200]}")
                    elif event.output.strip():
                        tool_outputs.append(event.output.strip())
                        if event.tool_name in _PASSTHROUGH_TOOLS:
                            passthrough_output = event.output.strip()
        except Exception as exc:
            engine_error = f"{type(exc).__name__}: {exc}"
            logger.exception("WoloQueryRunner engine error session_key=%r text=%r", session_key, user_text[:80])

        if session_key:
            save_conversation(workspace, session_key, engine.messages, session_id=session_id)

        # For passthrough tools (report/visualize), send the full tool output
        # directly instead of the LLM's potentially abbreviated summary.
        if passthrough_output:
            final = passthrough_output
        else:
            # Prefer the model's final text for human tone after a successful
            # record/import flow; tool output remains the fallback for silent
            # final turns.
            final = last_text or "\n".join(tool_outputs) or _FALLBACK_MESSAGE
            if final.startswith(_FALLBACK_MESSAGE):
                logger.warning(
                    "wolo fallback triggered — last_text=%r tool_outputs=%s "
                    "tool_errors=%s engine_error=%s session_key=%s text_preview=%r",
                    last_text,
                    [o[:80] for o in tool_outputs],
                    tool_errors,
                    engine_error,
                    session_key,
                    user_text[:120],
                )
        yield ("final", final)

    async def run(
        self,
        user_text: str,
        session_key: str = "",
        *,
        media: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> str:
        async for kind, text in self.stream_run(
            user_text,
            session_key,
            media=media,
            source_context=source_context,
        ):
            if kind == "final":
                return text
        return _FALLBACK_MESSAGE
