"""Tests for Feishu channel security and API correctness."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.feishu import FeishuChannel, _extract_feishu_mentions, _feishu_mentions_bot
from openharness.config.schema import FeishuConfig
from ohmo.group_registry import save_managed_group_record


def test_feishu_event_handler_registers_noop_processors_for_noise_events():
    channel = FeishuChannel(
        FeishuConfig(encrypt_key="encrypt", verification_token="verify"),
        MessageBus(),
    )
    calls: list[tuple[str, object]] = []

    class FakeBuilder:
        def register_p2_im_message_receive_v1(self, handler):
            calls.append(("receive", handler))
            return self

        def register_p2_im_message_message_read_v1(self, handler):
            calls.append(("read", handler))
            return self

        def register_p2_im_message_reaction_created_v1(self, handler):
            calls.append(("reaction_created", handler))
            return self

        def build(self):
            return "handler"

    class FakeDispatcher:
        @staticmethod
        def builder(encrypt_key: str, verification_token: str):
            calls.append((f"builder:{encrypt_key}:{verification_token}", object()))
            return FakeBuilder()

    fake_lark = SimpleNamespace(EventDispatcherHandler=FakeDispatcher)

    handler = channel._build_event_handler(fake_lark)

    assert handler == "handler"
    assert [name for name, _ in calls] == [
        "builder:encrypt:verify",
        "receive",
        "read",
        "reaction_created",
    ]
    for name, registered_handler in calls[2:]:
        assert name in {"read", "reaction_created"}
        registered_handler(SimpleNamespace())


@pytest.mark.asyncio
async def test_feishu_send_does_not_reply_in_thread_for_private_messages(monkeypatch):
    channel = FeishuChannel(FeishuConfig(), MessageBus())
    channel._client = object()
    sent: list[str | None] = []

    def fake_send(*args):
        sent.append(args[-1])
        return True

    monkeypatch.setattr(channel, "_send_message_sync", fake_send)

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_private",
            content="hello",
            metadata={"chat_type": "p2p", "message_id": "om_private"},
        )
    )

    assert sent == [None]


@pytest.mark.asyncio
async def test_feishu_send_replies_in_thread_for_group_messages(monkeypatch):
    channel = FeishuChannel(FeishuConfig(), MessageBus())
    channel._client = object()
    sent: list[str | None] = []

    def fake_send(*args):
        sent.append(args[-1])
        return True

    monkeypatch.setattr(channel, "_send_message_sync", fake_send)

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_group",
            content="hello",
            metadata={"chat_type": "group", "message_id": "om_group"},
        )
    )

    assert sent == ["om_group"]


def test_feishu_fetch_quoted_message_sync_extracts_role_sender_and_time():
    channel = FeishuChannel(FeishuConfig(), MessageBus())
    item = SimpleNamespace(
        msg_type="text",
        body=SimpleNamespace(content=json.dumps({"text": "上一条助手回复"}, ensure_ascii=False)),
        create_time="1749211935000",
        sender=SimpleNamespace(id="ou_bot", sender_type="app"),
    )
    fake_response = SimpleNamespace(
        success=lambda: True,
        data=SimpleNamespace(items=[item]),
    )
    channel._client = SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(
                message=SimpleNamespace(get=lambda req: fake_response)
            )
        )
    )

    quoted = channel._fetch_quoted_message_sync("parent-123", current_sender_id="ou_user")
    expected_sent_at = datetime.fromtimestamp(1749211935, timezone.utc).astimezone().isoformat()

    assert quoted == {
        "message_id": "parent-123",
        "role": "assistant",
        "content": "上一条助手回复",
        "msg_type": "text",
        "sender_type": "app",
        "sender_id": "ou_bot",
        "sender_label": "assistant",
        "sent_at": expected_sent_at,
    }


@pytest.mark.asyncio
async def test_feishu_reply_forwards_structured_quote_metadata_without_mutating_content(monkeypatch):
    channel = FeishuChannel(
        FeishuConfig(allow_from=["user-open-id"], react_emoji="OK"),
        MessageBus(),
    )
    reactions: list[str] = []
    forwarded: list[dict[str, object]] = []

    async def fake_add_reaction(message_id: str, emoji_type: str = "OK") -> None:
        reactions.append(f"{message_id}:{emoji_type}")

    async def fake_handle_message(**kwargs):
        forwarded.append(kwargs)

    async def fake_fetch_quoted_message(message_id: str, *, current_sender_id: str):
        assert message_id == "parent-123"
        assert current_sender_id == "user-open-id"
        return {
            "message_id": "parent-123",
            "role": "assistant",
            "sender_label": "OpenHarness Assistant",
            "sender_type": "app",
            "sent_at": "2026-06-06T20:12:15+08:00",
            "msg_type": "text",
            "content": "上一条被引用的消息",
        }

    monkeypatch.setattr(channel, "_add_reaction", fake_add_reaction)
    monkeypatch.setattr(channel, "_handle_message", fake_handle_message)
    monkeypatch.setattr(channel, "_fetch_quoted_message", fake_fetch_quoted_message)
    monkeypatch.setattr(channel, "_resolve_sender_display_name_sync", lambda sender_id: "Allowed User")

    event = SimpleNamespace(
        sender=SimpleNamespace(
            sender_type="user",
            sender_id=SimpleNamespace(open_id="user-open-id"),
        ),
        message=SimpleNamespace(
            message_id="message-reply",
            chat_id="ou_user",
            chat_type="p2p",
            message_type="text",
            content=json.dumps({"text": "这是当前回复"}, ensure_ascii=False),
            parent_id="parent-123",
        ),
    )

    await channel._on_message(SimpleNamespace(event=event))

    assert reactions == ["message-reply:OK"]
    assert len(forwarded) == 1
    assert forwarded[0]["content"] == "这是当前回复"
    assert forwarded[0]["metadata"]["parent_id"] == "parent-123"
    assert forwarded[0]["metadata"]["quoted_context"] == "上一条被引用的消息"
    assert forwarded[0]["metadata"]["quoted_message"] == {
        "message_id": "parent-123",
        "role": "assistant",
        "sender_label": "OpenHarness Assistant",
        "sender_type": "app",
        "sent_at": "2026-06-06T20:12:15+08:00",
        "msg_type": "text",
        "content": "上一条被引用的消息",
    }


@pytest.mark.asyncio
async def test_feishu_create_managed_group_builds_expected_request():
    channel = FeishuChannel(FeishuConfig(), MessageBus())
    captured = {}

    class FakeChat:
        def create(self, request):
            captured["request"] = request
            return SimpleNamespace(
                success=lambda: True,
                data=SimpleNamespace(chat_id="oc_new_group"),
            )

    channel._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(chat=FakeChat())))

    chat_id = await channel.create_managed_group(user_open_id="ou_user", name="OpenHarness 讨论群")

    request = captured["request"]
    assert chat_id == "oc_new_group"
    assert request.user_id_type == "open_id"
    assert request.set_bot_manager is True
    assert request.body.name == "OpenHarness 讨论群"
    assert request.body.user_id_list == ["ou_user"]
    assert request.body.chat_mode == "group"
    assert request.body.chat_type == "private"


@pytest.mark.asyncio
async def test_feishu_create_managed_group_reports_api_failure():
    channel = FeishuChannel(FeishuConfig(), MessageBus())

    class FakeChat:
        def create(self, request):
            return SimpleNamespace(
                success=lambda: False,
                code=99991663,
                msg="missing scope",
                get_log_id=lambda: "log-1",
            )

    channel._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(chat=FakeChat())))

    with pytest.raises(RuntimeError, match="99991663.*missing scope.*log-1"):
        await channel.create_managed_group(user_open_id="ou_user", name="OpenHarness 讨论群")


@pytest.mark.asyncio
async def test_feishu_rename_group_builds_expected_request():
    channel = FeishuChannel(FeishuConfig(), MessageBus())
    captured = {}

    class FakeChat:
        def update(self, request):
            captured["request"] = request
            return SimpleNamespace(success=lambda: True)

    channel._client = SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(chat=FakeChat())))

    await channel.rename_group(chat_id="oc_group", name="New Name")

    request = captured["request"]
    assert request.user_id_type == "open_id"
    assert request.chat_id == "oc_group"
    assert request.body.name == "New Name"


def test_feishu_extracts_text_mentions_and_matches_bot_name():
    content = {
        "text": "@_user_1 帮我看看",
        "mentions": [
            {
                "key": "@_user_1",
                "id": {"open_id": "ou_bot"},
                "name": "ohmo",
            }
        ],
    }

    assert _extract_feishu_mentions(content) == [
        {"key": "@_user_1", "name": "ohmo", "open_id": "ou_bot", "user_id": "", "union_id": ""}
    ]
    assert _feishu_mentions_bot(content, content["text"], FeishuConfig(bot_names=["ohmo"])) is True


def test_feishu_extracts_sdk_message_mentions():
    mention = SimpleNamespace(
        key="@_user_1",
        id=SimpleNamespace(open_id="ou_bot", user_id="user_bot", union_id=""),
        name="ohmo",
    )

    assert _extract_feishu_mentions({"text": "@_user_1 帮我看看"}, [mention]) == [
        {
            "key": "@_user_1",
            "name": "ohmo",
            "open_id": "ou_bot",
            "user_id": "user_bot",
            "union_id": "",
        }
    ]


def test_feishu_mention_detection_can_use_bot_open_id():
    content = {
        "text": "@_user_1 帮我看看",
        "mentions": [
            {
                "key": "@_user_1",
                "id": {"open_id": "ou_exact_bot"},
                "name": "Different Display Name",
            }
        ],
    }

    assert _feishu_mentions_bot(
        content,
        content["text"],
        FeishuConfig(bot_open_id="ou_exact_bot", bot_names=["ohmo"]),
    ) is True


def test_feishu_mention_detection_ignores_other_users():
    content = {
        "text": "@_user_1 帮我看看",
        "mentions": [
            {
                "key": "@_user_1",
                "id": {"open_id": "ou_other"},
                "name": "Alice",
            }
        ],
    }

    assert _feishu_mentions_bot(content, content["text"], FeishuConfig(bot_names=["ohmo"])) is False


@pytest.mark.asyncio
async def test_feishu_group_policy_ignores_unmentioned_unmanaged_group_without_reaction(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "ohmo"
    workspace.mkdir()
    monkeypatch.setenv("OHMO_WORKSPACE", str(workspace))
    channel = FeishuChannel(
        FeishuConfig(
            allow_from=["user-open-id"],
            react_emoji="OK",
            group_policy="managed_or_mention",
            bot_names=["ohmo"],
        ),
        MessageBus(),
    )
    reactions: list[str] = []
    forwarded: list[dict] = []

    async def fake_add_reaction(message_id: str, emoji_type: str = "OK") -> None:
        reactions.append(f"{message_id}:{emoji_type}")

    async def fake_handle_message(**kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr(channel, "_add_reaction", fake_add_reaction)
    monkeypatch.setattr(channel, "_handle_message", fake_handle_message)
    monkeypatch.setattr(channel, "_resolve_sender_display_name_sync", lambda sender_id: "Allowed User")

    event = SimpleNamespace(
        sender=SimpleNamespace(
            sender_type="user",
            sender_id=SimpleNamespace(open_id="user-open-id"),
        ),
        message=SimpleNamespace(
            message_id="message-unmanaged",
            chat_id="oc_unmanaged",
            chat_type="group",
            message_type="text",
            content=json.dumps({"text": "这个普通群消息不应该触发"}),
        ),
    )

    await channel._on_message(SimpleNamespace(event=event))

    assert reactions == []
    assert forwarded == []


@pytest.mark.asyncio
async def test_feishu_group_policy_allows_managed_group_without_mention(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "ohmo"
    workspace.mkdir()
    monkeypatch.setenv("OHMO_WORKSPACE", str(workspace))
    save_managed_group_record(
        workspace=workspace,
        channel="feishu",
        chat_id="oc_managed",
        owner_open_id="user-open-id",
        name="Managed Group",
    )
    channel = FeishuChannel(
        FeishuConfig(
            allow_from=["user-open-id"],
            react_emoji="OK",
            group_policy="managed_or_mention",
            bot_names=["ohmo"],
        ),
        MessageBus(),
    )
    reactions: list[str] = []
    forwarded: list[dict] = []

    async def fake_add_reaction(message_id: str, emoji_type: str = "OK") -> None:
        reactions.append(f"{message_id}:{emoji_type}")

    async def fake_handle_message(**kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr(channel, "_add_reaction", fake_add_reaction)
    monkeypatch.setattr(channel, "_handle_message", fake_handle_message)
    monkeypatch.setattr(channel, "_resolve_sender_display_name_sync", lambda sender_id: "Allowed User")

    event = SimpleNamespace(
        sender=SimpleNamespace(
            sender_type="user",
            sender_id=SimpleNamespace(open_id="user-open-id"),
        ),
        message=SimpleNamespace(
            message_id="message-managed",
            chat_id="oc_managed",
            chat_type="group",
            message_type="text",
            content=json.dumps({"text": "managed 群不用 @ 也应该触发"}),
        ),
    )

    await channel._on_message(SimpleNamespace(event=event))

    assert reactions == ["message-managed:OK"]
    assert len(forwarded) == 1
    assert forwarded[0]["metadata"]["mentions_bot"] is False


@pytest.mark.asyncio
async def test_feishu_group_policy_allows_mentioned_unmanaged_group(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "ohmo"
    workspace.mkdir()
    monkeypatch.setenv("OHMO_WORKSPACE", str(workspace))
    channel = FeishuChannel(
        FeishuConfig(
            allow_from=["user-open-id"],
            react_emoji="OK",
            group_policy="managed_or_mention",
            bot_names=["ohmo"],
        ),
        MessageBus(),
    )
    reactions: list[str] = []
    forwarded: list[dict] = []

    async def fake_add_reaction(message_id: str, emoji_type: str = "OK") -> None:
        reactions.append(f"{message_id}:{emoji_type}")

    async def fake_handle_message(**kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr(channel, "_add_reaction", fake_add_reaction)
    monkeypatch.setattr(channel, "_handle_message", fake_handle_message)
    monkeypatch.setattr(channel, "_resolve_sender_display_name_sync", lambda sender_id: "Allowed User")

    event = SimpleNamespace(
        sender=SimpleNamespace(
            sender_type="user",
            sender_id=SimpleNamespace(open_id="user-open-id"),
        ),
        message=SimpleNamespace(
            message_id="message-mentioned",
            chat_id="oc_unmanaged",
            chat_type="group",
            message_type="text",
            content=json.dumps(
                {
                    "text": "@_user_1 帮我看看",
                    "mentions": [
                        {
                            "key": "@_user_1",
                            "id": {"open_id": "ou_bot"},
                            "name": "ohmo",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        ),
    )

    await channel._on_message(SimpleNamespace(event=event))

    assert reactions == ["message-mentioned:OK"]
    assert len(forwarded) == 1
    assert forwarded[0]["metadata"]["mentions_bot"] is True


@pytest.mark.asyncio
async def test_feishu_group_policy_allows_sdk_message_mention(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / "ohmo"
    workspace.mkdir()
    monkeypatch.setenv("OHMO_WORKSPACE", str(workspace))
    channel = FeishuChannel(
        FeishuConfig(
            allow_from=["user-open-id"],
            react_emoji="OK",
            group_policy="managed_or_mention",
            bot_names=["ohmo"],
        ),
        MessageBus(),
    )
    reactions: list[str] = []
    forwarded: list[dict] = []

    async def fake_add_reaction(message_id: str, emoji_type: str = "OK") -> None:
        reactions.append(f"{message_id}:{emoji_type}")

    async def fake_handle_message(**kwargs):
        forwarded.append(kwargs)

    monkeypatch.setattr(channel, "_add_reaction", fake_add_reaction)
    monkeypatch.setattr(channel, "_handle_message", fake_handle_message)
    monkeypatch.setattr(channel, "_resolve_sender_display_name_sync", lambda sender_id: "Allowed User")

    event = SimpleNamespace(
        sender=SimpleNamespace(
            sender_type="user",
            sender_id=SimpleNamespace(open_id="user-open-id"),
        ),
        message=SimpleNamespace(
            message_id="message-sdk-mentioned",
            chat_id="oc_unmanaged",
            chat_type="group",
            message_type="text",
            content=json.dumps({"text": "@_user_1 帮我看看"}, ensure_ascii=False),
            mentions=[
                SimpleNamespace(
                    key="@_user_1",
                    id=SimpleNamespace(open_id="ou_bot", user_id="", union_id=""),
                    name="ohmo",
                )
            ],
        ),
    )

    await channel._on_message(SimpleNamespace(event=event))

    assert reactions == ["message-sdk-mentioned:OK"]
    assert len(forwarded) == 1
    assert forwarded[0]["metadata"]["mentions_bot"] is True
    assert forwarded[0]["metadata"]["mentions"][0]["open_id"] == "ou_bot"
