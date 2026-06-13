"""Built-in project templates.

Follows the FeedPreset pattern: hardcoded presets with zero DB overhead.
Templates pre-fill description, tags, priority, and milestone scaffolding.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProjectTemplate:
    id: str
    label: str
    description: str
    priority: str = "medium"
    tags: str = ""
    milestones: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "priority": self.priority,
            "tags": self.tags,
            "milestones": list(self.milestones),
        }


# ── Solo templates ────────────────────────────────────────────────

SOLO_TEMPLATES: tuple[ProjectTemplate, ...] = (
    ProjectTemplate(
        id="solo_goal",
        label="个人目标",
        description="设定一个明确的个人目标，跟踪进展直到完成。",
        priority="high",
        tags="goal",
        milestones=("定义目标与衡量标准", "制定行动计划", "执行第一轮迭代", "复盘与调整"),
    ),
    ProjectTemplate(
        id="solo_learning",
        label="学习 / 阅读",
        description="系统性学习一个新领域或技能，记录笔记和产出。",
        tags="learning,reading",
        milestones=("收集资料与制定计划", "核心内容学习", "产出总结或实践项目"),
    ),
    ProjectTemplate(
        id="solo_health",
        label="健康 / 习惯",
        description="建立或改善一个健康习惯，跟踪日常执行情况。",
        tags="health,habit",
        milestones=("确定目标习惯", "连续 7 天执行", "30 天巩固"),
    ),
    ProjectTemplate(
        id="solo_creative",
        label="创作 / Side Project",
        description="一个创意项目或 side project，从构思到发布。",
        tags="creative,side-project",
        milestones=("构思与规划", "核心开发 / 创作", "测试与打磨", "发布"),
    ),
)

# ── Wolo templates ────────────────────────────────────────────────

WOLO_TEMPLATES: tuple[ProjectTemplate, ...] = (
    ProjectTemplate(
        id="wolo_deliverable",
        label="交付 / 上线",
        description="一个有明确交付物和截止日期的工作项目。",
        priority="high",
        tags="deliverable",
        milestones=("需求确认", "方案设计", "开发实现", "测试验收", "上线发布"),
    ),
    ProjectTemplate(
        id="wolo_initiative",
        label="团队倡议 / 改进",
        description="推动一个团队层面的改进或倡议落地。",
        tags="initiative,improvement",
        milestones=("问题定义与调研", "方案设计", "试点执行", "效果评估与推广"),
    ),
    ProjectTemplate(
        id="wolo_research",
        label="调研 / 评估",
        description="对技术方案、产品方向或市场进行系统调研。",
        tags="research",
        milestones=("明确调研范围", "信息收集", "分析与对比", "结论与建议"),
    ),
    ProjectTemplate(
        id="wolo_ops",
        label="运维 / 稳定性",
        description="提升系统稳定性、监控或运维效率。",
        tags="ops,stability",
        milestones=("现状评估", "方案制定", "工具 / 流程建设", "验证与沉淀"),
    ),
)

# ── Registry ──────────────────────────────────────────────────────

_ALL: dict[str, ProjectTemplate] = {t.id: t for t in SOLO_TEMPLATES + WOLO_TEMPLATES}


def get_template(template_id: str) -> ProjectTemplate | None:
    return _ALL.get(template_id)


def list_templates(app_type: str = "") -> list[ProjectTemplate]:
    """Return templates filtered by app type (solo/wolo), or all if empty."""
    if app_type == "solo":
        return list(SOLO_TEMPLATES)
    if app_type == "wolo":
        return list(WOLO_TEMPLATES)
    return list(SOLO_TEMPLATES) + list(WOLO_TEMPLATES)
