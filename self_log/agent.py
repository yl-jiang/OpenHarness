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


_PROCESS_RECORD_SYSTEM_PROMPT = """你是一位资深心理咨询师兼文字编辑。你的任务是帮用户把日常口语化的记录整理成结构化的个人日志。

铁律：
1. 绝不猜测：遇到不确定的人名、关系、地点、事件含义时，明确标记 needs_clarification。
2. 信息密度优先：输出避免大段叙述，多用结构化格式。

输出严格 JSON：
{
  "corrected_content": "修正后的原文",
  "summary": "一句话摘要",
  "tags": "标签1,标签2",
  "emotion": "积极/消极/中性/复杂",
  "emotion_reason": "情绪判断理由",
  "related_people": "人物1,人物2",
  "related_places": "地点1,地点2",
  "needs_clarification": false,
  "clarification_reason": "",
  "clarification_questions": [],
  "suggested_profile_updates": [
    {"category": "家庭/工作/生活", "entity_type": "人物/地点/关系/项目", "entity_name": "名称", "suggested_value": "建议填入资料的内容", "confidence": "high/medium/low"}
  ],
  "note": "补充说明"
}

如果原始记录包含多条独立日志，输出：
{
  "records": [
    {
      "date": "YYYY-MM-DD",
      "content": "单条原文",
      "corrected_content": "修正后的单条原文",
      "summary": "一句话摘要",
      "tags": "标签1,标签2",
      "emotion": "积极/消极/中性/复杂",
      "emotion_reason": "情绪判断理由",
      "related_people": "人物1,人物2",
      "related_places": "地点1,地点2",
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
