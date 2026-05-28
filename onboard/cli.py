"""CLI for the onboard WebUI dashboard."""

from __future__ import annotations

import typer

from onboard.server import (
    OnboardServerError,
    run_server,
    server_status,
    start_background,
    stop_background,
)


app = typer.Typer(
    name="onboard",
    help="Unified WebUI dashboard for solo and wolo.",
    add_completion=False,
)


@app.command("run")
def run_cmd(
    host: str = typer.Option("0.0.0.0", "--host", help="Host interface to bind"),
    port: int = typer.Option(8090, "--port", min=1, max=65535, help="Port to bind"),
    reload: bool = typer.Option(False, "--reload", help="Enable uvicorn reload"),
) -> None:
    """Start onboard in the foreground."""
    try:
        run_server(host=host, port=port, reload=reload)
    except OnboardServerError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command("start")
def start_cmd(
    host: str = typer.Option("0.0.0.0", "--host", help="Host interface to bind"),
    port: int = typer.Option(8090, "--port", min=1, max=65535, help="Port to bind"),
) -> None:
    """Start onboard in the background."""
    from onboard.auth import get_token

    pid = start_background(host=host, port=port)
    token = get_token()
    print(f"onboard started (pid={pid}, url=http://{host}:{port})")
    print(f"  🔑 Access token: {token}")
    print(f"  🔗 Direct link:  http://{host}:{port}?token={token}")


@app.command("stop")
def stop_cmd() -> None:
    """Stop background onboard."""
    if stop_background():
        print("onboard stopped.")
        return
    print("onboard is not running.")


@app.command("status")
def status_cmd() -> None:
    """Show onboard process status."""
    status = server_status()
    print(
        f"onboard: {status['status']} | pid={status['pid']} | "
        f"url=http://{status['host']}:{status['port']} | log={status['log_file']}"
    )


@app.command("token")
def token_cmd(
    reset: bool = typer.Option(False, "--reset", help="Generate a new token"),
) -> None:
    """Show or reset the access token."""
    from onboard.auth import get_token, reset_token

    if reset:
        new_token = reset_token()
        print(f"Token reset. New token: {new_token}")
        print("(All existing sessions are now invalid.)")
    else:
        print(get_token())
