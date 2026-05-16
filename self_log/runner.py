"""Self-log query runner backed by the OpenHarness QueryEngine."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from openharness.api.client import SupportsStreamingMessages
from openharness.config import load_settings
from openharness.config.settings import PermissionSettings
from openharness.engine.messages import sanitize_conversation_messages
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import AssistantTurnComplete, ToolExecutionCompleted
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.ui.runtime import _resolve_api_client_from_settings
from openharness.utils.log import get_logger

from self_log.memory import load_memory_prompt
from self_log.session import load_conversation, save_conversation
from self_log.store import SelfLogStore
from self_log.tools import SelfLogToolRegistry, build_oh_registry
from self_log.workspace import get_soul_path, get_user_path, get_workspace_root

logger = get_logger(__name__)

_SELF_LOG_TOOL_ROUTER_PROMPT = """你是 self-log app 的语义路由 agent。用户通过飞书等渠道发送日常记录、日志、补录等内容，由你决定如何处理。

每条消息必须**调用工具**完成动作，不要只用文字回答。

---

## 决策流程

**第一步：判断意图**

| 意图 | 处理方式 |
|------|----------|
| 明确要记录 / 日常流水 / 情绪事件 | → self_log_record 或 self_log_import_records |
| 补录多天旧日记、粘贴流水账 | → self_log_import_records（由你拆分，不要要求用户整理） |
| 查看最近记录 | → self_log_view |
| 查状态/数量/路径 | → self_log_status |
| 要报告/复盘 | → self_log_report |
| 处理/整理待确认记录 | → self_log_process |
| 补昨天且有具体内容 | → self_log_backfill |
| 问候/测试/闲聊/意图不清 | → self_log_clarify |

---

## self_log_clarify 触发原则

**必须澄清（禁止猜测入库）：**
- 意图不明：问候语、单字、"hi/ok/?"、闲聊、测试消息 → 引导用户发送要记录的内容
- 只有补录意图但没有实际内容：用户说"帮我记一下/忘记记了"但没说具体是什么事
- 记录主体完全模糊：只有"他/她/他们做了某事"但完全不知道指谁，且指代关系对理解事件至关重要
- 引用当前无法理解的上下文："就是上次说的那件事"、"那个结果出来了"但无从判断是什么

**不需要澄清（直接入库）：**
- 事件和情绪可理解，即使有陌生人名（"和小李吃了火锅" 不需要知道小李是谁）
- 口语化、碎片化但主体明确（"好累，加班到11点了"）
- 人名/地名不确定，但这个不确定性不影响理解记录的核心意思
- 记录细节不全，但用户明显是在记流水账

**原则：宁可让记录稍微不完整，也不要频繁打断用户；只在缺失信息会导致记录完全无法理解时才询问。**
**每次只问一个问题，问最关键的那个。**

---

## 其他规则

- 调用 self_log_record 时尽量填写 corrected_content、summary、tags、emotion 等结构化字段
- 发现对话中涉及值得长期保留的用户背景信息（家人/工作/常去地点）→ 调用 self_log_remember 写入 memory（直接持久化）
- 对于需要审核的结构化资料更新建议 → 使用 self_log_profile_update
- 工具参数中不要填写当前日期，工具会自行计算
"""

_MAX_TURNS = 10


def _read_file(path: Path) -> str | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    return content or None


def _build_system_prompt(workspace: Path) -> str:
    """Build the system prompt by combining routing rules with persona files and memory."""
    sections = [_SELF_LOG_TOOL_ROUTER_PROMPT.strip()]

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


class SelfLogQueryRunner:
    """Run the self-log agent loop using OpenHarness QueryEngine with persistent conversation history."""

    def __init__(
        self,
        store: SelfLogStore,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        self._client = api_client or _resolve_api_client_from_settings(settings)
        self._store = store

    async def run(self, user_text: str, session_key: str = "") -> str:
        registry = SelfLogToolRegistry(self._store)
        oh_registry = build_oh_registry(registry)

        workspace = get_workspace_root(self._store.workspace)
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
            tool_metadata={"session_id": session_id},
        )
        if prior_messages:
            engine.load_messages(sanitize_conversation_messages(prior_messages))

        last_text = ""
        tool_outputs: list[str] = []
        try:
            async for event in engine.submit_message(user_text):
                if isinstance(event, AssistantTurnComplete):
                    candidate = event.message.text.strip()
                    if candidate and not event.message.tool_uses:
                        last_text = candidate
                elif isinstance(event, ToolExecutionCompleted):
                    if not event.is_error and event.output.strip():
                        tool_outputs.append(event.output.strip())
        except Exception:
            logger.exception("SelfLogQueryRunner engine error session_key=%r text=%r", session_key, user_text[:80])

        if session_key:
            save_conversation(workspace, session_key, engine.messages, session_id=session_id)

        return last_text or "\n".join(tool_outputs) or "这里是 self-log 记录专用 bot，请发送想要记录的内容。"
