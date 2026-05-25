"""WebSocket chat routes."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from onboard.services.chat_service import stream_chat


router = APIRouter(tags=["chat"])


@router.websocket("/ws/chat/{app_name}")
async def chat(websocket: WebSocket, app_name: str) -> None:
    await websocket.accept()
    session_key = websocket.query_params.get("session") or f"web-{uuid4().hex[:12]}"
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
