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
from uuid import uuid4

from wolo.core.models import (
    Milestone,
    Project,
        ProjectLink,
    WoloConfig,
    WoloEntry,
    WoloRecord,
)
from wolo.core.utils import _now
from wolo.processor import WoloProcessor
from wolo.core.store import WoloStore
from wolo.core.workspace import get_config_path, get_logs_dir, get_workspace_root, initialize_workspace, workspace_health

app = typer.Typer(
    name="wolo",
    help="独立的工作记录应用，适合沉淀进展、决策、blocker 与报告素材。",
    add_completion=False,
)
gateway_app = typer.Typer(name="gateway", help="管理 wolo 后台网关")
heartbeat_app = typer.Typer(name="heartbeat", help="查看或触发 wolo heartbeat")
onboard_app = typer.Typer(name="onboard", help="管理 onboard WebUI 仪表盘")
feed_digest_app = typer.Typer(name="feed-digest", help="管理资讯简报任务")
project_app = typer.Typer(name="project", help="管理 wolo 工作项目")
milestone_app = typer.Typer(name="milestone", help="管理项目里程碑")
project_app.add_typer(milestone_app)
app.add_typer(project_app)
app.add_typer(gateway_app)
app.add_typer(heartbeat_app)
app.add_typer(onboard_app)
app.add_typer(feed_digest_app)

_INTERACTIVE_CHANNELS = ("telegram", "slack", "discord", "feishu")
_WORKSPACE_HELP = "wolo 工作目录路径，默认 ~/.wolo"


@app.command("init", help="初始化 wolo 工作目录和默认数据文件")
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


@app.command("config", help="交互式配置模型 profile 与消息通道")
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


@app.command("record", help="写入一条原始工作记录")
def record_cmd(
    content: str = typer.Argument(...),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    entry = WoloStore(workspace).record(content)
    print(f"Recorded wolo entry {entry.id}")


@app.command("list", help="查看原始输入列表")
def list_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum entries to show"),
) -> None:
    for entry in WoloStore(workspace).list_entries(limit=limit):
        attachment_hint = f" [attachments={len(entry.attachments)}]" if entry.attachments else ""
        print(f"{entry.created_at} [{entry.channel}]{attachment_hint} {entry.content}")


@app.command("process", help="整理待处理记录并生成结构化内容")
def process_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    store = WoloStore(workspace)
    agent = OpenHarnessWoloAgent(
        profile=profile or load_config(workspace).provider_profile,
        record_model_call=store.record_llm_call,
    )
    result = asyncio.run(WoloProcessor(store, agent).process_pending(backfill_missing_yesterday=True))
    print(f"Processed {result.auto_processed} record(s), pending {result.pending_confirmations}.")


@app.command("view", help="查看结构化工作记录")
def view_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum records to show"),
) -> None:
    for record in WoloStore(workspace).list_records(limit=limit):
        attachment_hint = f" [attachments={len(record.attachments)}]" if record.attachments else ""
        print(f"{record.date} {record.emotion} [{record.source}] [{record.tags}]{attachment_hint} {record.summary}")


@app.command("show", help="查看单条工作记录详情")
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


@app.command("search", help="按关键词、标签、情绪或日期搜索工作记录")
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


@app.command("todos", help="查看待办事项")
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


@app.command("done", help="将待办标记为已完成")
def done_cmd(
    todo_id: str = typer.Argument(..., help="Todo ID to mark done"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    if WoloStore(workspace).complete_todo(todo_id):
        print(f"已完成待办：{todo_id}")
        return
    print(f"Todo not found or already done: {todo_id}")


@app.command("decisions", help="查看关键决策")
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


@app.command("highlights", help="查看重要事项、prompt 与 tool 经验")
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


@app.command("blockers", help="查看 blocker 和风险")
def blockers_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    project: str | None = typer.Option(None, "--project", help="Project filter"),
    query: str | None = typer.Option(None, "--query", help="Text query"),
    limit: int = typer.Option(20, "--limit", min=1),
) -> None:
    highlights_cmd(workspace=workspace, kind="blocker", project=project, query=query, limit=limit)


@app.command("query", help="对工作沉淀发起综合查询")
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


@app.command("report", help="生成新的周报、月报或年报")
def report_cmd(
    report_type: str = typer.Argument(..., help="weekly, monthly, or yearly"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    profile: str | None = typer.Option(None, "--profile", help="OpenHarness provider profile"),
) -> None:
    """Generate a new report."""
    store = WoloStore(workspace)
    agent = OpenHarnessWoloAgent(
        profile=profile or load_config(workspace).provider_profile,
        record_model_call=store.record_llm_call,
    )
    report = asyncio.run(WoloProcessor(store, agent).generate_report(report_type))
    print(report.content)


@app.command("report-list", help="查看已生成的报告列表")
def report_list_cmd(
    report_type: str | None = typer.Option(None, "--type", help="Filter by type: weekly, monthly, yearly"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """List all reports."""
    store = WoloStore(workspace)
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
    store = WoloStore(workspace)
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
    store = WoloStore(workspace)
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

    store = WoloStore(workspace)
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
    store = WoloStore(workspace)
    reports = store.list_reports()
    matches = [r for r in reports if keyword.lower() in (r.content or "").lower()]
    matches.sort(key=lambda r: r.created_at, reverse=True)
    if not matches:
        print(f"No reports matching '{keyword}'.")
        return
    for r in matches:
        print(f"[{r.id}] {r.report_type:8s} {r.created_at}")
        # Show context around match
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
        from wolo.feed_digest import run_feed_digest
        from wolo.gateway.feed_digest_runner import _push_to_im

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


@app.command("status", help="查看 wolo 运行状态与数据概览")
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


@gateway_app.command("start", help="启动 wolo 后台网关")
def start_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    pid = start_gateway_process(cwd, workspace)
    print(f"wolo gateway started (pid={pid})")


@gateway_app.command("stop", help="停止 wolo 后台网关")
def stop_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    if stop_gateway_process(cwd, workspace):
        print("wolo gateway stopped.")
        return
    print("wolo gateway is not running.")


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
    from onboard.server import run_server

    run_server(host=host, port=port, reload=reload)


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
        f"interval_s={status['interval_s']} has_signals={status['has_signals']} "
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
        print("No wolo heartbeat signals.")
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
    from wolo.core.workspace import get_workspace_root

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



# ---------------------------------------------------------------------------
# project commands
# ---------------------------------------------------------------------------

def _resolve_project(store: WoloStore, ref: str) -> Project | None:
    """Resolve project by ID first, then by exact title match."""
    p = store.get_project(ref)
    if p:
        return p
    projects = store.list_projects()
    for proj in projects:
        if proj.title == ref:
            return proj
    ref_lower = ref.lower()
    for proj in projects:
        if proj.title.lower() == ref_lower:
            return proj
    return None


@project_app.command("list", help="查看项目列表")
def project_list_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    status: str | None = typer.Option(None, "--status", help="Filter: active / completed / archived"),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    from rich.console import Console
    from rich.table import Table

    store = WoloStore(workspace)
    projects = store.list_projects_with_detail(status=status, limit=limit)
    if not projects:
        print("No projects found.")
        return

    console = Console()
    table = Table(title="项目列表")
    table.add_column("项目", style="bold")
    table.add_column("进度", justify="right")
    table.add_column("里程碑", justify="right")
    table.add_column("目标日期")
    table.add_column("风险")

    for p in projects:
        pct = p.get("completion_pct")
        pct_str = f"{pct:.0%}" if pct is not None else "-"
        ms_str = f"{p.get('completed_milestone_count', 0)}/{p.get('milestone_count', 0)}"
        target = p.get("target_date", "") or "-"
        risk = p.get("risk_status", "normal")
        risk_style = {"normal": "green", "attention": "yellow", "at_risk": "red"}.get(risk, "")
        title_display = f"{p['title']}\n  [dim]{p['id'][:8]}[/dim]"
        table.add_row(
            title_display,
            pct_str,
            ms_str,
            target,
            f"[{risk_style}]{risk}[/{risk_style}]" if risk_style else risk,
        )
    console.print(table)


@project_app.command("create", help="创建新项目")
def project_create_cmd(
    title: str = typer.Argument(..., help="项目标题"),
    description: str = typer.Option("", "--description", "-d", help="项目描述"),
    target_date: str = typer.Option("", "--target-date", help="目标日期 (YYYY-MM-DD)"),
    priority: str = typer.Option("medium", "--priority", "-p", help="high / medium / low"),
    tags: str = typer.Option("", "--tags", help="逗号分隔的标签"),
    stakeholders: str = typer.Option("", "--stakeholders", help="逗号分隔的干系人"),
    success_criteria: str = typer.Option("", "--success-criteria", help="成功标准"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    now = _now()
    project = Project(
        id=str(uuid4()),
        title=title,
        description=description,
        target_date=target_date,
        priority=priority,
        tags=tags,
        stakeholders=stakeholders,
        success_criteria=success_criteria,
        start_date=now[:10],
        created_at=now,
        updated_at=now,
    )
    store.create_project(project)
    print(f"Created project: {project.title} ({project.id[:8]})")


@project_app.command("show", help="查看项目详情")
def project_show_cmd(
    ref: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    from rich.console import Console
    from rich.table import Table

    store = WoloStore(workspace)
    project = _resolve_project(store, ref)
    if project is None:
        print(f"Project not found: {ref}")
        raise typer.Exit(1)

    detail = store.get_project_detail(project.id)
    if detail is None:
        print(f"Project detail not available: {ref}")
        raise typer.Exit(1)

    console = Console()
    console.print(f"\n[bold]{detail['title']}[/bold]  ({detail['id'][:8]})")
    console.print(f"  状态: {detail['status']}  优先级: {detail['priority']}")
    if detail.get("description"):
        console.print(f"  描述: {detail['description']}")
    if detail.get("stakeholders"):
        console.print(f"  干系人: {detail['stakeholders']}")
    if detail.get("success_criteria"):
        console.print(f"  成功标准: {detail['success_criteria']}")
    if detail.get("tags"):
        console.print(f"  标签: {detail['tags']}")
    if detail.get("target_date"):
        console.print(f"  目标日期: {detail['target_date']}")

    pct = detail.get("completion_pct")
    if pct is not None:
        console.print(f"  进度: {pct:.0%} (来源: {detail.get('completion_source', '-')})")

    console.print(
        f"  关联: records={detail.get('linked_record_count', 0)} "
        f"todos={detail.get('linked_todo_count', 0)} "
        f"blockers={detail.get('open_blocker_count', 0)}"
    )
    console.print(
        f"  活跃: 7d={detail.get('activity_7d', 0)} 30d={detail.get('activity_30d', 0)} "
        f"risk={detail.get('risk_status', 'normal')}"
    )

    milestones = store.list_milestones(project.id)
    if milestones:
        table = Table(title="里程碑")
        table.add_column("ID", style="dim")
        table.add_column("标题")
        table.add_column("状态")
        table.add_column("目标日期")
        for m in milestones:
            style = "green" if m.status == "completed" else "yellow"
            table.add_row(m.id[:8], m.title, f"[{style}]{m.status}[/{style}]", m.target_date or "-")
        console.print(table)

    aliases = store.list_project_aliases(project.id)
    if aliases:
        console.print(f"  别名: {', '.join(a.alias for a in aliases)}")


@project_app.command("update", help="更新项目信息")
def project_update_cmd(
    ref: str = typer.Argument(..., help="项目 ID 或标题"),
    title: str | None = typer.Option(None, "--title", help="新标题"),
    description: str | None = typer.Option(None, "--description", "-d", help="新描述"),
    target_date: str | None = typer.Option(None, "--target-date", help="目标日期 (YYYY-MM-DD)"),
    priority: str | None = typer.Option(None, "--priority", "-p", help="high / medium / low"),
    tags: str | None = typer.Option(None, "--tags", help="逗号分隔的标签"),
    stakeholders: str | None = typer.Option(None, "--stakeholders", help="逗号分隔的干系人"),
    success_criteria: str | None = typer.Option(None, "--success-criteria", help="成功标准"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    project = _resolve_project(store, ref)
    if project is None:
        print(f"Project not found: {ref}")
        raise typer.Exit(1)

    updates: dict[str, str] = {}
    if title is not None:
        updates["title"] = title
    if description is not None:
        updates["description"] = description
    if target_date is not None:
        updates["target_date"] = target_date
    if priority is not None:
        updates["priority"] = priority
    if tags is not None:
        updates["tags"] = tags
    if stakeholders is not None:
        updates["stakeholders"] = stakeholders
    if success_criteria is not None:
        updates["success_criteria"] = success_criteria

    if not updates:
        print("No fields to update.")
        return
    store.update_project(project.id, **updates)
    print(f"Updated project: {project.title} ({project.id[:8]})")


@project_app.command("complete", help="标记项目为已完成")
def project_complete_cmd(
    ref: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    project = _resolve_project(store, ref)
    if project is None:
        print(f"Project not found: {ref}")
        raise typer.Exit(1)
    if store.complete_project(project.id):
        print(f"Completed project: {project.title} ({project.id[:8]})")
    else:
        print(f"Project is not active: {project.title}")


@project_app.command("archive", help="归档项目")
def project_archive_cmd(
    ref: str = typer.Argument(..., help="项目 ID 或标题"),
    reason: str = typer.Option("", "--reason", "-r", help="归档原因"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    project = _resolve_project(store, ref)
    if project is None:
        print(f"Project not found: {ref}")
        raise typer.Exit(1)
    if store.archive_project(project.id, reason=reason):
        print(f"Archived project: {project.title} ({project.id[:8]})")
    else:
        print(f"Failed to archive project: {project.title}")


@project_app.command("reactivate", help="重新激活已归档或已完成的项目")
def project_reactivate_cmd(
    ref: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    project = _resolve_project(store, ref)
    if project is None:
        print(f"Project not found: {ref}")
        raise typer.Exit(1)
    if store.reactivate_project(project.id):
        print(f"Reactivated project: {project.title} ({project.id[:8]})")
    else:
        print(f"Failed to reactivate project: {project.title}")


@project_app.command("delete", help="删除项目（不删除关联的源实体）")
def project_delete_cmd(
    ref: str = typer.Argument(..., help="项目 ID 或标题"),
    force: bool = typer.Option(False, "--force", "-f", help="跳过确认"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    project = _resolve_project(store, ref)
    if project is None:
        print(f"Project not found: {ref}")
        raise typer.Exit(1)
    if not force:
        typer.confirm(
            f"Delete project '{project.title}'? Linked records/todos/decisions will NOT be deleted.",
            abort=True,
        )
    if store.delete_project(project.id):
        print(f"Deleted project: {project.title} ({project.id[:8]})")
    else:
        print(f"Failed to delete project: {project.title}")


@project_app.command("link", help="将实体关联到项目")
def project_link_cmd(
    ref: str = typer.Argument(..., help="项目 ID 或标题"),
    entity_type: str = typer.Option(..., "--type", "-t", help="record / todo / decision / highlight / experiment"),
    entity_id: str = typer.Option(..., "--id", help="实体 ID"),
    source: str = typer.Option("user", "--source", help="user / ai_high_confidence / ai_candidate / migration"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    project = _resolve_project(store, ref)
    if project is None:
        print(f"Project not found: {ref}")
        raise typer.Exit(1)
    now = _now()
    link = ProjectLink(
        id=str(uuid4()),
        project_id=project.id,
        entity_type=entity_type,
        entity_id=entity_id,
        source=source,
        status="active",
        created_at=now,
        updated_at=now,
    )
    store.create_project_link(link)
    print(f"Linked {entity_type} {entity_id[:8]} to project {project.title}")


@project_app.command("unlink", help="取消实体与项目的关联")
def project_unlink_cmd(
    link_id: str = typer.Argument(..., help="关联 ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    if store.delete_project_link(link_id):
        print(f"Removed project link: {link_id[:8]}")
    else:
        print(f"Project link not found: {link_id}")


# --- milestone sub-group ---

@milestone_app.command("add", help="为项目添加里程碑")
def milestone_add_cmd(
    ref: str = typer.Argument(..., help="项目 ID 或标题"),
    title: str = typer.Argument(..., help="里程碑标题"),
    target_date: str = typer.Option("", "--target-date", help="目标日期 (YYYY-MM-DD)"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    project = _resolve_project(store, ref)
    if project is None:
        print(f"Project not found: {ref}")
        raise typer.Exit(1)
    now = _now()
    milestone = Milestone(
        id=str(uuid4()),
        project_id=project.id,
        title=title,
        target_date=target_date,
        created_at=now,
        updated_at=now,
    )
    store.create_milestone(milestone)
    print(f"Added milestone: {milestone.title} ({milestone.id[:8]}) to {project.title}")


@milestone_app.command("complete", help="标记里程碑为已完成")
def milestone_complete_cmd(
    milestone_id: str = typer.Argument(..., help="里程碑 ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    if store.complete_milestone(milestone_id):
        print(f"Completed milestone: {milestone_id[:8]}")
    else:
        print(f"Milestone not found or already completed: {milestone_id}")


@milestone_app.command("delete", help="删除里程碑")
def milestone_delete_cmd(
    milestone_id: str = typer.Argument(..., help="里程碑 ID"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    store = WoloStore(workspace)
    if store.delete_milestone(milestone_id):
        print(f"Deleted milestone: {milestone_id[:8]}")
    else:
        print(f"Milestone not found: {milestone_id}")


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

@project_app.command("review", help="生成项目回顾报告")
def project_review_cmd(
    project: str = typer.Argument(..., help="项目 ID 或标题"),
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    from wolo.core.models import WoloReport
    store = WoloStore(workspace)
    p = _resolve_project(store, project)
    if p is None:
        print(f"Project not found: {project}")
        raise typer.Exit(1)

    detail = store.get_project_detail(p.id)
    milestones = store.list_milestones(p.id)
    links = store.list_project_links(project_id=p.id)

    lines = []
    lines.append(f"# Project Review: {p.title}")
    lines.append("")
    if p.description:
        lines.append(p.description)
        lines.append("")
    lines.append(f"- Status: {p.status}")
    lines.append(f"- Priority: {p.priority}")
    if p.stakeholders:
        lines.append(f"- Stakeholders: {p.stakeholders}")
    if p.success_criteria:
        lines.append(f"- Success criteria: {p.success_criteria}")
    if p.start_date:
        lines.append(f"- Start: {p.start_date}")
    if p.target_date:
        lines.append(f"- Target: {p.target_date}")
    if p.completed_at:
        lines.append(f"- Completed: {p.completed_at}")
    lines.append("")

    lines.append("## Milestones")
    if milestones:
        for m in milestones:
            status = "✓" if m.status == "completed" else "○"
            date = f" ({m.target_date})" if m.target_date else ""
            lines.append(f"- {status} {m.title}{date}")
    else:
        lines.append("- No milestones defined")
    lines.append("")

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

    from uuid import uuid4
    report = WoloReport(
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

