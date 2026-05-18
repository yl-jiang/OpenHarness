"""OpenHarness-backed model agents for wolo."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiTextDeltaEvent,
    SupportsStreamingMessages,
)
from openharness.config import load_settings
from openharness.engine.messages import ConversationMessage
from openharness.ui.runtime import _resolve_api_client_from_settings
from openharness.utils.log import get_logger

logger = get_logger(__name__)
_DEFAULT_JSON_ATTEMPTS = 3
_DEFAULT_RETRY_DELAY_SECONDS = 0.5


class OpenHarnessWoloAgent:
    """Work-log domain agent backed by OpenHarness provider/auth/client plumbing."""

    def __init__(
        self,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
        max_json_attempts: int = _DEFAULT_JSON_ATTEMPTS,
        retry_delay_seconds: float = _DEFAULT_RETRY_DELAY_SECONDS,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        self._client = api_client or _resolve_api_client_from_settings(settings)
        self._max_json_attempts = max(1, max_json_attempts)
        self._retry_delay_seconds = max(0.0, retry_delay_seconds)

    async def process_record(self, raw_content: str, profile_context: str) -> dict[str, Any]:
        snippet = raw_content[:120].replace("\n", " ")
        logger.debug("process_record start model=%s content=%r", self._settings.model, snippet)
        result = await self._complete_json(
            system_prompt=_PROCESS_RECORD_SYSTEM_PROMPT,
            user_prompt=f"{profile_context}\n\n## 用户原始记录\n{raw_content}\n\n请整理上述记录，输出 JSON。",
            fallback=_fallback_record(raw_content),
            operation="process_record",
        )
        needs_clarification = bool(result.get("needs_clarification"))
        multi_records = len(result.get("records") or [])
        logger.debug(
            "process_record done needs_clarification=%s multi_records=%d",
            needs_clarification,
            multi_records,
        )
        return result

    async def extract_artifacts(
        self,
        record: dict[str, Any],
        raw_content: str,
        profile_context: str,
    ) -> dict[str, Any]:
        logger.debug("extract_artifacts start record_id=%s", record.get("id") or record.get("entry_id"))
        result = await self._complete_json(
            system_prompt=_ARTIFACT_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=(
                f"{profile_context}\n\n"
                f"## 原始输入\n{raw_content}\n\n"
                f"## 已结构化主记录\n{json.dumps(record, ensure_ascii=False)}\n\n"
                "请只提取工作 artifacts，输出 JSON。"
            ),
            fallback=_empty_artifacts(),
            operation="extract_artifacts",
        )
        return _normalize_artifacts(result)

    async def generate_report(
        self,
        report_type: str,
        records: list[dict[str, Any]],
        profile_context: str,
    ) -> str:
        if report_type not in {"weekly", "monthly", "yearly"}:
            raise ValueError(f"Unknown report type: {report_type}")
        logger.info("generate_report start type=%s records=%d", report_type, len(records))
        records_text = "\n".join(
            f"### {record.get('date', '')}\n"
            f"- 摘要：{record.get('summary', '')}\n"
            f"- 标签：{record.get('tags', '')}\n"
            f"- 状态/情绪：{record.get('emotion', '')}\n"
            f"- 原文：{str(record.get('raw_content', ''))[:400]}"
            for record in records
        )
        return await self._complete(
            system_prompt=_report_system_prompt(report_type),
            user_prompt=f"{profile_context}\n\n## 记录数据\n{records_text}",
        )

    async def generate_daily_question(self, profile_context: str) -> str:
        logger.info("generate_daily_question start")
        return await self._complete(
            system_prompt=_DAILY_QUESTION_SYSTEM_PROMPT,
            user_prompt=f"{profile_context}\n\n请根据以上上下文，为用户生成一个今日份的、有深度的对话引导问题。",
        )

    async def generate_reflection_questions(
        self,
        profile_context: str,
        records_summary: str,
        focus: str | None = None,
        style: str | None = None,
    ) -> str:
        logger.info("generate_reflection_questions start focus=%s style=%s", focus, style)
        user_prompt = f"{profile_context}\n\n## 最近记录摘要\n{records_summary}"
        if focus:
            user_prompt += f"\n\n请特别关注以下领域：{focus}"
        if style:
            user_prompt += f"\n\n请使用以下语气/风格：{style}"

        return await self._complete(
            system_prompt=_REFLECTION_SYSTEM_PROMPT,
            user_prompt=user_prompt + "\n\n请生成 3 个深度复盘问题。",
        )

    async def _complete(self, *, system_prompt: str, user_prompt: str) -> str:
        request = ApiMessageRequest(
            model=self._settings.model,
            messages=[ConversationMessage.from_user_text(user_prompt)],
            system_prompt=system_prompt,
            max_tokens=min(self._settings.max_tokens, 4096),
            tools=[],
        )
        chunks: list[str] = []
        async for event in self._client.stream_message(request):
            if isinstance(event, ApiTextDeltaEvent):
                chunks.append(event.text)
            elif isinstance(event, ApiMessageCompleteEvent):
                final_text = event.message.text.strip()
                if final_text:
                    return final_text
        return "".join(chunks).strip()

    async def _complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        prompt = user_prompt
        for attempt in range(1, self._max_json_attempts + 1):
            try:
                content = await self._complete(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                )
                parsed = _parse_json_object(content)
                logger.debug("%s json parsed attempt=%d", operation, attempt)
                return parsed
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "%s json completion failed attempt=%d/%d: %s",
                    operation,
                    attempt,
                    self._max_json_attempts,
                    exc,
                )
                if attempt < self._max_json_attempts and self._retry_delay_seconds:
                    await asyncio.sleep(self._retry_delay_seconds)
                prompt = (
                    f"{user_prompt}\n\n"
                    "上一次模型输出不是合法 JSON。请重新输出，必须只包含一个 JSON object，"
                    "不要包含 Markdown、解释或额外文本。"
                )
        logger.error("%s exhausted json retries; using fallback: %s", operation, last_error)
        return fallback


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if "```json" in stripped:
        stripped = stripped.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in stripped:
        stripped = stripped.split("```", 1)[1].split("```", 1)[0].strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("model output must be a JSON object")
    return parsed


def _fallback_record(raw_content: str) -> dict[str, Any]:
    return {
        "corrected_content": raw_content,
        "summary": "（模型结构化失败，原样保留）",
        "tags": "其他",
        "emotion": "中性",
        "emotion_reason": "结构化失败",
        "related_people": "",
        "related_places": "",
        "needs_clarification": False,
        "clarification_reason": "",
        "clarification_questions": [],
        "suggested_profile_updates": [],
        "note": "模型输出格式异常，已原样保留",
    }


def _empty_artifacts() -> dict[str, Any]:
    return {"todos": [], "decisions": [], "highlights": [], "suggested_profile_updates": []}


def _normalize_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    normalized = _empty_artifacts()
    for key in normalized:
        value = result.get(key)
        normalized[key] = value if isinstance(value, list) else []
    return normalized


_PROCESS_RECORD_SYSTEM_PROMPT = """你是一位严谨的 AI 工作日志助手。你的任务是将用户杂乱、碎片化的工作输入转化为主工作记录。

你拥有以下上下文信息作为参考：
1. **Soul**: 你的行为准则与人设。
2. **User Profile**: 用户的工作角色、项目、团队、工具和报告偏好。
3. **Work Memory**: 已沉淀的长效工作事实（项目背景、仓库、工具、prompt 模式、协作约定）。
4. **Recent Observations**: 最近观察到但尚未确认为长效事实的项目动态、blocker、决策和工具经验。

### 核心原则
- **事实一致性**：检查用户新输入是否与已知项目上下文矛盾。若发现矛盾（例如项目名、负责人、结论或工具结果冲突），在 `note` 中指出，并在 `needs_clarification` 为 true 时询问。
- **工作优先级**：优先识别项目、任务、会议、代码变更、prompt、tool、命令、决策、blocker、风险、指标和 next action。
- **记忆分层**：
    - **Memory (长效工作记忆)**：稳定、低频变动的事实（项目目标、团队分工、工具链、汇报节奏）。
    - **Observations (动态观察)**：高频变动的进展、临时 blocker、prompt/tool 实验、阶段性结论。
- **绝不猜测**：对模糊的项目、结论、owner、度量或交付状态，优先追问而非脑补。

### 输出格式 (严格 JSON)
{
  "date": "YYYY-MM-DD (仅当用户提到非今天的日期时输出)",
  "period": "凌晨/清晨/上午/中午/下午/傍晚/深夜 (仅当用户提到的时间与当前记录时间明显不符时输出)",
  "corrected_content": "修正语病但不改变事实的工作原文",
  "summary": "一句极简的工作摘要，突出交付物/决策/blocker",
  "tags": "标签1,标签2 (如：项目,会议,代码,prompt,tool,bug,review,blocker,决策,交付)",
  "emotion": "顺利/受阻/中性/高压/完成/风险",
  "events": "会议、里程碑、发布、评审、事故或重要工作节点",
  "emotion_reason": "基于工作状态的简短说明，例如为何受阻或为何完成",
  "related_people": "涉及同事、团队、owner 或 stakeholder",
  "related_places": "涉及地点、仓库、系统、服务、工具或平台",
  "needs_clarification": false,
  "clarification_reason": "若为 true，说明原因",
  "clarification_questions": ["追问的问题"],
  "note": "对本次工作记录的洞察、风险、后续动作或与历史事实的冲突提醒"
}

如果输入包含多条独立日志，请拆分为 `records` 数组：
{
  "records": [
    {
      "date": "YYYY-MM-DD",
      "content": "单条原文",
      "corrected_content": "修正后原文",
      "summary": "摘要",
      "tags": "标签",
      "emotion": "工作状态",
      "period": "时段",
      "events": "会议/里程碑/发布/评审/事故",
      "source": "补录"
    }
  ],
  "needs_clarification": false
}
"""


_ARTIFACT_EXTRACTION_SYSTEM_PROMPT = """你是一位工作 artifacts 提取器。输入包含原始文本和已经整理好的主工作记录。

你的唯一职责是从事实中提取可持续维护的工作 artifacts，不要重写主记录，也不要推测缺失事实。

### 输出格式 (严格 JSON)
{
  "todos": [
    {
      "title": "明确可执行的待办",
      "project": "项目名",
      "priority": "high/medium/low",
      "due_date": "YYYY-MM-DD 或空"
    }
  ],
  "decisions": [
    {
      "title": "关键决策",
      "rationale": "为什么这么决定",
      "impact": "影响范围或后果",
      "project": "项目名"
    }
  ],
  "highlights": [
    {
      "kind": "important/prompt/tool/blocker/risk",
      "title": "重要事项标题",
      "content": "可复用经验、阻塞、风险或关键上下文",
      "project": "项目名",
      "tags": "prompt,tool,blocker 等"
    }
  ],
  "suggested_profile_updates": [
    {
      "category": "分类",
      "entity_type": "项目/团队/仓库/工具/prompt/流程/偏好/负责人",
      "entity_name": "名称",
      "suggested_value": "新发现或更新的工作事实",
      "confidence": "high/medium/low"
    }
  ]
}

若没有对应内容，输出空数组。只输出 JSON。
"""

def _report_system_prompt(report_type: str) -> str:
    labels = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}
    return (
        f"你是一位资深工程/知识工作复盘助手。请基于用户工作记录和 Work Artifacts 生成一份高密度、"
        f"结构化的工作{labels[report_type]}。"
        "使用 Markdown、表格和 bullet points；覆盖已完成事项、关键决策、blocker/风险、prompt/tool 经验、"
        "开放待办、跨项目模式、下周 next actions；每个结论尽量引用日期、项目或 artifact 作为证据，"
        "客观、可执行，拒绝空泛鼓励。"
    )

_DAILY_QUESTION_SYSTEM_PROMPT = """你是一位 AI 工作记录助手。
你的目标是通过一个精准、具体的问题，引导用户记录今天的工作重点。

### 引导原则
1. **基于上下文**：参考用户的 User Profile 和最近的 Observation。如果用户最近在忙某个项目、prompt/tool 实验、bug、PR 或会议，问题应与之相关。
2. **避免陈词滥调**：不要问“今天工作怎么样”，要问具体的进展、blocker、决策或 next action。
3. **保持简短**：问题应在一句话以内。
4. **可汇报**：问题的答案应该能进入周报或项目复盘。

示例：
- “昨天 wolo 结构化方案里，todo/decision/highlight 的边界最后怎么定的？”
- “今天哪个 blocker 最影响交付，下一步是谁负责推进？”
- “今天有没有新的 prompt/tool 经验值得沉淀成可复用做法？”
"""

_REFLECTION_SYSTEM_PROMPT = """你是一位工作复盘教练。
你的任务是基于用户最近的工作记录，提出 3 个能够推动项目、prompt、tool 或协作改进的深度复盘问题。

### 复盘原则
1. **见微知著**：从碎片的记录中发现重复 blocker、工具链问题、prompt 模式或协作摩擦。
2. **多维视角**：如果用户只关注了进展，提醒风险和决策依据；如果只关注了 blocker，引导拆解 next action。
3. **针对性**：如果提供了 focus 领域，请严格围绕该领域提问。
4. **启发性**：问题不应有标准答案，而是为了沉淀可复用工作方法。
"""
