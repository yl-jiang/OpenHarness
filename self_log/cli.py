"""CLI entry point for the standalone self-log app."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys

import typer

from openharness.auth.manager import AuthManager
from openharness.config import load_settings

from self_log.agent import OpenHarnessSelfLogAgent
from self_log.config import load_config, save_config
from self_log.gateway.service import (
    SelfLogGatewayService,
    gateway_status,
    start_gateway_process,
    stop_gateway_process,
)
from self_log.models import SelfLogConfig
from self_log.processor import SelfLogProcessor
from self_log.store import SelfLogStore
from self_log.workspace import get_config_path, get_workspace_root, initialize_workspace, workspace_health

app = typer.Typer(
    name="self-log",
    help="self-log: a standalone personal logging app built on OpenHarness.",
    add_completion=False,
)
gateway_app = typer.Typer(name="gateway", help="Run the self-log gateway")
app.add_typer(gateway_app)

_INTERACTIVE_CHANNELS = ("telegram", "slack", "discord", "feishu")
_WORKSPACE_HELP = "Path to the self-log workspace (defaults to ~/.self-log)"


@app.command("init")
def init_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    root = initialize_workspace(workspace)
    SelfLogStore(root).initialize()
    print(f"Initialized self-log at {root}")


@app.command("config")
def config_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    root = initialize_workspace(workspace)
    existing = load_config(root)
    provider_profile = _prompt_provider_profile(existing.provider_profile)
    enabled_channels, channel_configs = _prompt_channels(existing, target="self-log")
    send_progress = _confirm_prompt("Send progress updates to channels?", default=existing.send_progress)
    send_tool_hints = _confirm_prompt("Send tool hints to channels?", default=existing.send_tool_hints)
    config = existing.model_copy(
        update={
            "provider_profile": provider_profile,
            "enabled_channels": enabled_channels,
            "channel_configs": channel_configs,
            "send_progress": send_progress,
            "send_tool_hints": send_tool_hints,
        }
    )
    save_config(config, root)
    print(f"Saved self-log config to {get_config_path(root)}")


@app.command("record")
def record_cmd(
    content: str = typer.Argument(...),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    entry = SelfLogStore(workspace).record(content)
    print(f"Recorded self-log entry {entry.id}")


@app.command("list")
def list_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum entries to show"),
) -> None:
    for entry in SelfLogStore(workspace).list_entries(limit=limit):
        print(f"{entry.created_at} [{entry.channel}] {entry.content}")


@app.command("process")
def process_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    store = SelfLogStore(workspace)
    agent = OpenHarnessSelfLogAgent(profile=profile or load_config(workspace).provider_profile)
    result = asyncio.run(SelfLogProcessor(store, agent).process_pending(backfill_missing_yesterday=True))
    print(f"Processed {result.auto_processed} record(s), pending {result.pending_confirmations}.")


@app.command("view")
def view_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum records to show"),
) -> None:
    for record in SelfLogStore(workspace).list_records(limit=limit):
        print(f"{record.date} {record.emotion} [{record.source}] [{record.tags}] {record.summary}")


@app.command("report")
def report_cmd(
    report_type: str = typer.Argument(..., help="weekly, monthly, or yearly"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    store = SelfLogStore(workspace)
    agent = OpenHarnessSelfLogAgent(profile=profile or load_config(workspace).provider_profile)
    report = asyncio.run(SelfLogProcessor(store, agent).generate_report(report_type))
    print(report.content)


@app.command("status")
def status_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    status = SelfLogStore(workspace).status()
    gateway = gateway_status(workspace=workspace)
    print(
        f"self-log: entries={status['entries']} | records={status['records']} | "
        f"pending={status['pending_confirmations']} | gateway={'running' if gateway.running else 'stopped'} | "
        f"path={status['path']}"
    )


@app.command("start")
def start_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    pid = start_gateway_process(cwd, workspace)
    print(f"self-log gateway started (pid={pid})")


@app.command("stop")
def stop_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    if stop_gateway_process(cwd, workspace):
        print("self-log gateway stopped.")
        return
    print("self-log gateway is not running.")


@app.command("doctor")
def doctor_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    health = workspace_health(get_workspace_root(workspace))
    for name, ok in health.items():
        print(f"{name}: {'ok' if ok else 'missing'}")


@gateway_app.command("run")
def gateway_run_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    _configure_gateway_logging(workspace)
    raise typer.Exit(asyncio.run(SelfLogGatewayService(cwd, workspace).run_foreground()))


def _configure_gateway_logging(workspace: str | Path | None = None) -> None:
    """Configure foreground gateway logging."""
    config = load_config(workspace)
    level_name = str(config.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = _SelfLogFormatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Silence noisy third-party loggers that pollute gateway output
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class _SelfLogFormatter(logging.Formatter):
    """Formatter that shortens logger names for cleaner terminal output."""

    # Maps known verbose prefix → short label
    _PREFIX_MAP = {
        "self_log.gateway.": "gateway.",
        "self_log.": "self_log.",
        "openharness.channels.impl.": "channel.",
        "openharness.channels.": "channels.",
        "openharness.": "oh.",
    }

    def format(self, record: logging.LogRecord) -> str:
        name = record.name
        for prefix, short in self._PREFIX_MAP.items():
            if name.startswith(prefix):
                record.name = short + name[len(prefix):]
                break
        return super().format(record)


def _can_use_questionary() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if sys.stdin is not sys.__stdin__ or sys.stdout is not sys.__stdout__:
        return False
    try:
        import questionary  # noqa: F401
    except ImportError:
        return False
    return True


def _confirm_prompt(message: str, *, default: bool = False) -> bool:
    if _can_use_questionary():
        import questionary

        result = questionary.confirm(message, default=default).ask()
        if result is None:
            raise typer.Abort()
        return bool(result)
    return typer.confirm(message, default=default)


def _text_prompt(message: str, *, default: str = "") -> str:
    if _can_use_questionary():
        import questionary

        result = questionary.text(message, default=default).ask()
        if result is None:
            raise typer.Abort()
        return str(result)
    return typer.prompt(message, default=default)


def _select_from_menu(
    title: str,
    options: list[tuple[str, str]],
    *,
    default_value: str | None = None,
) -> str:
    if _can_use_questionary():
        import questionary

        choices = [
            questionary.Choice(title=label, value=value, checked=(value == default_value))
            for value, label in options
        ]
        result = questionary.select(title, choices=choices, default=default_value).ask()
        if result is None:
            raise typer.Abort()
        return str(result)
    print(title)
    default_index = 1
    for index, (value, label) in enumerate(options, 1):
        marker = " (default)" if value == default_value else ""
        if value == default_value:
            default_index = index
        print(f"  {index}. {label}{marker}")
    raw = typer.prompt("Choose", default=str(default_index))
    try:
        return options[int(raw) - 1][0]
    except (ValueError, IndexError):
        raise typer.BadParameter(f"Invalid selection: {raw}") from None


def _prompt_provider_profile(default_value: str) -> str:
    statuses = AuthManager(load_settings()).get_profile_statuses()
    options = [
        (name, str(info["label"]) + ("" if bool(info["configured"]) else " (missing)"))
        for name, info in statuses.items()
    ]
    return _select_from_menu("Provider profile:", options, default_value=default_value)


def _prompt_channels(
    existing: SelfLogConfig,
    *,
    target: str,
) -> tuple[list[str], dict[str, dict]]:
    enabled: list[str] = []
    configs: dict[str, dict] = {}
    print(f"Configure channels for {target}:")
    for channel in _INTERACTIVE_CHANNELS:
        current = channel in existing.enabled_channels
        prior = dict(existing.channel_configs.get(channel, {}))
        if current:
            enabled.append(channel)
            if not _confirm_prompt(f"Reconfigure {channel}?", default=False):
                configs[channel] = prior
                continue
        elif not _confirm_prompt(f"Enable {channel}?", default=False):
            continue
        else:
            enabled.append(channel)
        config: dict[str, object] = {
            "allow_from": _csv_prompt(
                f"{channel} allow_from (comma separated user/chat IDs; blank denies all; '*' allows everyone)",
                default=",".join(prior.get("allow_from", [])),
            )
        }
        if channel == "telegram":
            config["token"] = _text_prompt("Telegram bot token", default=str(prior.get("token", "")))
            config["reply_to_message"] = _confirm_prompt(
                "Reply to the original Telegram message?",
                default=bool(prior.get("reply_to_message", True)),
            )
        elif channel == "slack":
            config["bot_token"] = _text_prompt("Slack bot token", default=str(prior.get("bot_token", "")))
            config["app_token"] = _text_prompt("Slack app token", default=str(prior.get("app_token", "")))
            config["mode"] = "socket"
            config["reply_in_thread"] = _confirm_prompt(
                "Reply in thread?",
                default=bool(prior.get("reply_in_thread", True)),
            )
        elif channel == "discord":
            config["token"] = _text_prompt("Discord bot token", default=str(prior.get("token", "")))
            config["gateway_url"] = _text_prompt(
                "Discord gateway URL",
                default=str(prior.get("gateway_url", "wss://gateway.discord.gg/?v=10&encoding=json")),
            )
            config["intents"] = int(_text_prompt("Discord intents bitmask", default=str(prior.get("intents", 513))))
        elif channel == "feishu":
            config["app_id"] = _text_prompt("Feishu app id", default=str(prior.get("app_id", "")))
            config["app_secret"] = _text_prompt("Feishu app secret", default=str(prior.get("app_secret", "")))
            config["encrypt_key"] = _text_prompt("Feishu encrypt key", default=str(prior.get("encrypt_key", "")))
            config["verification_token"] = _text_prompt(
                "Feishu verification token",
                default=str(prior.get("verification_token", "")),
            )
            config["react_emoji"] = _text_prompt("Feishu reaction emoji", default=str(prior.get("react_emoji", "OK")))
            config["group_policy"] = "open"
            config["bot_open_id"] = _text_prompt(
                "Bot open_id (leave empty to auto-detect)",
                default=str(prior.get("bot_open_id", "")),
            )
            config["bot_names"] = _text_prompt(
                "Bot name keywords (comma separated)",
                default=str(prior.get("bot_names", "self-log,openharness")),
            )
        configs[channel] = config
    return enabled, configs


def _csv_prompt(message: str, *, default: str = "") -> list[str]:
    raw = _text_prompt(message, default=default)
    return [item.strip() for item in raw.split(",") if item.strip()]
