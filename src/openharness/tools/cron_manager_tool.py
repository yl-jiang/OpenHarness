"""Unified cron manager tool — list, create, update, toggle, and delete cron jobs."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from openharness.services.cron import (
    delete_cron_job,
    get_cron_job,
    load_cron_jobs,
    set_job_enabled,
    upsert_cron_job,
    validate_cron_expression,
    validate_timezone,
)
from openharness.services.cron_scheduler import is_scheduler_running
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class CronManagerToolInput(BaseModel):
    """Arguments for the cron_manager tool."""

    action: Literal["list", "create", "update", "toggle", "delete"] = Field(
        description=(
            "Action to perform:\n"
            "  list   — list all configured cron jobs with schedule and status\n"
            "  create — create or replace a cron job\n"
            "  update — change schedule, command, cwd, or payload of an existing job\n"
            "  toggle — enable or disable a job without deleting it\n"
            "  delete — permanently remove a cron job by name"
        ),
    )
    name: Optional[str] = Field(
        default=None,
        description="Cron job name. Required for create/update/toggle/delete.",
    )
    schedule: Optional[str] = Field(
        default=None,
        description=(
            "Standard 5-field cron expression (e.g. '*/5 * * * *' for every 5 minutes, "
            "'0 9 * * 1-5' for weekdays at 9 am). "
            "Required for 'create'; optional for 'update'."
        ),
    )
    command: Optional[str] = Field(
        default=None,
        description=(
            "Shell command to run when the job fires. "
            "For 'create', provide command or message/payload."
        ),
    )
    message: Optional[str] = Field(
        default=None,
        description="Instruction for an agent_turn cron job.",
    )
    timezone: Optional[str] = Field(
        default=None,
        description="IANA timezone for interpreting the cron schedule.",
    )
    cwd: Optional[str] = Field(
        default=None,
        description="Working directory for the command. Defaults to the current session directory.",
    )
    enabled: Optional[bool] = Field(
        default=None,
        description=(
            "For 'create': whether the job starts active (default true). "
            "For 'toggle': true to enable, false to disable."
        ),
    )
    payload: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional agent_turn payload, e.g. "
            "{'kind': 'agent_turn', 'message': 'check GitHub', 'deliver': true, "
            "'channel': 'feishu', 'to': 'ou_xxx'}."
        ),
    )
    notify: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional notification target, e.g. "
            "{'type': 'feishu_dm', 'user_open_id': 'ou_xxx'}."
        ),
    )


class CronManagerTool(BaseTool):
    """Unified cron manager: list, create, update, toggle, and delete cron jobs."""

    name = "cron_manager"
    description = (
        "Manage local cron-style scheduled jobs: list all jobs, create a shell-command "
        "or agent_turn job, update an existing job, enable/disable a job, "
        "or delete it permanently.\n\n"
        "Actions:\n"
        "  list   — show all jobs with schedule, status, and last/next run times\n"
        "  create — create or replace a job (name + schedule + command/message required)\n"
        "  update — change schedule, command, cwd, timezone, payload, or notify target\n"
        "  toggle — enable or disable a job (name + enabled required)\n"
        "  delete — remove a job by name\n\n"
        "Always use action='list' first when unsure of job names. "
        "Run 'oh cron start' to start the scheduler daemon."
    )
    input_model = CronManagerToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "update", "toggle", "delete"],
                        "description": (
                            "Action to perform: "
                            "'list' to inspect jobs, "
                            "'create' to add/replace a job, "
                            "'update' to modify an existing job, "
                            "'toggle' to enable/disable, "
                            "'delete' to remove."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": "Cron job name. Required for create/update/toggle/delete.",
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "5-field cron expression, e.g. '*/5 * * * *' (every 5 min) "
                            "or '0 9 * * 1-5' (weekdays 9 am). "
                            "Required for 'create'; optional for 'update'."
                        ),
                    },
                    "command": {
                        "type": "string",
                        "description": (
                            "Shell command to execute. "
                            "For 'create', provide command or message/payload."
                        ),
                    },
                    "message": {
                        "type": "string",
                        "description": "Instruction for an agent_turn cron job.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone for interpreting the cron schedule.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory override for the command.",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": (
                            "For 'create': initial enabled state (default true). "
                            "For 'toggle': true to enable, false to disable."
                        ),
                    },
                    "payload": {
                        "type": "object",
                        "description": (
                            "Optional agent_turn payload. Example: "
                            "{'kind': 'agent_turn', 'message': 'check GitHub', "
                            "'deliver': true, 'channel': 'feishu', 'to': 'ou_xxx'}."
                        ),
                    },
                    "notify": {
                        "type": "object",
                        "description": (
                            "Optional notification target. Example: "
                            "{'type': 'feishu_dm', 'user_open_id': 'ou_xxx'}."
                        ),
                    },
                },
                "required": ["action"],
            },
        }

    def is_read_only(self, arguments: CronManagerToolInput) -> bool:
        return arguments.action == "list"

    async def execute(self, arguments: CronManagerToolInput, context: ToolExecutionContext) -> ToolResult:
        if arguments.action == "list":
            return self._list()
        if arguments.action == "create":
            return self._create(arguments, context)
        if arguments.action == "update":
            return self._update(arguments)
        if arguments.action == "toggle":
            return self._toggle(arguments)
        if arguments.action == "delete":
            return self._delete(arguments)
        return ToolResult(
            output=f"Unknown action '{arguments.action}'. Valid actions: list, create, update, toggle, delete.",
            is_error=True,
        )

    def _with_scheduler_hint(self, output: str, *, active: bool) -> ToolResult:
        if active and not is_scheduler_running():
            output = (
                f"{output}\n"
                "Scheduler is stopped. This change is saved, but jobs will not run automatically "
                "until you start it with 'oh cron start'."
            )
        return ToolResult(output=output)

    # ── list ────────────────────────────────────────────────────────────────

    def _list(self) -> ToolResult:
        jobs = load_cron_jobs()
        if not jobs:
            return ToolResult(output="No cron jobs configured.")

        scheduler = "running" if is_scheduler_running() else "stopped"
        lines = [f"Scheduler: {scheduler}", ""]

        for job in jobs:
            enabled = "on" if job.get("enabled", True) else "off"
            last_run = job.get("last_run", "never")
            if last_run != "never":
                last_run = last_run[:19]
            next_run = job.get("next_run", "n/a")
            if next_run != "n/a":
                next_run = next_run[:19]
            last_status = job.get("last_status", "")
            status_str = f" ({last_status})" if last_status else ""
            notify = job.get("notify")
            notify_line = ""
            if isinstance(notify, dict):
                notify_type = notify.get("type", "?")
                target = notify.get("user_open_id") or notify.get("open_id") or notify.get("to") or "?"
                notify_line = f"\n     notify: {notify_type} -> {target}"
            payload = job.get("payload")
            payload_line = ""
            if isinstance(payload, dict):
                payload_line = (
                    f"\n     payload: {payload.get('kind', 'agent_turn')} -> "
                    f"{payload.get('channel', '?')}:{payload.get('to', '?')}"
                )
            timezone = f" ({job['timezone']})" if job.get("timezone") else ""
            command = job.get("command") or "(agent_turn)"
            lines.append(
                f"[{enabled}] {job['name']}  {job.get('schedule', '?')}{timezone}\n"
                f"     cmd: {command}"
                f"{payload_line}"
                f"{notify_line}\n"
                f"     last: {last_run}{status_str}  next: {next_run}"
            )
        return ToolResult(output="\n".join(lines))

    # ── create ───────────────────────────────────────────────────────────────

    def _create(self, arguments: CronManagerToolInput, context: ToolExecutionContext) -> ToolResult:
        if not arguments.name:
            return ToolResult(output="name is required for action='create'.", is_error=True)
        if not arguments.schedule:
            return ToolResult(output="schedule is required for action='create'.", is_error=True)

        if not validate_cron_expression(arguments.schedule):
            return ToolResult(
                output=(
                    f"Invalid cron expression: {arguments.schedule!r}\n"
                    "Use standard 5-field format: minute hour day month weekday\n"
                    "Examples: '*/5 * * * *' (every 5 min), '0 9 * * 1-5' (weekdays 9 am)"
                ),
                is_error=True,
            )
        if not validate_timezone(arguments.timezone):
            return ToolResult(output=f"Invalid timezone: {arguments.timezone!r}", is_error=True)

        payload = dict(arguments.payload or {})
        if arguments.message:
            payload.setdefault("kind", "agent_turn")
            payload.setdefault("message", arguments.message)
        if arguments.notify is not None:
            payload.setdefault("deliver", True)
            if str(arguments.notify.get("type") or "").strip().lower() == "feishu_dm":
                payload.setdefault("channel", "feishu")
                payload.setdefault(
                    "to",
                    arguments.notify.get("user_open_id") or arguments.notify.get("open_id"),
                )

        if payload and not payload.get("message") and not arguments.command:
            return ToolResult(output="Cron job requires payload.message, message, or command.", is_error=True)
        if not payload and not arguments.command:
            return ToolResult(output="Cron job requires command or message.", is_error=True)

        enabled = arguments.enabled if arguments.enabled is not None else True
        job: dict[str, Any] = {
            "name": arguments.name,
            "schedule": arguments.schedule,
            "cwd": arguments.cwd or str(context.cwd),
            "enabled": enabled,
        }
        if arguments.timezone:
            job["timezone"] = arguments.timezone
        if arguments.command is not None:
            job["command"] = arguments.command
        if payload:
            payload.setdefault("kind", "agent_turn")
            job["payload"] = payload
        if arguments.notify is not None:
            job["notify"] = arguments.notify
        upsert_cron_job(job)
        status = "enabled" if enabled else "disabled"
        return self._with_scheduler_hint(
            f"Cron job '{arguments.name}' created [{arguments.schedule}] ({status}).",
            active=enabled,
        )

    # ── update ───────────────────────────────────────────────────────────────

    def _update(self, arguments: CronManagerToolInput) -> ToolResult:
        if not arguments.name:
            return ToolResult(output="name is required for action='update'.", is_error=True)

        job = get_cron_job(arguments.name)
        if job is None:
            return ToolResult(
                output=f"Cron job '{arguments.name}' not found. Use action='list' to see available jobs.",
                is_error=True,
            )

        updated = dict(job)
        changed: list[str] = []

        if arguments.schedule is not None:
            if not validate_cron_expression(arguments.schedule):
                return ToolResult(
                    output=(
                        f"Invalid cron expression: {arguments.schedule!r}\n"
                        "Use standard 5-field format: minute hour day month weekday"
                    ),
                    is_error=True,
                )
            updated["schedule"] = arguments.schedule
            changed.append("schedule")

        if arguments.timezone is not None:
            if not validate_timezone(arguments.timezone):
                return ToolResult(output=f"Invalid timezone: {arguments.timezone!r}", is_error=True)
            updated["timezone"] = arguments.timezone
            changed.append("timezone")

        if arguments.command is not None:
            updated["command"] = arguments.command
            changed.append("command")

        if arguments.cwd is not None:
            updated["cwd"] = arguments.cwd
            changed.append("cwd")

        if arguments.payload is not None:
            updated["payload"] = dict(arguments.payload)
            changed.append("payload")

        if arguments.message is not None:
            payload = dict(updated.get("payload") or {})
            payload.setdefault("kind", "agent_turn")
            payload["message"] = arguments.message
            updated["payload"] = payload
            changed.append("message")

        if arguments.notify is not None:
            updated["notify"] = arguments.notify
            changed.append("notify")

        if not changed:
            return ToolResult(
                output=(
                    "No changes provided. Specify at least one of: "
                    "schedule, command, cwd, timezone, payload, message, notify."
                ),
                is_error=True,
            )

        upsert_cron_job(updated)
        return self._with_scheduler_hint(
            f"Cron job '{arguments.name}' updated ({', '.join(changed)}).",
            active=bool(updated.get("enabled", True)),
        )

    # ── toggle ───────────────────────────────────────────────────────────────

    def _toggle(self, arguments: CronManagerToolInput) -> ToolResult:
        if not arguments.name:
            return ToolResult(output="name is required for action='toggle'.", is_error=True)
        if arguments.enabled is None:
            return ToolResult(
                output="enabled is required for action='toggle' (true to enable, false to disable).",
                is_error=True,
            )

        if not set_job_enabled(arguments.name, arguments.enabled):
            return ToolResult(
                output=f"Cron job '{arguments.name}' not found. Use action='list' to see available jobs.",
                is_error=True,
            )
        state = "enabled" if arguments.enabled else "disabled"
        return self._with_scheduler_hint(
            f"Cron job '{arguments.name}' is now {state}.",
            active=arguments.enabled,
        )

    # ── delete ───────────────────────────────────────────────────────────────

    def _delete(self, arguments: CronManagerToolInput) -> ToolResult:
        if not arguments.name:
            return ToolResult(output="name is required for action='delete'.", is_error=True)

        if not delete_cron_job(arguments.name):
            return ToolResult(
                output=f"Cron job '{arguments.name}' not found. Use action='list' to see available jobs.",
                is_error=True,
            )
        return ToolResult(output=f"Cron job '{arguments.name}' deleted.")
