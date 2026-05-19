"""Work-log query runner backed by the OpenHarness QueryEngine."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.api.client import SupportsStreamingMessages
from openharness.config import load_settings
from openharness.config.settings import PermissionSettings
from openharness.engine.messages import ConversationMessage, ImageBlock, TextBlock, sanitize_conversation_messages
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import AssistantTurnComplete, ToolExecutionCompleted, ToolExecutionStarted
from openharness.engine.types import ToolMetadataKey
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.skills import load_skill_registry
from openharness.ui.runtime import _resolve_api_client_from_settings, _resolve_vision_config
from openharness.utils.log import get_logger

from wolo.memory import load_memory_prompt
from wolo.session import load_conversation, save_conversation
from wolo.store import WoloStore
from wolo.tools import WoloToolRegistry, build_oh_registry
from wolo.workspace import get_memory_dir, get_sessions_dir, get_skills_dir, get_soul_path, get_user_path, get_workspace_root

logger = get_logger(__name__)

_WOLO_TOOL_ROUTER_PROMPT = """你是 wolo app 的语义路由 agent。用户通过飞书等渠道发送工作记录、项目进展、会议纪要、prompt/tool 经验、补录等内容，由你决定如何处理。

每条消息必须**调用工具**完成动作，不要只用文字回答。

---

## 决策流程

**第一步：判断意图**

| 意图 | 处理方式 |
|------|----------|
| 明确要记录工作 / 项目进展 / 会议 / 代码 / prompt / tool / blocker / 决策 | → wolo_record 或 wolo_import_records |
| 补录多天工作日志、粘贴会议流水账、周报草稿 | → wolo_import_records（由你拆分，不要要求用户整理） |
| 查看最近记录 | → wolo_view |
| 查某条记录对应的原图 / 原文件 / 来源消息 | → wolo_show |
| 查状态/数量/路径 | → wolo_status |
| 查待办/完成项 | → wolo_todos 或 wolo_done |
| 查 blocker/风险 | → wolo_blockers |
| 查关键决策 | → wolo_decisions |
| 查重要事项/prompt/tool 经验 | → wolo_highlights |
| 询问过往工作/重要事项/做过什么 | → wolo_work_query |
| 要周报/月报/年报/工作复盘 | → wolo_report |
| 处理/整理待确认记录 | → wolo_process |
| 补昨天且有具体内容 | → wolo_backfill |
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

## 其他规则

- 调用 wolo_record 时尽量填写 corrected_content、summary、tags、emotion 等结构化字段，tags 优先包含项目/会议/代码/prompt/tool/blocker/决策/交付等工作标签
- 如果消息中包含明确待办、关键决策、重要事项、prompt/tool 经验、blocker 或风险，必须同时填写 todos、decisions、highlights 参数，方便后续查询和周报引用
- `wolo_view` / `wolo_search` / `wolo_work_query` 会显示已绑定的 attachments；如果需要继续读取历史附件：图片用 `image_to_text`，UTF-8 文本附件用 `read_file`，其他二进制文件先返回路径
- 发现值得长期保留的工作背景信息（项目目标、团队分工、仓库、工具链、prompt 模式、汇报偏好）→ 调用 wolo_remember 写入 memory（直接持久化）
- 对于需要审核的结构化资料更新建议 → 使用 wolo_profile_update
- 工具参数中不要填写当前日期，工具会自行计算
"""

_MAX_TURNS = 10


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
        self._client = api_client or _resolve_api_client_from_settings(settings)
        self._store = store

    async def stream_run(
        self,
        user_text: str,
        session_key: str = "",
        *,
        media: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
    ):
        """Async generator yielding ``(kind, text)`` tuples during execution.

        Yields:
            ``("progress", text)`` — transient status/thinking hint
            ``("tool_hint", text)`` — tool-use notification
            ``("final", text)``    — the final reply (always last)
        """
        registry = WoloToolRegistry(self._store, source_context=source_context)
        oh_registry = build_oh_registry(registry)

        workspace = get_workspace_root(self._store.workspace)
        skill_dirs = (str(get_skills_dir(workspace)),)
        prior_messages, session_id = load_conversation(workspace, session_key) if session_key else ([], None)
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

        # Prefix the user message with a volatile time context so the *system prompt*
        # remains static and can be fully KV-Cache shared across turns.
        user_message = _build_user_message(_build_time_context() + user_text, media)

        yield ("progress", "🤔 正在思考...")
        last_text = ""
        tool_outputs: list[str] = []
        try:
            async for event in engine.submit_message(user_message):
                if isinstance(event, ToolExecutionStarted):
                    yield ("tool_hint", f"🛠️ 正在调用 {event.tool_name}")
                elif isinstance(event, AssistantTurnComplete):
                    candidate = event.message.text.strip()
                    if candidate and not event.message.tool_uses:
                        last_text = candidate
                elif isinstance(event, ToolExecutionCompleted):
                    if not event.is_error and event.output.strip():
                        tool_outputs.append(event.output.strip())
        except Exception:
            logger.exception("WoloQueryRunner engine error session_key=%r text=%r", session_key, user_text[:80])

        if session_key:
            save_conversation(workspace, session_key, engine.messages, session_id=session_id)

        yield ("final", last_text or "\n".join(tool_outputs) or "这里是 wolo 工作记录专用 bot，请发送想要记录的工作内容。")

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
        return "这里是 wolo 工作记录专用 bot，请发送想要记录的工作内容。"
