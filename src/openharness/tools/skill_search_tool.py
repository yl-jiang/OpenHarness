"""Search available skills by natural language query."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from openharness.skills.loader import load_skill_registry_cached
from openharness.skills.search import find_relevant_skills
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SkillSearchInput(BaseModel):
    """Arguments for skill_search."""

    query: str = Field(
        description=(
            "Natural language query describing the task or skill you are looking "
            "for (e.g. '帮我写周报', 'send a Lark message', 'code review')."
        ),
    )
    limit: int = Field(
        default=8,
        description="Maximum number of results to return (default 8).",
    )
    tag: Optional[str] = Field(
        default=None,
        description=(
            "Optional tag filter — only skills carrying this tag are considered. "
            "Leave unset to search across all skills."
        ),
    )


class SkillSearchTool(BaseTool):
    """Hybrid (BM25 + heuristic) skill search."""

    name = "skill_search"
    description = (
        "Search available skills by natural language. Uses a hybrid ranking "
        "strategy (BM25 + heuristic token matching fused via Reciprocal Rank "
        "Fusion) so synonyms, Chinese, and partial matches are handled. "
        "Use this instead of scanning the full `skill_list` when the user's "
        "intent is specific or the catalogue is large."
    )
    input_model = SkillSearchInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "default": 8,
                    },
                    "tag": {
                        "type": "string",
                        "description": (
                            "Optional tag filter — only skills with this tag "
                            "are considered."
                        ),
                    },
                },
                "required": ["query"],
            },
        }

    def is_read_only(self, arguments: SkillSearchInput) -> bool:
        return True

    async def execute(
        self, arguments: SkillSearchInput, context: ToolExecutionContext
    ) -> ToolResult:
        if not arguments.query.strip():
            return ToolResult(
                output="query must be a non-empty natural language description.",
                is_error=True,
            )

        registry = load_skill_registry_cached(
            context.metadata.get("skill_registry_cwd", context.cwd),
            extra_skill_dirs=context.metadata.get("extra_skill_dirs"),
            extra_plugin_roots=context.metadata.get("extra_plugin_roots"),
        )

        results = find_relevant_skills(
            arguments.query,
            registry,
            max_results=arguments.limit,
            tag_filter=arguments.tag,
        )

        if not results:
            hint = (
                "Try different keywords"
                if not arguments.tag
                else "Try a different tag or drop the tag filter"
            )
            return ToolResult(
                output=(
                    f"No skills matched '{arguments.query}'. "
                    f"{hint}, or use `skill_list` to see all available skills."
                ),
            )

        lines = [f"Found {len(results)} skill(s) matching '{arguments.query}':", ""]
        max_score = max(r.score for r in results) or 1.0
        for result in results:
            skill = result.skill
            display_score = result.score / max_score
            tag_part = f"  tags: {','.join(skill.tags)}" if skill.tags else ""
            lines.append(
                f"  {skill.name}  [{skill.source}]  score={display_score:.2f}{tag_part}"
            )
            if skill.description:
                lines.append(f"    {skill.description}")
        lines.append("")
        lines.append(
            "Use `skill_load(name='<skill_name>')` to load a skill's instructions."
        )
        return ToolResult(output="\n".join(lines))
