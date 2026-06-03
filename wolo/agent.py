"""OpenHarness-backed model agents for wolo."""

from __future__ import annotations

import asyncio
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

from wolo.prompts import (
    ARTIFACT_EXTRACTION_SYSTEM_PROMPT,
    DAILY_QUESTION_SYSTEM_PROMPT,
    EXTRACT_ARTIFACTS_USER_PROMPT,
    GENERATE_DAILY_QUESTION_USER_PROMPT,
    GENERATE_REFLECTION_USER_PROMPT,
    JSON_RETRY_SUFFIX,
    PROCESS_RECORD_SYSTEM_PROMPT,
    PROCESS_RECORD_USER_PROMPT,
    REFLECTION_SYSTEM_PROMPT,
    report_system_prompt,
)

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
        record_model_call: ModelCallRecorder | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        base_client = api_client or _resolve_api_client_from_settings(settings)
        self._client = wrap_with_model_call_recorder(base_client, record_model_call)
        self._max_json_attempts = max(1, max_json_attempts)
        self._retry_delay_seconds = max(0.0, retry_delay_seconds)

    async def process_record(self, raw_content: str, profile_context: str) -> dict[str, Any]:
        snippet = raw_content[:120].replace("\n", " ")
        logger.debug("process_record start model=%s content=%r", self._settings.model, snippet)
        result = await self._complete_json(
            system_prompt=PROCESS_RECORD_SYSTEM_PROMPT,
            user_prompt=PROCESS_RECORD_USER_PROMPT.format(profile_context=profile_context, raw_content=raw_content),
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
            system_prompt=ARTIFACT_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=EXTRACT_ARTIFACTS_USER_PROMPT.format(
                profile_context=profile_context,
                raw_content=raw_content,
                record_json=json.dumps(record, ensure_ascii=False),
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
        *,
        stats_summary: str = "",
    ) -> str:
        logger.info("generate_report start type=%s records=%d", report_type, len(records))
        records_text = "\n".join(
            f"### {record.get('date', '')} [{record.get('emotion', '')}] #{record.get('tags', '')}\n"
            f"**摘要**: {record.get('summary', '')}\n"
            f"**样本类型**: {record.get('sample_type', '')} | "
            f"**问题本质**: {record.get('problem_essence', '')}\n"
            f"**策略**: {record.get('strategy', '')} → "
            f"**下一步**: {record.get('next_move', '')}\n"
            f"**验证信号**: {record.get('validation_signal', '')}\n"
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

        return await self._complete(
            system_prompt=REFLECTION_SYSTEM_PROMPT,
            user_prompt=GENERATE_REFLECTION_USER_PROMPT.format(
                profile_context=profile_context,
                records_summary=records_summary,
                focus_section=focus_section,
                style_section=style_section,
            ),
        )

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
                prompt = user_prompt + JSON_RETRY_SUFFIX
        logger.error("%s exhausted json retries; using fallback: %s", operation, last_error)
        return fallback


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        stripped = stripped[start : end + 1]
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
        "sample_type": DEFAULT_SAMPLE_TYPE,
        "problem_essence": "",
        "available_cards": "",
        "strategy": "",
        "next_move": "",
        "deadline": "",
        "validation_signal": "",
        "needs_clarification": False,
        "clarification_reason": "",
        "clarification_questions": [],
        "suggested_profile_updates": [],
        "note": "模型输出格式异常，已原样保留",
    }


def _empty_artifacts() -> dict[str, Any]:
    return {"todos": [], "decisions": [], "highlights": [], "experiments": [], "suggested_profile_updates": []}


def _normalize_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    normalized = _empty_artifacts()
    for key in normalized:
        value = result.get(key)
        normalized[key] = value if isinstance(value, list) else []
    return normalized
