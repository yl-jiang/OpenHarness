"""Session routing for ohmo gateway."""

from __future__ import annotations

from openharness.channels.bus.events import InboundMessage


def session_key_for_message(message: InboundMessage) -> str:
    """Route sessions by sender plus chat/thread when available.

    Thread IDs are only used for session routing in group chats, where each
    thread represents an independent topic.  In p2p chats the entire
    conversation is one session, so thread_id is intentionally ignored to
    prevent Feishu (and similar platforms) from fragmenting the session every
    time the platform attaches a reply-thread ID to a message.
    """
    if message.session_key_override:
        return message.session_key_override
    sender_id = str(message.sender_id).strip() or "anonymous"
    chat_type = str(message.metadata.get("chat_type") or "").strip().lower()
    if chat_type == "group":
        thread_id = (
            message.metadata.get("thread_id")
            or message.metadata.get("thread_ts")
            or message.metadata.get("message_thread_id")
        )
        if thread_id:
            return f"{message.channel}:{message.chat_id}:{thread_id}:{sender_id}"
    return f"{message.channel}:{message.chat_id}:{sender_id}"

