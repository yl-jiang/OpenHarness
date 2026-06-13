"""OpenHarness-backed model agents for solo."""

from __future__ import annotations

import json
from typing import Any

from common.constants import DEFAULT_SAMPLE_TYPE
from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiTextDeltaEvent,
    SupportsStreamingMessages,
)
from openharness.api.recording_client import ModelCallRecorder, wrap_with_model_call_recorder
from openharness.config import load_settings
from openharness.engine.messages import ConversationMessage
from openharness.ui.runtime import _resolve_api_client_from_settings
from openharness.utils.log import get_logger
from solo.prompts import (
    ARTIFACT_EXTRACTION_SYSTEM_PROMPT,
    DAILY_QUESTION_SYSTEM_PROMPT,
    EXTRACT_ARTIFACTS_USER_PROMPT,
    GENERATE_DAILY_QUESTION_USER_PROMPT,
    GENERATE_REFLECTION_USER_PROMPT,
    PROCESS_RECORD_SYSTEM_PROMPT,
    PROCESS_RECORD_USER_PROMPT,
    REFLECTION_SYSTEM_PROMPT,
    report_system_prompt,
)

logger = get_logger(__name__)


class OpenHarnessSoloAgent:
    """Self-log domain agent backed by OpenHarness provider/auth/client plumbing."""

    def __init__(
        self,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
        record_model_call: ModelCallRecorder | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        base_client = api_client or _resolve_api_client_from_settings(settings)
        self._client = wrap_with_model_call_recorder(base_client, record_model_call)

    async def process_record(self, raw_content: str, profile_context: str) -> dict[str, Any]:
        snippet = raw_content[:120].replace("\n", " ")
        logger.debug("process_record start model=%s content=%r", self._settings.model, snippet)
        content = await self._complete(
            system_prompt=PROCESS_RECORD_SYSTEM_PROMPT,
            user_prompt=PROCESS_RECORD_USER_PROMPT.format(profile_context=profile_context, raw_content=raw_content),
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
        *,
        stats_summary: str = "",
    ) -> str:
        logger.info("generate_report start type=%s records=%d", report_type, len(records))
        records_text = "\n".join(
            f"### {record.get('date', '')} [{record.get('emotion', '')}] #{record.get('tags', '')}\n"
            f"**摘要**: {record.get('summary', '')}\n"
            f"**样本类型**: {record.get('sample_type', '')} | "
            f"**触发场景**: {record.get('trigger_scene', '')}\n"
            f"**断裂点**: {record.get('break_point', '')} → "
            f"**跨越动作**: {record.get('bridge_action', '')}\n"
            f"**规避设计**: {record.get('environment_design', '')}\n"
            f"**下一轮实验**: {record.get('next_experiment', '')}\n"
            f"**原文摘录**: {str(record.get('raw_content', ''))[:300]}"
            for record in records
        )
        user_prompt_parts = [profile_context]
        if stats_summary:
            user_prompt_parts.append(f"\n\n## 数据统计摘要\n{stats_summary}")
        user_prompt_parts.append(f"\n\n## 记录数据（共 {len(records)} 条）\n{records_text}")
        content = await self._complete(
            system_prompt=report_system_prompt(report_type),
            user_prompt="".join(user_prompt_parts),
            max_tokens=self._settings.max_tokens,
        )
        if not content.strip():
            raise RuntimeError("report generation returned empty response")
        return content

    async def generate_daily_question(self, profile_context: str) -> str:
        logger.info("generate_daily_question start")
        return await self._complete(
            system_prompt=DAILY_QUESTION_SYSTEM_PROMPT,
            user_prompt=GENERATE_DAILY_QUESTION_USER_PROMPT.format(profile_context=profile_context),
        )

    async def generate_reflection_questions(
        self,
        profile_context: str,
        records_summary: str,
        focus: str | None = None,
        style: str | None = None,
    ) -> str:
        logger.info("generate_reflection_questions start focus=%s style=%s", focus, style)
        focus_section = f"\n\n请特别关注以下领域：{focus}" if focus else ""
        style_section = f"\n\n请使用以下语气/风格：{style}" if style else ""
        user_prompt = GENERATE_REFLECTION_USER_PROMPT.format(
            profile_context=profile_context,
            records_summary=records_summary,
            focus_section=focus_section,
            style_section=style_section,
        )

        return await self._complete(
            system_prompt=REFLECTION_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

    async def extract_artifacts(
        self,
        record: dict[str, Any],
        raw_content: str,
        profile_context: str,
    ) -> dict[str, Any]:
        logger.debug("extract_artifacts start record_id=%s", record.get("id") or record.get("entry_id"))
        content = await self._complete(
            system_prompt=ARTIFACT_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=EXTRACT_ARTIFACTS_USER_PROMPT.format(
                profile_context=profile_context,
                raw_content=raw_content,
                record_json=json.dumps(record, ensure_ascii=False),
            ),
        )
        result = _safe_parse_json(content)
        return _normalize_artifacts(result)

    async def run_prompt(self, system_prompt: str, user_prompt: str) -> str:
        """Public interface for single-turn LLM completion (used by project discovery)."""
        return await self._complete(system_prompt=system_prompt, user_prompt=user_prompt)

    async def _complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> str:
        request = ApiMessageRequest(
            model=self._settings.model,
            messages=[ConversationMessage.from_user_text(user_prompt)],
            system_prompt=system_prompt,
            max_tokens=max_tokens or min(self._settings.max_tokens, 4096),
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
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        stripped = stripped[start : end + 1]
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
            "sample_type": DEFAULT_SAMPLE_TYPE,
            "trigger_scene": "",
            "friction_signal": "",
            "awareness_timing": "",
            "break_point": "",
            "bridge_action": "",
            "environment_design": "",
            "next_experiment": "",
            "needs_clarification": False,
            "clarification_reason": "",
            "clarification_questions": [],
            "suggested_profile_updates": [],
            "note": "LLM 输出格式异常，已原样保留",
        }
    return parsed if isinstance(parsed, dict) else {}


def _normalize_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {"todos": [], "experiments": [], "suggested_profile_updates": []}
    for key in normalized:
        value = result.get(key)
        normalized[key] = value if isinstance(value, list) else []
    return normalized
