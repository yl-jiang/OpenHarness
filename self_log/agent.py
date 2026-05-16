"""OpenHarness-backed model agents for self-log."""

from __future__ import annotations

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


class OpenHarnessSelfLogAgent:
    """Self-log domain agent backed by OpenHarness provider/auth/client plumbing."""

    def __init__(
        self,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        self._client = api_client or _resolve_api_client_from_settings(settings)

    async def process_record(self, raw_content: str, profile_context: str) -> dict[str, Any]:
        snippet = raw_content[:120].replace("\n", " ")
        logger.debug("process_record start model=%s content=%r", self._settings.model, snippet)
        content = await self._complete(
            system_prompt=_PROCESS_RECORD_SYSTEM_PROMPT,
            user_prompt=f"{profile_context}\n\n## 用户原始记录\n{raw_content}\n\n请整理上述记录，输出 JSON。",
        )
        result = _safe_parse_json(content)
        needs_clarification = bool(result.get("needs_clarification"))
        multi_records = len(result.get("records") or [])
        logger.debug(
            "process_record done needs_clarification=%s multi_records=%d",
            needs_clarification,
            multi_records,
        )
        return result

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
            f"- 情绪：{record.get('emotion', '')}\n"
            f"- 原文：{str(record.get('raw_content', ''))[:400]}"
            for record in records
        )
        return await self._complete(
            system_prompt=_report_system_prompt(report_type),
            user_prompt=f"{profile_context}\n\n## 记录数据\n{records_text}",
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


def _safe_parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if "```json" in stripped:
        stripped = stripped.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in stripped:
        stripped = stripped.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("_safe_parse_json failed to parse LLM output, falling back. raw=%r", text[:200])
        return {
            "corrected_content": text,
            "summary": "（JSON 解析失败，原样保留）",
            "tags": "其他",
            "emotion": "中性",
            "emotion_reason": "解析失败",
            "related_people": "",
            "related_places": "",
            "needs_clarification": False,
            "clarification_reason": "",
            "clarification_questions": [],
            "suggested_profile_updates": [],
            "note": "LLM 输出格式异常，已原样保留",
        }
    return parsed if isinstance(parsed, dict) else {}


_PROCESS_RECORD_SYSTEM_PROMPT = """你是一位深度理解人性的 AI 个人记录助手。你的任务是将用户杂乱、碎片化的日常输入转化为结构化的生命足迹。

你拥有以下上下文信息作为参考：
1. **Soul**: 你的行为准则与人设。
2. **User Profile**: 用户的基础资料。
3. **Personal Memory**: 已沉淀的长效背景事实（家庭、工作、健康等）。
4. **Recent Observations**: 最近观察到但尚未确认为长效事实的偏好或动态。

### 核心原则
- **事实一致性**：检查用户新输入的内容是否与已知上下文矛盾。若发现矛盾（例如已记录用户在北京，但新记录说在上海家里），在 `note` 中指出，并在 `needs_clarification` 为 true 时询问。
- **记忆分层**：
    - **Memory (长效记忆)**：极其稳定、低频变动的事实（人际关系、工作头衔、慢性状况）。
    - **Observations (动态观察)**：高频变动、偏好、习惯、临时状态。
- **绝不猜测**：对模糊的人称、地点、事件，优先追问而非脑补。

### 输出格式 (严格 JSON)
{
  "corrected_content": "修正后语病并补全上下文的原文",
  "summary": "一句极简的摘要",
  "tags": "标签1,标签2 (如：工作,家庭,情绪,健康)",
  "emotion": "积极/消极/中性/复杂",
  "emotion_reason": "基于心理学视角的简短情绪分析",
  "related_people": "涉及人物",
  "related_places": "涉及地点",
  "needs_clarification": false,
  "clarification_reason": "若为 true，说明原因",
  "clarification_questions": ["追问的问题"],
  "suggested_profile_updates": [
    {
      "category": "分类",
      "entity_type": "人物/关系/地点/偏好/项目",
      "entity_name": "名称",
      "suggested_value": "新发现或更新的内容",
      "confidence": "high/medium/low"
    }
  ],
  "note": "对本次记录的深度洞察或与历史事实的碰撞提醒"
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
      "emotion": "情绪",
      "source": "补录"
    }
  ],
  "needs_clarification": false
}
"""

def _report_system_prompt(report_type: str) -> str:
    labels = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}
    return (
        f"你是一位个人成长教练。请基于用户记录生成一份高密度、结构化的{labels[report_type]}。"
        "使用 Markdown、表格和 bullet points；温暖、客观、有洞察力，拒绝空泛鼓励。"
    )
