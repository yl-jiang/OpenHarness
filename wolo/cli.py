"""CLI entry point for the standalone wolo app."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import sys

import typer

from openharness.auth.manager import AuthManager
from openharness.config import load_settings

from wolo.agent import OpenHarnessWoloAgent
from wolo.config import load_config, save_config
from wolo.gateway.service import (
    WoloGatewayService,
    gateway_status,
    start_gateway_process,
    stop_gateway_process,
)
from wolo.models import WoloConfig, WoloEntry, WoloRecord
from wolo.processor import WoloProcessor
from wolo.store import WoloStore
from wolo.workspace import get_config_path, get_logs_dir, get_workspace_root, initialize_workspace, workspace_health

app = typer.Typer(
    name="wolo",
    help="wolo: a standalone work logging app built on OpenHarness.",
    add_completion=False,
)
gateway_app = typer.Typer(name="gateway", help="Run the wolo gateway")
heartbeat_app = typer.Typer(name="heartbeat", help="Inspect or trigger wolo heartbeat")
app.add_typer(gateway_app)
app.add_typer(heartbeat_app)

_INTERACTIVE_CHANNELS = ("telegram", "slack", "discord", "feishu")
_WORKSPACE_HELP = "Path to the wolo workspace (defaults to ~/.wolo)"


@app.command("init")
def init_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    root = initialize_workspace(workspace)
    WoloStore(root).initialize()
    print(f"Initialized wolo at {root}")
    _maybe_install_service(root)


def _maybe_install_service(workspace: str | Path) -> None:
    """Prompt to install the system-level background service."""
    from openharness.utils.platform_service import is_service_installed

    label = "ai.wolo.gateway"
    if is_service_installed(label):
        return

    print("\n🔧 系统集成 (Optional)")
    print("是否希望在开机登录时自动启动 wolo 后台网关？")
    print("这可以确保你的工作记录服务始终在线，随时响应远程频道（如飞书/Slack）的记录请求。")
    if _confirm_prompt("安装系统级自启动服务？", default=False):
        from openharness.utils.platform_service import install_service
        root = Path(workspace).resolve()
        args = ["-m", "wolo", "gateway", "run", "--workspace", str(root)]
        if install_service(label, args, root, description="wolo Work Logging Gateway"):
            print(f"✅ wolo gateway 服务已安装并启动 (标签: {label})")
        else:
            print("❌ 安装失败，请尝试手动运行 `wolo gateway install-service`。")


@app.command("config")
def config_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    root = initialize_workspace(workspace)
    existing = load_config(root)
    provider_profile = _prompt_provider_profile(existing.provider_profile)
    enabled_channels, channel_configs = _prompt_channels(existing, target="wolo")
    send_progress = _confirm_prompt("Send progress updates to channels?", default=existing.send_progress)
    send_tool_hints = _confirm_prompt("Send tool hints to channels?", default=existing.send_tool_hints)
    heartbeat_enabled = _confirm_prompt("Enable periodic wolo heartbeat?", default=existing.heartbeat.enabled)
    heartbeat_interval = existing.heartbeat.interval_s
    if heartbeat_enabled:
        heartbeat_interval = int(
            _text_prompt("Heartbeat interval seconds", default=str(existing.heartbeat.interval_s))
        )
    config = existing.model_copy(
        update={
            "provider_profile": provider_profile,
            "enabled_channels": enabled_channels,
            "channel_configs": channel_configs,
            "send_progress": send_progress,
            "send_tool_hints": send_tool_hints,
            "heartbeat": existing.heartbeat.model_copy(
                update={"enabled": heartbeat_enabled, "interval_s": heartbeat_interval}
            ),
        }
    )
    save_config(config, root)
    print(f"Saved wolo config to {get_config_path(root)}")


@app.command("record")
def record_cmd(
    content: str = typer.Argument(...),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    entry = WoloStore(workspace).record(content)
    print(f"Recorded wolo entry {entry.id}")


@app.command("list")
def list_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum entries to show"),
) -> None:
    for entry in WoloStore(workspace).list_entries(limit=limit):
        attachment_hint = f" [attachments={len(entry.attachments)}]" if entry.attachments else ""
        print(f"{entry.created_at} [{entry.channel}]{attachment_hint} {entry.content}")


@app.command("process")
def process_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    store = WoloStore(workspace)
    agent = OpenHarnessWoloAgent(profile=profile or load_config(workspace).provider_profile)
    result = asyncio.run(WoloProcessor(store, agent).process_pending(backfill_missing_yesterday=True))
    print(f"Processed {result.auto_processed} record(s), pending {result.pending_confirmations}.")


@app.command("view")
def view_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum records to show"),
) -> None:
    for record in WoloStore(workspace).list_records(limit=limit):
        attachment_hint = f" [attachments={len(record.attachments)}]" if record.attachments else ""
        print(f"{record.date} {record.emotion} [{record.source}] [{record.tags}]{attachment_hint} {record.summary}")


@app.command("show")
def show_cmd(
    record_id: str = typer.Argument(..., help="Record ID to inspect"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    record = store.get_record(record_id)
    if record is None:
        print(f"Record not found: {record_id}")
        raise typer.Exit(1)
    entry = store.get_entry(record.entry_id)
    print(_format_record_trace(store, record, entry))


@app.command("search")
def search_cmd(
    query: str = typer.Argument(None, help="Text search query"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated tags"),
    emotions: str | None = typer.Option(None, "--emotions", help="Comma-separated emotions"),
    start: str | None = typer.Option(None, "--start", help="Start date (YYYY-MM-DD)"),
    end: str | None = typer.Option(None, "--end", help="End date (YYYY-MM-DD)"),
    limit: int = typer.Option(10, "--limit", min=1),
) -> None:
    store = WoloStore(workspace)
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    emotion_list = [e.strip() for e in emotions.split(",")] if emotions else None
    records = store.search_records(
        query=query,
        tags=tag_list,
        emotions=emotion_list,
        start_date=start,
        end_date=end,
        limit=limit,
    )
    if not records:
        print("No matching records found.")
        return
    for record in records:
        print(f"{record.date} {record.emotion} [{record.tags}] {record.summary}")


@app.command("todos")
def todos_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    status: str = typer.Option("pending", "--status", help="Todo status: pending/done"),
    project: str | None = typer.Option(None, "--project", help="Project filter"),
    limit: int = typer.Option(20, "--limit", min=1),
) -> None:
    todos = WoloStore(workspace).list_todos(status=status, project=project, limit=limit)
    if not todos:
        print("No matching todos found.")
        return
    for todo in todos:
        print(f"{todo.id} [{todo.status}] [{todo.priority}] [{todo.project}] {todo.title}")


@app.command("done")
def done_cmd(
    todo_id: str = typer.Argument(..., help="Todo ID to mark done"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    if WoloStore(workspace).complete_todo(todo_id):
        print(f"已完成待办：{todo_id}")
        return
    print(f"Todo not found or already done: {todo_id}")


@app.command("decisions")
def decisions_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    project: str | None = typer.Option(None, "--project", help="Project filter"),
    query: str | None = typer.Option(None, "--query", help="Text query"),
    limit: int = typer.Option(20, "--limit", min=1),
) -> None:
    decisions = WoloStore(workspace).list_decisions(project=project, query=query, limit=limit)
    if not decisions:
        print("No matching decisions found.")
        return
    for decision in decisions:
        print(f"{decision.id} [{decision.project}] {decision.title}")


@app.command("highlights")
def highlights_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    kind: str | None = typer.Option(None, "--kind", help="important/prompt/tool/blocker/risk"),
    project: str | None = typer.Option(None, "--project", help="Project filter"),
    query: str | None = typer.Option(None, "--query", help="Text query"),
    limit: int = typer.Option(20, "--limit", min=1),
) -> None:
    highlights = WoloStore(workspace).list_highlights(
        kind=kind,
        project=project,
        query=query,
        limit=limit,
    )
    if not highlights:
        print("No matching highlights found.")
        return
    for item in highlights:
        print(f"{item.id} [{item.kind}] [{item.project}] {item.title}: {item.content}")


@app.command("blockers")
def blockers_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    project: str | None = typer.Option(None, "--project", help="Project filter"),
    query: str | None = typer.Option(None, "--query", help="Text query"),
    limit: int = typer.Option(20, "--limit", min=1),
) -> None:
    highlights_cmd(workspace=workspace, kind="blocker", project=project, query=query, limit=limit)


@app.command("query")
def query_cmd(
    query: str = typer.Argument(..., help="Work history query"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(10, "--limit", min=1),
) -> None:
    store = WoloStore(workspace)
    records = store.search_records(query=query, limit=limit)
    decisions = store.list_decisions(query=query, limit=limit)
    highlights = store.list_highlights(query=query, limit=limit)
    for record in records:
        print(f"record {record.date} [{record.tags}] {record.summary}")
    for decision in decisions:
        print(f"decision {decision.id} [{decision.project}] {decision.title}")
    for item in highlights:
        print(f"highlight {item.id} [{item.kind}] [{item.project}] {item.title}")
    if not records and not decisions and not highlights:
        print("No matching work history found.")


@app.command("report")
def report_cmd(
    report_type: str = typer.Argument(..., help="weekly, monthly, or yearly"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    store = WoloStore(workspace)
    agent = OpenHarnessWoloAgent(profile=profile or load_config(workspace).provider_profile)
    report = asyncio.run(WoloProcessor(store, agent).generate_report(report_type))
    print(report.content)


@app.command("status")
def status_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    status = WoloStore(workspace).status()
    gateway = gateway_status(workspace=workspace)
    print(
        f"wolo: entries={status['entries']} | records={status['records']} | "
        f"attachments={status['attachments']} | todos={status['todos']} | decisions={status['decisions']} | "
        f"highlights={status['highlights']} | pending={status['pending_confirmations']} | "
        f"gateway={'running' if gateway.running else 'stopped'} | "
        f"path={status['path']}"
    )


@app.command("start")
def start_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    pid = start_gateway_process(cwd, workspace)
    print(f"wolo gateway started (pid={pid})")


@app.command("stop")
def stop_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    if stop_gateway_process(cwd, workspace):
        print("wolo gateway stopped.")
        return
    print("wolo gateway is not running.")


@app.command("doctor")
def doctor_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    health = workspace_health(get_workspace_root(workspace))
    for name, ok in health.items():
        print(f"{name}: {'ok' if ok else 'missing'}")


@heartbeat_app.command("status")
def heartbeat_status_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    from openharness.channels.bus.queue import MessageBus
    from wolo.gateway.heartbeat import WoloHeartbeatService

    config = load_config(workspace)
    service = WoloHeartbeatService(
        bus=MessageBus(),
        workspace=workspace,
        provider_profile=config.provider_profile,
        enabled_channels=config.enabled_channels,
        interval_s=config.heartbeat.interval_s,
        enabled=config.heartbeat.enabled,
        keep_recent_messages=config.heartbeat.keep_recent_messages,
    )
    status = service.status()
    print(
        f"wolo heartbeat: enabled={status['enabled']} "
        f"interval_s={status['interval_s']} agenda={status['agenda']} "
        f"notify_target={status['notify_target']}"
    )


@heartbeat_app.command("trigger")
def heartbeat_trigger_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    from openharness.channels.bus.queue import MessageBus
    from wolo.gateway.heartbeat import WoloHeartbeatService

    config = load_config(workspace)
    service = WoloHeartbeatService(
        bus=MessageBus(),
        workspace=workspace,
        provider_profile=profile or config.provider_profile,
        enabled_channels=config.enabled_channels,
        interval_s=config.heartbeat.interval_s,
        enabled=True,
        keep_recent_messages=config.heartbeat.keep_recent_messages,
    )
    result = asyncio.run(service.trigger_once())
    if not result.executed:
        print("No wolo heartbeat agenda.")
        return
    print(result.response or "wolo heartbeat completed.")


@gateway_app.command("run")
def gateway_run_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    _configure_gateway_logging(workspace)
    raise typer.Exit(asyncio.run(WoloGatewayService(cwd, workspace).run_foreground()))


@gateway_app.command("install-service")
def gateway_install_service_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Install the wolo gateway as a system-level background service (LaunchAgent/systemd)."""
    from openharness.utils.platform_service import install_service
    from wolo.workspace import get_workspace_root

    root = get_workspace_root(workspace)
    args = ["-m", "wolo", "gateway", "run", "--workspace", str(root)]
    if install_service("ai.wolo.gateway", args, root, description="wolo Work Logging Gateway"):
        print("✅ wolo gateway service installed and started (label: ai.wolo.gateway)")
    else:
        print("❌ Failed to install wolo gateway service.", file=sys.stderr)
        raise typer.Exit(1)


@gateway_app.command("uninstall-service")
def gateway_uninstall_service_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Uninstall the wolo gateway background service."""
    from openharness.utils.platform_service import uninstall_service
    if uninstall_service("ai.wolo.gateway"):
        print("✅ wolo gateway service uninstalled.")
    else:
        print("❌ Failed to uninstall wolo gateway service (or not found).", file=sys.stderr)


def _configure_gateway_logging(workspace: str | Path | None = None) -> None:
    """Configure foreground gateway logging."""
    config = load_config(workspace)
    level_name = str(config.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_path = get_logs_dir(workspace) / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = _WoloFormatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
    handlers: list[logging.Handler] = []

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    if sys.stderr.isatty():
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        handlers.append(stream_handler)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    for handler in handlers:
        root_logger.addHandler(handler)

    # Silence noisy third-party loggers that pollute gateway output
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _format_record_trace(
    store: WoloStore,
    record: WoloRecord,
    entry: WoloEntry | None,
) -> str:
    lines = [
        f"record_id={record.id}",
        f"entry_id={record.entry_id}",
        f"date={record.date}",
        f"created_at={record.created_at}",
        f"source={record.source}",
        f"summary={record.summary}",
    ]
    if entry is not None:
        lines.extend(
            [
                f"channel={entry.channel}",
                f"sender_id={entry.sender_id}",
                f"chat_id={entry.chat_id}",
                f"message_id={entry.message_id or ''}",
            ]
        )
        source_message = dict((entry.metadata or {}).get("source_message") or {})
        if source_message:
            lines.append(
                "source_message="
                + json.dumps(source_message, ensure_ascii=False, sort_keys=True)
            )
    attachments = record.attachments or (entry.attachments if entry is not None else [])
    lines.append(f"attachments={len(attachments)}")
    for index, attachment in enumerate(attachments, start=1):
        lines.append(
            f"{index}. [{attachment.kind}] {attachment.original_name} | "
            f"mime={attachment.media_type} | size={attachment.size_bytes} | sha256={attachment.sha256}"
        )
        lines.append(f"   stored_path={store.resolve_attachment_path(attachment)}")
        lines.append(f"   source_path={attachment.source_path}")
    return "\n".join(lines)


class _WoloFormatter(logging.Formatter):
    """Formatter that shortens logger names for cleaner terminal output."""

    # Maps known verbose prefix → short label
    _PREFIX_MAP = {
        "wolo.gateway.": "gateway.",
        "wolo.": "wolo.",
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
    existing: WoloConfig,
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
                default=str(prior.get("bot_names", "wolo,openharness")),
            )
        configs[channel] = config
    return enabled, configs


def _csv_prompt(message: str, *, default: str = "") -> list[str]:
    raw = _text_prompt(message, default=default)
    return [item.strip() for item in raw.split(",") if item.strip()]
