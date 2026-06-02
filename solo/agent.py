"""OpenHarness-backed model agents for solo."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiTextDeltaEvent,
    SupportsStreamingMessages,
)
from openharness.api.recording_client import wrap_with_model_call_recorder
from openharness.config import load_settings
from openharness.engine.messages import ConversationMessage
from openharness.ui.runtime import _resolve_api_client_from_settings
from openharness.utils.log import get_logger

logger = get_logger(__name__)


class OpenHarnessSoloAgent:
    """Self-log domain agent backed by OpenHarness provider/auth/client plumbing."""

    def __init__(
        self,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
        record_model_call: Callable[[str], None] | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        base_client = api_client or _resolve_api_client_from_settings(settings)
        self._client = wrap_with_model_call_recorder(base_client, record_model_call)

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
            system_prompt=_report_system_prompt(report_type),
            user_prompt="".join(user_prompt_parts),
            max_tokens=self._settings.max_tokens,
        )
        if not content.strip():
            raise RuntimeError("report generation returned empty response")
        return content

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

    async def extract_artifacts(
        self,
        record: dict[str, Any],
        raw_content: str,
        profile_context: str,
    ) -> dict[str, Any]:
        logger.debug("extract_artifacts start record_id=%s", record.get("id") or record.get("entry_id"))
        content = await self._complete(
            system_prompt=_ARTIFACT_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=(
                f"{profile_context}\n\n"
                f"## 原始输入\n{raw_content}\n\n"
                f"## 已结构化记录\n{json.dumps(record, ensure_ascii=False)}\n\n"
                "请只提取个人 artifacts，输出 JSON。"
            ),
        )
        result = _safe_parse_json(content)
        return _normalize_artifacts(result)

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
            "sample_type": "neutral",
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


_PROCESS_RECORD_SYSTEM_PROMPT = """你是一位深度理解人性的 AI 个人记录助手。你的任务不是替用户写抒情日记，而是把杂乱输入转成可用于行为迭代的高价值样本。

你拥有以下上下文信息作为参考：
1. **Soul**: 你的行为准则与人设。
2. **User Profile**: 用户的基础资料。
3. **Personal Memory**: 已沉淀的长效背景事实（家庭、工作、健康等）。
4. **Recent Observations**: 最近观察到但尚未确认为长效事实的偏好或动态。

### 核心原则
- **事实一致性**：检查用户新输入的内容是否与已知上下文矛盾。若发现矛盾（例如已记录用户在北京，但新记录说在上海家里），在 `note` 中指出，并在 `needs_clarification` 为 true 时询问。
- **系统迭代优先**：优先识别这条记录是否属于以下四类样本：`tension_success`（有张力的成功）、`aware_failure`（有觉察的失败）、`avoidance_design`（规避设计）、`neutral`（普通记录）。
- **记忆分层**：
    - **Memory (长效记忆)**：极其稳定、低频变动的事实（人际关系、工作头衔、慢性状况）。
    - **Observations (动态观察)**：高频变动、偏好、习惯、临时状态。
- **绝不猜测**：对模糊的人称、地点、事件，优先追问而非脑补。
- **记录过程而非自评**：优先提取触发场景、摩擦感、断裂点、跨越动作、环境设计和下一轮实验，不要放大自我评价。

### 输出格式 (严格 JSON)
{
  "date": "YYYY-MM-DD (仅当用户提到非今天的日期时输出)",
  "period": "凌晨/清晨/上午/中午/下午/傍晚/深夜 (仅当用户提到的时间与当前记录时间明显不符时输出)",
  "corrected_content": "修正后语病并补全上下文的原文",
  "summary": "一句极简的摘要",
  "tags": "标签1,标签2 (如：工作,家庭,情绪,健康)",
  "emotion": "积极/消极/中性/复杂",
  "events": "节日信息、纪念日或生日",
  "emotion_reason": "基于心理学视角的简短情绪分析",
  "related_people": "涉及人物",
  "related_places": "涉及地点",
  "sample_type": "tension_success/aware_failure/avoidance_design/neutral",
  "trigger_scene": "触发场景的一句话描述",
  "friction_signal": "身体/情绪/认知摩擦信号；若无可空",
  "awareness_timing": "当场/事后/未明确",
  "break_point": "真正卡住的位置；若无可空",
  "bridge_action": "最终跨过去的最小动作；若无可空",
  "environment_design": "绕开触发的环境改动；若无可空",
  "next_experiment": "下一轮最值得验证的一条行为实验；若无可空",
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
      "period": "时段",
      "events": "节日信息",
      "source": "补录"
    }
  ],
  "needs_clarification": false
}
"""

def _report_system_prompt(report_type: str) -> str:
    labels = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}
    period_label = labels.get(report_type, report_type)

    return f"""你是一位个人成长教练。请基于用户记录和迭代样本生成一份有深度、有洞察的个人{period_label}。

## 核心要求

**你不是在做数据统计搬运工。** 统计数据会在报告末尾由系统自动附加——你的职责是提供人类无法通过数据表直接看出的「洞察」和「叙事」。

你需要做的：
1. 从零散记录中**提炼出个人状态的叙事线**——本期的内在主题是什么
2. 识别**行为模式与变化趋势**——哪些触发在重复，哪些跨越在累积
3. 给出**具体可执行的下一步实验**——基于实际观察
4. 对潜在风险做**前瞻判断**——什么模式可能在下一周期恶化

## 输出结构

---

# 🌱 个人{period_label}

## 🎯 本期全貌

用一段连贯的叙事（5-8句）概括本期个人状态。要回答：
- 核心关注点是什么？有没有变化？
- 哪些方面有进展，哪些在原地踏步？
- 和上一周期相比，整体状态是上升、平稳还是下滑？

## ✨ 有效跨越 & 突破

只列真正重要的跨越（2-5条）：

| 跨越 | 领域 | 为什么这是一个突破 |
|------|------|-------------------|
| ... | ... | 一句话说明为什么不是日常操作而是真正的跨越 |

## 🔍 深度洞察

这是报告最核心的部分。请识别并展开 2-4 个洞察，例如：
- 某个反复出现的触发模式——触发的共性是什么、根源在哪
- 某次跨越背后的真正支撑条件——不是意志力而是环境/结构
- 规避设计的有效性评估——哪些在生效，哪些形同虚设
- 隐性的情绪/能量消耗模式——没有被正式识别但一直在起作用

每个洞察用 **加粗标题** + 2-3句分析展开。

## 🚧 需要关注的信号

| 信号 | 严重度 | 说明 | 建议行动 |
|------|--------|------|----------|
| ... | 🔴/🟡 | 为什么值得警惕 | ... |

只列确实构成风险的模式（1-4条）。

## 🔬 下期实验建议

基于以上分析，给出 2-4 个具体实验：
1. **实验内容** — 因为观察到...所以建议尝试...验证信号是...

---

## 写作原则
- **叙事优先**：先讲故事、给判断，再用具体记录佐证
- **引用具体证据**：每个结论引用日期或具体事件
- **诚实**: 数据不足就明说，不编造不臆测
- **不要重复统计数据**: 情绪分布、标签云、活跃热力图等会由系统自动附在报告末尾，你不需要输出这些
- **温暖但不空泛**: 关怀但拒绝"继续加油""相信你可以"等空话
- **关注模式**: 重点识别触发-断裂-跨越的循环
"""

_DAILY_QUESTION_SYSTEM_PROMPT = """你是一位深度理解人性的 AI 个人记录助手。
你的目标是通过一个精准、温暖且有启发性的问题，引导用户开启今天的记录，并优先采集可迭代样本。

### 引导原则
1. **基于上下文**：参考用户的 User Profile 和最近的 Observation。如果用户最近在忙某个项目，或者提到了某种困扰，问题应与之相关。
2. **避免陈词滥调**：不要问“今天过得怎么样”，要优先问触发场景、差点失手的瞬间、或者今天有没有成功绕开的诱因。
3. **保持简短**：问题应在一句话以内。
4. **人本关怀**：展现出你记得他/她之前说过的话。

示例：
- “昨天的架构评审中，那个关于扩展性的争议最后是怎么解决的？”
- “你上周提到最近睡眠不太好，今天感觉精神状态恢复一些了吗？”
- “今天在处理那个紧急 Bug 的间隙，有没有哪怕一瞬间让你觉得产生成就感？”
"""

_REFLECTION_SYSTEM_PROMPT = """你是一位个人成长教练。
你的任务是基于用户最近的心理状态和生活记录，提出 3 个能够推动行为系统迭代的深度复盘问题。

### 复盘原则
1. **见微知著**：从碎片记录中发现重复触发、摩擦信号、断裂点和规避设计。
2. **多维视角**：如果用户只关注了客观事实，试着引导他/她关注感受；如果只关注了感受，试着引导他/她关注行动。
3. **针对性**：如果提供了 focus 领域，请严格围绕该领域提问。
4. **启发性**：问题不应有标准答案，而是为了开启下一轮实验。
"""

_ARTIFACT_EXTRACTION_SYSTEM_PROMPT = """你是一位个人事务与行为实验 artifacts 提取器。输入包含原始文本和已经整理好的个人记录。

你的唯一职责是从事实中提取可执行的个人待办事项与可验证的行为实验，不要重写主记录，也不要推测缺失事实。

### 提取原则
- 只提取用户明确提到或暗示的待办/计划/承诺
- 涉及约会、预约、购物清单、健康检查、家务、人情往来等个人事务
- 不要将已完成的事情标记为待办
- 不要为模糊的愿望创建待办（如"想减肥"不算，但"明天开始跑步"算）

### 输出格式 (严格 JSON)
{
  "todos": [
    {
      "title": "明确可执行的待办",
      "category": "所属分类",
      "priority": "优先级",
      "due_date": "YYYY-MM-DD 或空"
    }
  ],
  "experiments": [
    {
      "title": "实验标题",
      "hypothesis": "准备验证的假设",
      "trigger": "触发条件",
      "desired_action": "希望执行的动作",
      "environment_design": "用于绕开本能的环境设计",
      "success_criteria": "如何判断实验有效",
      "observation_window": "观察窗口，如 7天"
    }
  ],
  "suggested_profile_updates": [
    {
      "category": "分类",
      "entity_type": "实体类型",
      "entity_name": "名称",
      "suggested_value": "新发现或更新的个人事实",
      "confidence": "high/medium/low"
    }
  ]
}

若没有对应内容，输出空数组。只输出 JSON。
"""


def _normalize_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {"todos": [], "experiments": [], "suggested_profile_updates": []}
    for key in normalized:
        value = result.get(key)
        normalized[key] = value if isinstance(value, list) else []
    return normalized
