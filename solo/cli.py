"""CLI entry point for the standalone solo app."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import sys

import typer

from openharness.auth.manager import AuthManager
from openharness.config import load_settings

from solo.agent import OpenHarnessSoloAgent
from solo.config import load_config, save_config
from solo.gateway.service import (
    SoloGatewayService,
    gateway_status,
    start_gateway_process,
    stop_gateway_process,
)
from uuid import uuid4

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from solo.core.models import Project, Milestone, ProjectLink, SoloConfig, SoloEntry, SoloRecord
from solo.core.utils import _now
from solo.processor import SoloProcessor
from solo.core.store import SoloStore
from solo.core.workspace import get_config_path, get_logs_dir, get_workspace_root, initialize_workspace, workspace_health

app = typer.Typer(
    name="solo",
    help="独立的个人记录应用，适合记录日常碎片、补录旧内容并生成回顾报告。",
    add_completion=False,
)
gateway_app = typer.Typer(name="gateway", help="管理 solo 后台网关")
heartbeat_app = typer.Typer(name="heartbeat", help="查看或触发 solo heartbeat")
onboard_app = typer.Typer(name="onboard", help="管理 onboard WebUI 仪表盘")
feed_digest_app = typer.Typer(name="feed-digest", help="管理资讯简报任务")
project_app = typer.Typer(name="project", help="管理 solo 项目")
milestone_app = typer.Typer(name="milestone", help="管理项目里程碑")
app.add_typer(gateway_app)
app.add_typer(heartbeat_app)
app.add_typer(onboard_app)
app.add_typer(feed_digest_app)
app.add_typer(project_app)
project_app.add_typer(milestone_app)

_INTERACTIVE_CHANNELS = ("telegram", "slack", "discord", "feishu")
_WORKSPACE_HELP = "solo 工作目录路径，默认 ~/.solo"


@app.command("init", help="初始化 solo 工作目录和默认数据文件")
def init_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    root = initialize_workspace(workspace)
    SoloStore(root).initialize()
    print(f"Initialized solo at {root}")
    _maybe_install_service(root)


def _maybe_install_service(workspace: str | Path) -> None:
    """Prompt to install the system-level background service."""
    from openharness.utils.platform_service import is_service_installed

    label = "ai.solo.gateway"
    if is_service_installed(label):
        return

    print("\n🔧 系统集成 (Optional)")
    print("是否希望在开机登录时自动启动 solo 后台网关？")
    print("这可以确保你的日志记录服务始终在线，随时响应远程频道（如飞书/Slack）的记录请求。")
    if _confirm_prompt("安装系统级自启动服务？", default=False):
        from openharness.utils.platform_service import install_service
        root = Path(workspace).resolve()
        args = ["-m", "solo", "gateway", "run", "--workspace", str(root)]
        if install_service(label, args, root, description="solo Personal Logging Gateway"):
            print(f"✅ solo gateway 服务已安装并启动 (标签: {label})")
        else:
            print("❌ 安装失败，请尝试手动运行 `solo gateway install-service`。")


@app.command("config", help="交互式配置模型 profile 与消息通道")
def config_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    root = initialize_workspace(workspace)
    existing = load_config(root)
    provider_profile = _prompt_provider_profile(existing.provider_profile)
    enabled_channels, channel_configs = _prompt_channels(existing, target="solo")
    send_progress = _confirm_prompt("Send progress updates to channels?", default=existing.send_progress)
    send_tool_hints = _confirm_prompt("Send tool hints to channels?", default=existing.send_tool_hints)
    heartbeat_enabled = _confirm_prompt("Enable periodic solo heartbeat?", default=existing.heartbeat.enabled)
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
    print(f"Saved solo config to {get_config_path(root)}")


@app.command("record", help="写入一条原始日常记录")
def record_cmd(
    content: str = typer.Argument(...),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    entry = SoloStore(workspace).record(content)
    print(f"Recorded solo entry {entry.id}")


@app.command("list", help="查看原始输入列表")
def list_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum entries to show"),
) -> None:
    for entry in SoloStore(workspace).list_entries(limit=limit):
        attachment_hint = f" [attachments={len(entry.attachments)}]" if entry.attachments else ""
        print(f"{entry.created_at} [{entry.channel}]{attachment_hint} {entry.content}")


@app.command("process", help="整理待处理记录并生成结构化内容")
def process_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    store = SoloStore(workspace)
    agent = OpenHarnessSoloAgent(
        profile=profile or load_config(workspace).provider_profile,
        record_model_call=store.record_llm_call,
    )
    result = asyncio.run(SoloProcessor(store, agent).process_pending(backfill_missing_yesterday=True))
    print(f"Processed {result.auto_processed} record(s), pending {result.pending_confirmations}.")


@app.command("view", help="查看结构化记录")
def view_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum records to show"),
) -> None:
    for record in SoloStore(workspace).list_records(limit=limit):
        attachment_hint = f" [attachments={len(record.attachments)}]" if record.attachments else ""
        print(f"{record.date} {record.emotion} [{record.source}] [{record.tags}]{attachment_hint} {record.summary}")


@app.command("show", help="查看单条记录详情")
def show_cmd(
    record_id: str = typer.Argument(..., help="Record ID to inspect"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = SoloStore(workspace)
    record = store.get_record(record_id)
    if record is None:
        print(f"Record not found: {record_id}")
        raise typer.Exit(1)
    entry = store.get_entry(record.entry_id)
    print(_format_record_trace(store, record, entry))


@app.command("search", help="按关键词、标签、情绪或日期搜索记录")
def search_cmd(
    query: str = typer.Argument(None, help="Text search query"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated tags"),
    emotions: str | None = typer.Option(None, "--emotions", help="Comma-separated emotions"),
    start: str | None = typer.Option(None, "--start", help="Start date (YYYY-MM-DD)"),
    end: str | None = typer.Option(None, "--end", help="End date (YYYY-MM-DD)"),
    limit: int = typer.Option(10, "--limit", min=1),
) -> None:
    store = SoloStore(workspace)
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


@app.command("report", help="生成新的周报、月报或年报")
def report_cmd(
    report_type: str = typer.Argument(..., help="weekly, monthly, or yearly"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    """Generate a new report."""
    store = SoloStore(workspace)
    agent = OpenHarnessSoloAgent(
        profile=profile or load_config(workspace).provider_profile,
        record_model_call=store.record_llm_call,
    )
    report = asyncio.run(SoloProcessor(store, agent).generate_report(report_type))
    print(report.content)


@app.command("report-list", help="查看已生成的报告列表")
def report_list_cmd(
    report_type: str | None = typer.Option(None, "--type", help="Filter by type: weekly, monthly, yearly"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """List all reports."""
    store = SoloStore(workspace)
    reports = store.list_reports()
    if report_type:
        reports = [r for r in reports if r.report_type == report_type]
    reports.sort(key=lambda r: r.created_at, reverse=True)
    if not reports:
        print("No reports found.")
        return
    for r in reports:
        preview = r.content[:60].replace("\n", " ") if r.content else "(empty)"
        print(f"[{r.id}] {r.report_type:8s} {r.created_at}  {preview}")


@app.command("report-show", help="查看报告全文")
def report_show_cmd(
    report_id: str = typer.Argument(..., help="Report ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Show full content of a report."""
    store = SoloStore(workspace)
    report = store.get_report(report_id)
    if not report:
        print(f"Report {report_id} not found.")
        raise typer.Exit(1)
    print(f"# {report.report_type} report ({report.created_at})\n")
    print(report.content or "(empty)")


@app.command("report-delete", help="删除一份报告")
def report_delete_cmd(
    report_id: str = typer.Argument(..., help="Report ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete a report permanently."""
    store = SoloStore(workspace)
    report = store.get_report(report_id)
    if not report:
        print(f"Report {report_id} not found.")
        raise typer.Exit(1)
    if not force:
        confirm = typer.confirm(f"Delete {report.report_type} report ({report.created_at})?")
        if not confirm:
            raise typer.Abort()
    store.delete_report(report_id)
    print(f"Deleted report {report_id}.")


@app.command("report-edit", help="在编辑器中修改报告内容")
def report_edit_cmd(
    report_id: str = typer.Argument(..., help="Report ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Edit report content in $EDITOR."""
    import os
    import tempfile
    import subprocess as sp

    store = SoloStore(workspace)
    report = store.get_report(report_id)
    if not report:
        print(f"Report {report_id} not found.")
        raise typer.Exit(1)

    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(report.content or "")
        tmp_path = f.name

    try:
        sp.run([editor, tmp_path], check=True)
        new_content = Path(tmp_path).read_text(encoding="utf-8")
        if new_content == report.content:
            print("No changes.")
            return
        store.update_report(report_id, new_content)
        print(f"Updated report {report_id}.")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.command("report-search", help="按关键词搜索报告")
def report_search_cmd(
    keyword: str = typer.Argument(..., help="Search keyword"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Search reports by keyword in content."""
    store = SoloStore(workspace)
    reports = store.list_reports()
    matches = [r for r in reports if keyword.lower() in (r.content or "").lower()]
    matches.sort(key=lambda r: r.created_at, reverse=True)
    if not matches:
        print(f"No reports matching '{keyword}'.")
        return
    for r in matches:
        print(f"[{r.id}] {r.report_type:8s} {r.created_at}")
        lower_content = (r.content or "").lower()
        idx = lower_content.find(keyword.lower())
        start = max(0, idx - 40)
        end = min(len(r.content or ""), idx + len(keyword) + 40)
        snippet = (r.content or "")[start:end].replace("\n", " ")
        print(f"    ...{snippet}...")
        print()


@feed_digest_app.command("run")
def feed_digest_run_cmd(
    preset: str = typer.Option("ai_news", "--preset", help="Preset name"),
    date: str | None = typer.Option(None, "--date", help="Date YYYY-MM-DD (default: today)"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    push: bool = typer.Option(False, "--push/--no-push", help="Push result to IM"),
) -> None:
    """Manually run a feed digest cycle (for debugging/backfill)."""

    async def _run() -> None:
        from solo.feed_digest import run_feed_digest
        from solo.gateway.feed_digest_runner import _push_to_im

        report = await run_feed_digest(workspace=workspace, preset_name=preset, date=date)
        meta = report.metadata or {}
        print(
            f"Feed digest archived: id={report.id} preset={meta.get('preset')} "
            f"date={meta.get('date')} is_empty={meta.get('is_empty')}"
        )
        if push and report.content:
            print("Pushing to IM...")
            await _push_to_im(workspace, report.content)
            print("Pushed.")

    asyncio.run(_run())


@app.command("status", help="查看 solo 运行状态与数据概览")
def status_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    status = SoloStore(workspace).status()
    gateway = gateway_status(workspace=workspace)
    print(
        f"solo: entries={status['entries']} | records={status['records']} | "
        f"attachments={status['attachments']} | pending={status['pending_confirmations']} | "
        f"gateway={'running' if gateway.running else 'stopped'} | "
        f"path={status['path']}"
    )


@gateway_app.command("start", help="启动 solo 后台网关")
def start_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    pid = start_gateway_process(cwd, workspace)
    print(f"solo gateway started (pid={pid})")


@gateway_app.command("stop", help="停止 solo 后台网关")
def stop_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    if stop_gateway_process(cwd, workspace):
        print("solo gateway stopped.")
        return
    print("solo gateway is not running.")


@app.command("doctor", help="检查工作目录与配置健康状况")
def doctor_cmd(workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP)) -> None:
    health = workspace_health(get_workspace_root(workspace))
    for name, ok in health.items():
        print(f"{name}: {'ok' if ok else 'missing'}")


@onboard_app.command("run")
def onboard_run_cmd(
    host: str = typer.Option("0.0.0.0", "--host", help="Host interface to bind"),
    port: int = typer.Option(8090, "--port", min=1, max=65535, help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable uvicorn reload"),
) -> None:
    """Start onboard WebUI in the foreground."""
    from onboard.server import OnboardServerError, run_server

    try:
        run_server(host=host, port=port, reload=reload)
    except OnboardServerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@onboard_app.command("start")
def onboard_start_cmd(
    host: str = typer.Option("0.0.0.0", "--host", help="Host interface to bind"),
    port: int = typer.Option(8090, "--port", min=1, max=65535, help="Port to bind"),
) -> None:
    """Start onboard WebUI in the background."""
    from onboard.server import start_background

    pid = start_background(host=host, port=port)
    print(f"onboard started (pid={pid}, url=http://{host}:{port})")


@onboard_app.command("stop")
def onboard_stop_cmd() -> None:
    """Stop background onboard WebUI."""
    from onboard.server import stop_background

    if stop_background():
        print("onboard stopped.")
        return
    print("onboard is not running.")


@onboard_app.command("status")
def onboard_status_cmd() -> None:
    """Show onboard WebUI process status."""
    from onboard.server import server_status

    status = server_status()
    print(
        f"onboard: {status['status']} | pid={status['pid']} | "
        f"url=http://{status['host']}:{status['port']} | log={status['log_file']}"
    )


@heartbeat_app.command("status")
def heartbeat_status_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    from openharness.channels.bus.queue import MessageBus
    from solo.gateway.heartbeat import SoloHeartbeatService

    config = load_config(workspace)
    service = SoloHeartbeatService(
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
        f"solo heartbeat: enabled={status['enabled']} "
        f"interval_s={status['interval_s']} has_signals={status['has_signals']} "
        f"notify_target={status['notify_target']}"
    )


@heartbeat_app.command("trigger")
def heartbeat_trigger_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    from openharness.channels.bus.queue import MessageBus
    from solo.gateway.heartbeat import SoloHeartbeatService

    config = load_config(workspace)
    service = SoloHeartbeatService(
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
        print("No solo heartbeat agenda.")
        return
    print(result.response or "solo heartbeat completed.")


@gateway_app.command("run")
def gateway_run_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    _configure_gateway_logging(workspace)
    raise typer.Exit(asyncio.run(SoloGatewayService(cwd, workspace).run_foreground()))


@gateway_app.command("install-service")
def gateway_install_service_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Install the solo gateway as a system-level background service (LaunchAgent/systemd)."""
    from openharness.utils.platform_service import install_service
    from solo.core.workspace import get_workspace_root

    root = get_workspace_root(workspace)
    args = ["-m", "solo", "gateway", "run", "--workspace", str(root)]
    if install_service("ai.solo.gateway", args, root, description="solo Personal Logging Gateway"):
        print("✅ solo gateway service installed and started (label: ai.solo.gateway)")
    else:
        print("❌ Failed to install solo gateway service.", file=sys.stderr)
        raise typer.Exit(1)


@gateway_app.command("uninstall-service")
def gateway_uninstall_service_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Uninstall the solo gateway background service."""
    from openharness.utils.platform_service import uninstall_service
    if uninstall_service("ai.solo.gateway"):
        print("✅ solo gateway service uninstalled.")
    else:
        print("❌ Failed to uninstall solo gateway service (or not found).", file=sys.stderr)


def _configure_gateway_logging(workspace: str | Path | None = None) -> None:
    """Configure foreground gateway logging."""
    config = load_config(workspace)
    level_name = str(config.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_path = get_logs_dir(workspace) / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = _SoloFormatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s")
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
    store: SoloStore,
    record: SoloRecord,
    entry: SoloEntry | None,
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


class _SoloFormatter(logging.Formatter):
    """Formatter that shortens logger names for cleaner terminal output."""

    # Maps known verbose prefix → short label
    _PREFIX_MAP = {
        "solo.gateway.": "gateway.",
        "solo.": "solo.",
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


def _resolve_project(store: SoloStore, ref: str) -> Project | None:
    """Resolve project by ID first, then by exact title match."""
    p = store.get_project(ref)
    if p:
        return p
    projects = store.list_projects()
    for proj in projects:
        if proj.title == ref:
            return proj
    # Try case-insensitive
    ref_lower = ref.lower()
    for proj in projects:
        if proj.title.lower() == ref_lower:
            return proj
    return None


@project_app.command("list", help="列出所有项目")
def project_list_cmd(
    status: str = typer.Option("active", help="项目状态筛选: active/completed/archived/all"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """List all projects with detail."""
    store = SoloStore(workspace)
    status_filter = None if status == "all" else status
    projects = store.list_projects_with_detail(status=status_filter)

    if not projects:
        print(f"No {status} projects found.")
        return

    console = Console()
    table = Table(title=f"{status.capitalize()} Projects")
    table.add_column("项目", style="cyan", no_wrap=True)
    table.add_column("进度", justify="right")
    table.add_column("里程碑", justify="center")
    table.add_column("目标日期", justify="center")
    table.add_column("风险", justify="center")

    for p in projects:
        title = p["title"]
        completion_pct = p.get("completion_pct")
        if completion_pct is not None:
            bar_len = 10
            filled = int(bar_len * completion_pct / 100)
            bar = "#" * filled + "-" * (bar_len - filled)
            progress = f"[{bar}] {completion_pct}%"
        else:
            progress = "-"

        milestone_count = p.get("milestone_count", 0)
        completed_milestone_count = p.get("completed_milestone_count", 0)
        milestones = f"{completed_milestone_count}/{milestone_count}" if milestone_count > 0 else "-"

        target_date = p.get("target_date", "") or "-"

        risk_status = p.get("risk_status", "normal")
        risk_label = {"normal": "ok", "attention": "warn", "at_risk": "risk"}.get(risk_status, "ok")
        risk_color = {"normal": "green", "attention": "yellow", "at_risk": "red"}.get(risk_status, "green")
        risk = f"[{risk_color}]{risk_label}[/{risk_color}]"

        table.add_row(title, progress, milestones, target_date, risk)

    console.print(table)


@project_app.command("create", help="创建新项目")
def project_create_cmd(
    title: str = typer.Argument(..., help="项目标题"),
    description: str = typer.Option("", help="项目描述"),
    target_date: str = typer.Option("", "--target-date", help="目标日期 YYYY-MM-DD"),
    priority: str = typer.Option("medium", help="优先级: high/medium/low"),
    tags: str = typer.Option("", help="标签，逗号分隔"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Create a new project."""
    store = SoloStore(workspace)
    now = _now()
    project = Project(
        id=str(uuid4()),
        title=title,
        description=description,
        status="active",
        priority=priority,
        start_date=now[:10],
        target_date=target_date,
        tags=tags,
        created_at=now,
        updated_at=now,
    )
    store.create_project(project)
    print(f"Created project '{title}' (id={project.id})")


@project_app.command("show", help="查看项目详情")
def project_show_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Show project details."""
    store = SoloStore(workspace)
    proj = _resolve_project(store, project)
    if not proj:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    detail = store.get_project_detail(proj.id)
    if not detail:
        print(f"Failed to load project detail: {proj.id}")
        raise typer.Exit(1)

    console = Console()

    completion_pct = detail.get("completion_pct")
    progress_line = f"{completion_pct}%" if completion_pct is not None else "-"

    info_lines = [
        f"[bold cyan]{proj.title}[/bold cyan]",
        f"ID: {proj.id}",
        f"Status: {proj.status}  Priority: {proj.priority}",
        f"Description: {proj.description or '-'}",
        f"Target: {proj.target_date or '-'}  Tags: {proj.tags or '-'}",
        f"Progress: {progress_line}",
        f"Created: {proj.created_at}",
    ]
    console.print(Panel("\n".join(info_lines), title="Project"))

    milestones = store.list_milestones(proj.id)
    if milestones:
        ms_table = Table(title="Milestones")
        ms_table.add_column("Title", style="cyan")
        ms_table.add_column("Status", justify="center")
        ms_table.add_column("Target", justify="center")
        for m in milestones:
            icon = "done" if m.status == "completed" else "pending"
            ms_table.add_row(m.title, icon, m.target_date or "-")
        console.print(ms_table)

    links = store.list_project_links(project_id=proj.id, status="active")
    if links:
        entity_counts: dict[str, int] = {}
        for lnk in links:
            entity_counts[lnk.entity_type] = entity_counts.get(lnk.entity_type, 0) + 1
        link_lines = [f"{k}: {v}" for k, v in entity_counts.items()]
        console.print(Panel("\n".join(link_lines), title="Linked Entities"))


@project_app.command("update", help="更新项目信息")
def project_update_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    title: str = typer.Option(None, help="新标题"),
    description: str = typer.Option(None, help="新描述"),
    target_date: str = typer.Option(None, "--target-date", help="新目标日期"),
    priority: str = typer.Option(None, help="新优先级"),
    tags: str = typer.Option(None, help="新标签"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Update project information."""
    store = SoloStore(workspace)
    proj = _resolve_project(store, project)
    if not proj:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    fields: dict[str, str] = {}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if target_date is not None:
        fields["target_date"] = target_date
    if priority is not None:
        fields["priority"] = priority
    if tags is not None:
        fields["tags"] = tags

    if not fields:
        print("No fields to update.")
        return

    if store.update_project(proj.id, **fields):
        print(f"Updated project '{proj.title}'")
    else:
        print(f"Failed to update project '{proj.title}'")
        raise typer.Exit(1)


@project_app.command("complete", help="标记项目完成")
def project_complete_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Mark a project as completed."""
    store = SoloStore(workspace)
    proj = _resolve_project(store, project)
    if not proj:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    if store.complete_project(proj.id):
        print(f"Completed project '{proj.title}'")
    else:
        print(f"Failed to complete project '{proj.title}'")
        raise typer.Exit(1)


@project_app.command("archive", help="归档项目")
def project_archive_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    reason: str = typer.Option("", "--reason", help="归档原因"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Archive a project."""
    store = SoloStore(workspace)
    proj = _resolve_project(store, project)
    if not proj:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    if store.archive_project(proj.id, reason):
        print(f"Archived project '{proj.title}'")
    else:
        print(f"Failed to archive project '{proj.title}'")
        raise typer.Exit(1)


@project_app.command("reactivate", help="重新激活项目")
def project_reactivate_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Reactivate a completed or archived project."""
    store = SoloStore(workspace)
    proj = _resolve_project(store, project)
    if not proj:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    if store.reactivate_project(proj.id):
        print(f"Reactivated project '{proj.title}'")
    else:
        print(f"Failed to reactivate project '{proj.title}'")
        raise typer.Exit(1)


@project_app.command("delete", help="删除项目（不删除关联的原始记录）")
def project_delete_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Delete a project (does NOT delete linked records/todos)."""
    store = SoloStore(workspace)
    proj = _resolve_project(store, project)
    if not proj:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    print(f"Warning: Deleting project '{proj.title}' will NOT delete linked records, todos, or other entities.")
    if not typer.confirm("Are you sure you want to delete this project?"):
        raise typer.Abort()

    if store.delete_project(proj.id):
        print(f"Deleted project '{proj.title}'")
    else:
        print(f"Failed to delete project '{proj.title}'")
        raise typer.Exit(1)


@milestone_app.command("add", help="添加里程碑")
def milestone_add_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    title: str = typer.Argument(..., help="里程碑标题"),
    target_date: str = typer.Option("", "--target-date", help="目标日期 YYYY-MM-DD"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Add a milestone to a project."""
    store = SoloStore(workspace)
    proj = _resolve_project(store, project)
    if not proj:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    now = _now()
    milestone = Milestone(
        id=str(uuid4()),
        project_id=proj.id,
        title=title,
        status="pending",
        target_date=target_date,
        created_at=now,
        updated_at=now,
    )
    store.create_milestone(milestone)
    print(f"Added milestone '{title}' to project '{proj.title}'")


@milestone_app.command("complete", help="标记里程碑完成")
def milestone_complete_cmd(
    milestone_id: str = typer.Argument(..., help="里程碑 ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Mark a milestone as completed."""
    store = SoloStore(workspace)
    if store.complete_milestone(milestone_id):
        print(f"Completed milestone {milestone_id}")
    else:
        print(f"Milestone not found: {milestone_id}")
        raise typer.Exit(1)


@milestone_app.command("delete", help="删除里程碑")
def milestone_delete_cmd(
    milestone_id: str = typer.Argument(..., help="里程碑 ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Delete a milestone."""
    store = SoloStore(workspace)
    if store.delete_milestone(milestone_id):
        print(f"Deleted milestone {milestone_id}")
    else:
        print(f"Milestone not found: {milestone_id}")
        raise typer.Exit(1)


@project_app.command("link", help="关联实体到项目")
def project_link_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    entity_type: str = typer.Argument(..., help="实体类型: record/todo/decision/highlight/experiment"),
    entity_id: str = typer.Argument(..., help="实体 ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Link an entity to a project."""
    store = SoloStore(workspace)
    proj = _resolve_project(store, project)
    if not proj:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    now = _now()
    link = ProjectLink(
        id=str(uuid4()),
        project_id=proj.id,
        entity_type=entity_type,
        entity_id=entity_id,
        source="user",
        status="active",
        created_at=now,
        updated_at=now,
    )
    store.create_project_link(link)
    print(f"Linked {entity_type} '{entity_id}' to project '{proj.title}'")


@project_app.command("unlink", help="解除实体与项目的关联")
def project_unlink_cmd(
    link_id: str = typer.Argument(..., help="关联 ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """Unlink an entity from a project."""
    store = SoloStore(workspace)
    if store.delete_project_link(link_id):
        print(f"Unlinked {link_id}")
    else:
        print(f"Link not found: {link_id}")
        raise typer.Exit(1)


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
    existing: SoloConfig,
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
                default=str(prior.get("bot_names", "solo,openharness")),
            )
        configs[channel] = config
    return enabled, configs


def _csv_prompt(message: str, *, default: str = "") -> list[str]:
    raw = _text_prompt(message, default=default)
    return [item.strip() for item in raw.split(",") if item.strip()]

@project_app.command("review", help="生成项目回顾报告")
def project_review_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    from solo.core.models import SoloReport
    store = SoloStore(workspace)
    p = _resolve_project(store, project)
    if p is None:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    detail = store.get_project_detail(p.id)
    milestones = store.list_milestones(p.id)
    links = store.list_project_links(project_id=p.id)

    # Build review content
    lines = []
    lines.append(f"# Project Review: {p.title}")
    lines.append("")
    if p.description:
        lines.append(p.description)
        lines.append("")
    lines.append(f"- Status: {p.status}")
    lines.append(f"- Priority: {p.priority}")
    if p.start_date:
        lines.append(f"- Start: {p.start_date}")
    if p.target_date:
        lines.append(f"- Target: {p.target_date}")
    if p.completed_at:
        lines.append(f"- Completed: {p.completed_at}")
    lines.append("")

    # Milestones
    lines.append("## Milestones")
    if milestones:
        for m in milestones:
            status = "✓" if m.status == "completed" else "○"
            date = f" ({m.target_date})" if m.target_date else ""
            lines.append(f"- {status} {m.title}{date}")
    else:
        lines.append("- No milestones defined")
    lines.append("")

    # Linked entities summary
    entity_counts = {}
    for lnk in links:
        if lnk.status == "active":
            entity_counts[lnk.entity_type] = entity_counts.get(lnk.entity_type, 0) + 1
    lines.append("## Linked Entities")
    if entity_counts:
        for et, count in sorted(entity_counts.items()):
            lines.append(f"- {et}: {count}")
    else:
        lines.append("- No entities linked")
    lines.append("")

    # Statistics
    lines.append("## Statistics")
    lines.append(f"- Completion: {detail.get('completion_pct', 'N/A')}% ({detail.get('completion_source', 'none')})")
    lines.append(f"- Milestones: {detail.get('completed_milestone_count', 0)}/{detail.get('milestone_count', 0)}")
    lines.append(f"- Linked records: {detail.get('linked_record_count', 0)}")
    lines.append(f"- Linked todos: {detail.get('linked_todo_count', 0)}")
    lines.append(f"- Activity (7d): {detail.get('activity_7d', 0)}")
    lines.append(f"- Activity (30d): {detail.get('activity_30d', 0)}")
    lines.append(f"- Risk: {detail.get('risk_status', 'normal')}")
    lines.append("")

    review_content = "\n".join(lines)

    # Save as report
    from uuid import uuid4
    report = SoloReport(
        id=str(uuid4()),
        report_type="project_review",
        content=review_content,
        created_at=_now(),
        period_start=p.start_date or p.created_at,
        period_end=p.completed_at or _now(),
        metadata={"project_id": p.id, "project_title": p.title},
    )
    store.add_report(report)
    print(review_content)
    print(f"\n✅ Review report saved (id: {report.id})")

