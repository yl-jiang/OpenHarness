"""Shell injection processor for skill templates.

Parses ``!{cmd}`` segments inside skill content, performs context-sensitive
argument substitution (shell-escaped inside commands, raw inside text),
authorizes every command up-front, then executes them in order via the
existing :class:`BashTool` so the resulting output can be spliced back into
the prompt that is submitted to the model.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from openharness.tools.base import ToolExecutionContext
from openharness.tools.bash_tool import BashTool, BashToolInput

if TYPE_CHECKING:
    from openharness.commands.core import CommandContext
    from openharness.skills.types import SkillDefinition


__all__ = [
    "SkillShellInjectionError",
    "InjectionSegment",
    "extract_injections",
    "render_skill_prompt_with_shell",
]


_SKILL_ARG_REGEX = re.compile(r"""(?:\[Image\s+\d+\]|"[^"]*"|'[^']*'|[^\s"']+)""")
_PLACEHOLDER_REGEX = re.compile(r"\$(\d+)")
_INJECTION_TOKEN_REGEX = re.compile(r"!\{|[{}]")


class SkillShellInjectionError(Exception):
    """Raised when a skill's shell injection cannot be safely rendered."""


@dataclass(frozen=True)
class InjectionSegment:
    """A single parsed segment of a skill body.

    ``kind == "text"`` keeps the raw markdown (with placeholders intact).
    ``kind == "shell"`` carries the inner command string (without ``!{`` /
    ``}`` wrappers).  Empty shell segments are preserved so callers can
    short-circuit them without executing anything.
    """

    kind: Literal["text", "shell"]
    value: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def extract_injections(content: str) -> list[InjectionSegment]:
    """Split skill ``content`` into text / shell segments.

    Uses regex tokenization plus brace-depth tracking so
    ``!{python -c 'print({"a": 1})'}`` parses as a single shell command
    with the inner braces preserved. Unterminated ``!{`` raises
    :class:`SkillShellInjectionError`.
    """

    segments: list[InjectionSegment] = []
    text_start = 0
    depth = 0
    shell_start: int | None = None

    for match in _INJECTION_TOKEN_REGEX.finditer(content):
        token = match.group(0)
        if depth == 0:
            if token != "!{":
                continue
            if match.start() > text_start:
                segments.append(InjectionSegment("text", content[text_start : match.start()]))
            shell_start = match.end()
            depth = 1
            continue

        if token in {"!{", "{"}:
            depth += 1
            continue

        depth -= 1
        if depth == 0:
            assert shell_start is not None
            segments.append(InjectionSegment("shell", content[shell_start : match.start()]))
            text_start = match.end()
            shell_start = None

    if depth != 0:
        raise SkillShellInjectionError(
            "Unterminated shell injection: missing closing '}' for '!{'."
        )

    if text_start < len(content):
        segments.append(InjectionSegment("text", content[text_start:]))
    return segments


# ---------------------------------------------------------------------------
# Argument substitution
# ---------------------------------------------------------------------------


def _tokenize(raw_args: str) -> list[str]:
    tokens = _SKILL_ARG_REGEX.findall(raw_args)
    return [re.sub(r'^["\']|["\']$', "", token) for token in tokens]


def _substitute_text(text: str, *, tokens: list[str], raw_args: str) -> str:
    """Apply ``$1``/``$ARGUMENTS`` substitution to raw text (unescaped).

    Mirrors ``render_skill_template`` semantics but operates over the union
    of all text segments so the "last positional swallows the remainder"
    rule keeps working across the whole template.
    """

    placeholders = [int(m) for m in _PLACEHOLDER_REGEX.findall(text)]
    last_position = max(placeholders, default=0)

    def replace(match: re.Match[str]) -> str:
        position = int(match.group(1))
        index = position - 1
        if index >= len(tokens):
            return ""
        if position == last_position:
            return " ".join(tokens[index:])
        return tokens[index]

    rendered = _PLACEHOLDER_REGEX.sub(replace, text)
    rendered = rendered.replace("${ARGUMENTS}", raw_args).replace("$ARGUMENTS", raw_args)
    return rendered


def _substitute_shell(command: str, *, tokens: list[str]) -> str:
    """Apply ``$1``/``$ARGUMENTS`` substitution with shell-escaping.

    Critical security boundary: every value substituted into a command
    string is run through :func:`shlex.quote` to defeat command-injection
    via unsanitised positional arguments.  ``$ARGUMENTS`` keeps each token
    as a separate quoted word so word boundaries survive.
    """

    placeholders = [int(m) for m in _PLACEHOLDER_REGEX.findall(command)]
    last_position = max(placeholders, default=0)

    def replace(match: re.Match[str]) -> str:
        position = int(match.group(1))
        index = position - 1
        if index >= len(tokens):
            return ""
        if position == last_position:
            return " ".join(shlex.quote(t) for t in tokens[index:])
        return shlex.quote(tokens[index])

    rendered = _PLACEHOLDER_REGEX.sub(replace, command)
    quoted_all = " ".join(shlex.quote(t) for t in tokens)
    rendered = rendered.replace("${ARGUMENTS}", quoted_all).replace("$ARGUMENTS", quoted_all)
    return rendered


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def render_skill_prompt_with_shell(
    skill: "SkillDefinition",
    args: str,
    *,
    context: "CommandContext",
) -> str:
    """Render *skill* into a prompt string, expanding ``!{cmd}`` injections.

    Raises :class:`SkillShellInjectionError` for any of:
      * ``!{...}`` present but ``shell-injection: true`` not set;
      * unterminated ``!{``;
      * any pre-execution permission denial (no command runs in that case).
    """

    segments = extract_injections(skill.content)
    has_injection = any(seg.kind == "shell" for seg in segments)

    if not has_injection:
        return skill.content

    if not skill.shell_injection:
        raise SkillShellInjectionError(
            f"Skill {skill.name} contains shell injection but 'shell-injection' "
            "is not enabled in its frontmatter."
        )

    raw_args = args.strip()
    tokens = _tokenize(raw_args)

    # Phase 1: resolve all final command strings (substitution happens before
    # authorization so we authorise the *actual* command the user will run).
    commands: list[tuple[int, str]] = []
    for idx, segment in enumerate(segments):
        if segment.kind != "shell":
            continue
        inner = segment.value.strip()
        if not inner:
            continue
        final_command = _substitute_shell(segment.value, tokens=tokens)
        commands.append((idx, final_command))

    # Phase 2: stage-authorize every command up-front.  Bail out before any
    # side-effects if a single one is rejected.
    engine = context.engine
    for _, command in commands:
        decision = await engine.authorize_tool(
            "bash",
            is_read_only=False,
            command=command,
        )
        if not decision.allowed:
            reason = decision.reason or "permission denied"
            raise SkillShellInjectionError(
                f"Shell injection denied for skill {skill.name}: {reason} "
                f"(command: {command!r})."
            )

    # Phase 3: execute commands in declared order.
    bash_tool = _resolve_bash_tool(context)
    outputs: dict[int, str] = {}
    cwd_path = Path(str(context.cwd)).expanduser().resolve()
    exec_context = ToolExecutionContext(
        cwd=cwd_path,
        metadata={"origin": "skill_shell_injection", "skill": skill.name},
        approval_coordinator=engine.approval_coordinator,
    )

    for idx, command in commands:
        try:
            result = await bash_tool.execute(
                BashToolInput(command=command),
                exec_context,
            )
        except Exception as exc:  # pragma: no cover - defensive: spawn failure
            raise SkillShellInjectionError(
                f"Shell injection failed for skill {skill.name} on command "
                f"{command!r}: {exc}"
            ) from exc

        outputs[idx] = _format_command_output(command, result)

    # Phase 4: stitch text + outputs in original order, applying *unescaped*
    # arg substitution to text segments only.
    pieces: list[str] = []
    for idx, segment in enumerate(segments):
        if segment.kind == "text":
            pieces.append(_substitute_text(segment.value, tokens=tokens, raw_args=raw_args))
        elif idx in outputs:
            pieces.append(outputs[idx])
        # else: empty `!{}` — replaced by nothing.

    rendered = "".join(pieces)

    # Preserve the historical "append unmatched args" behaviour from
    # render_skill_template so a skill with no $ placeholders still sees
    # the user's args.
    uses_placeholder = bool(_PLACEHOLDER_REGEX.search(skill.content)) or (
        "${ARGUMENTS}" in skill.content or "$ARGUMENTS" in skill.content
    )
    if not uses_placeholder and raw_args:
        rendered = f"{rendered}\n\n{raw_args}"
    return rendered


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_bash_tool(context: "CommandContext") -> BashTool:
    registry = context.tool_registry
    if registry is not None:
        tool = registry.get("bash")
        if isinstance(tool, BashTool):
            return tool
    return BashTool()


def _format_command_output(command: str, result) -> str:
    """Format a tool result for inline injection back into the prompt."""

    output = (result.output or "").rstrip()
    if result.is_error:
        if output:
            return f"{output}\n[Shell command {command!r} failed]"
        return f"[Shell command {command!r} failed]"
    return output
