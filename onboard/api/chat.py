"""WebSocket chat routes and session history API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse

from onboard.services.chat_service import stream_chat

router = APIRouter(tags=["chat"])


def _get_workspace(app_name: str) -> Path:
    if app_name == "solo":
        from solo.core.workspace import get_workspace_root
        return get_workspace_root(None)
    else:
        from wolo.core.workspace import get_workspace_root
        return get_workspace_root(None)


def _get_session_module(app_name: str):
    if app_name == "solo":
        from solo.core import session
        return session
    else:
        from wolo.core import session
        return session


@router.websocket("/ws/chat/{app_name}")
async def chat(websocket: WebSocket, app_name: str) -> None:
    await websocket.accept()
    session_key = websocket.query_params.get("session") or f"web-{uuid4().hex[:12]}"
    # Notify client of assigned session key
    await websocket.send_json({"type": "session_key", "session_key": session_key})
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "cancel":
                await websocket.send_json({"type": "complete", "content": ""})
                continue
            if message_type != "message":
                await websocket.send_json({"type": "error", "message": "Unsupported message type"})
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                await websocket.send_json({"type": "error", "message": "Message content is required"})
                continue
            try:
                async for event in stream_chat(app_name, content, session_key=session_key):
                    await websocket.send_json(event)
            except Exception as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        return


@router.get("/api/{app_name}/chat/sessions")
def list_sessions(
    app_name: str,
    limit: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
) -> list[dict[str, Any]]:
    """List recent chat sessions (within 30 days)."""
    session_mod = _get_session_module(app_name)
    workspace = _get_workspace(app_name)
    sessions = session_mod.list_conversations(workspace, limit=limit)

    # Filter to last 30 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    sessions = [s for s in sessions if (s.get("updated_at") or "") >= cutoff]

    # Search by keyword in first user message (preview)
    if search:
        keyword = search.lower()
        filtered = []
        for s in sessions:
            messages, _ = session_mod.load_conversation(workspace, s["session_key"])
            preview = next((m.text[:100] for m in messages if m.role == "user" and m.text.strip()), "")
            if keyword in preview.lower():
                s["preview"] = preview
                filtered.append(s)
            else:
                # Also search assistant responses
                for m in messages:
                    if m.text and keyword in m.text.lower():
                        s["preview"] = preview
                        filtered.append(s)
                        break
        sessions = filtered
    else:
        # Add preview (first user message) for display
        for s in sessions:
            messages, _ = session_mod.load_conversation(workspace, s["session_key"])
            s["preview"] = next((m.text[:100] for m in messages if m.role == "user" and m.text.strip()), "")

    return sessions


@router.get("/api/{app_name}/chat/sessions/{session_key}")
def get_session(app_name: str, session_key: str) -> dict[str, Any]:
    """Get full message history for a session."""
    session_mod = _get_session_module(app_name)
    workspace = _get_workspace(app_name)
    messages, session_id = session_mod.load_conversation(workspace, session_key)
    return {
        "session_key": session_key,
        "session_id": session_id,
        "messages": [
            {"role": m.role, "content": m.text}
            for m in messages
            if m.role in ("user", "assistant") and m.text.strip()
        ],
    }


@router.delete("/api/{app_name}/chat/sessions/{session_key}")
def delete_session(app_name: str, session_key: str) -> dict[str, bool]:
    """Delete a chat session."""
    session_mod = _get_session_module(app_name)
    workspace = _get_workspace(app_name)
    conn = session_mod._get_db(workspace)
    try:
        cur = conn.execute("DELETE FROM conversations WHERE session_key = ?", (session_key,))
        conn.commit()
        return {"deleted": cur.rowcount > 0}
    finally:
        conn.close()


@router.get("/api/{app_name}/chat/sessions/{session_key}/export/markdown")
def export_markdown(app_name: str, session_key: str) -> PlainTextResponse:
    """Export a session as formatted Markdown."""
    session_mod = _get_session_module(app_name)
    workspace = _get_workspace(app_name)
    messages, _ = session_mod.load_conversation(workspace, session_key)

    lines = [f"# Chat Session: {session_key}\n"]
    for m in messages:
        if not m.text.strip():
            continue
        if m.role == "user":
            lines.append(f"## 🧑 User\n\n{m.text.strip()}\n")
        elif m.role == "assistant":
            lines.append(f"## 🤖 Assistant\n\n{m.text.strip()}\n")

    content = "\n".join(lines)
    return PlainTextResponse(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="chat-{session_key}.md"'},
    )


@router.get("/api/{app_name}/chat/sessions/{session_key}/export/html")
def export_html(app_name: str, session_key: str) -> HTMLResponse:
    """Export a session as formatted HTML."""
    session_mod = _get_session_module(app_name)
    workspace = _get_workspace(app_name)
    messages, _ = session_mod.load_conversation(workspace, session_key)

    msg_blocks: list[str] = []
    for m in messages:
        if not m.text.strip():
            continue
        escaped = m.text.strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if m.role == "user":
            msg_blocks.append(
                f'<div class="msg user"><div class="label">🧑 User</div><div class="content">{escaped}</div></div>'
            )
        elif m.role == "assistant":
            msg_blocks.append(
                f'<div class="msg assistant"><div class="label">🤖 Assistant</div><div class="content">{escaped}</div></div>'
            )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Chat: {session_key}</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; background: #0a0a0f; color: #e4e4e7; }}
h1 {{ font-size: 1.4rem; border-bottom: 1px solid #2e2e33; padding-bottom: 0.5rem; }}
.msg {{ margin: 1.5rem 0; padding: 1rem; border-radius: 8px; }}
.msg.user {{ background: #1c1917; border-left: 3px solid #d4a574; }}
.msg.assistant {{ background: #1c1c21; border-left: 3px solid #5eead4; }}
.label {{ font-size: 0.75rem; font-weight: 600; margin-bottom: 0.5rem; opacity: 0.7; }}
.content {{ white-space: pre-wrap; line-height: 1.6; }}
</style>
</head>
<body>
<h1>Chat Session: {session_key}</h1>
{"".join(msg_blocks)}
</body>
</html>"""
    return HTMLResponse(
        html,
        headers={"Content-Disposition": f'attachment; filename="chat-{session_key}.html"'},
    )
