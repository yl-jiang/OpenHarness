"""Tests for Feishu merge_forward (合并转发) message expansion."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.feishu import FeishuChannel
from openharness.config.schema import FeishuConfig


def _make_channel() -> FeishuChannel:
    return FeishuChannel(
        FeishuConfig(app_id="test", app_secret="test"),
        MessageBus(),
    )


def _make_message_item(msg_type: str, content: dict, sender_id: str = "", message_id: str = "") -> SimpleNamespace:
    """Build a fake message item matching the lark_oapi GetMessage response shape."""
    sender = SimpleNamespace(id=sender_id, id_type="open_id", sender_type="user")
    return SimpleNamespace(
        msg_type=msg_type,
        body=SimpleNamespace(content=json.dumps(content)),
        sender=sender,
        message_id=message_id,
    )


# ── _extract_text_from_message_item ──────────────────────────────────────


class TestExtractTextFromMessageItem:
    def test_text_message(self):
        item = _make_message_item("text", {"text": "hello world"})
        assert FeishuChannel._extract_text_from_message_item(item) == "hello world"

    def test_post_message(self):
        item = _make_message_item("post", {
            "zh_cn": {
                "title": "Title",
                "content": [[{"tag": "text", "text": "body text"}]],
            }
        })
        result = FeishuChannel._extract_text_from_message_item(item)
        assert "Title" in result
        assert "body text" in result

    def test_interactive_message(self):
        item = _make_message_item("interactive", {
            "header": {"title": {"content": "Card Title"}},
            "elements": [],
        })
        result = FeishuChannel._extract_text_from_message_item(item)
        assert "Card Title" in result

    def test_merge_forward_no_recurse(self):
        """Nested merge_forward should return placeholder, not recurse."""
        item = _make_message_item("merge_forward", {})
        assert FeishuChannel._extract_text_from_message_item(item) == "[merged forward messages]"

    def test_empty_body(self):
        item = SimpleNamespace(msg_type="text", body=None, sender=None)
        assert FeishuChannel._extract_text_from_message_item(item) == ""

    def test_invalid_json_body(self):
        item = SimpleNamespace(
            msg_type="text",
            body=SimpleNamespace(content="not valid json{{{"),
            sender=None,
        )
        assert FeishuChannel._extract_text_from_message_item(item) == ""


# ── _fetch_merge_forward_content_sync ────────────────────────────────────


class TestFetchMergeForwardContentSync:
    def test_expands_sub_messages(self):
        channel = _make_channel()
        # Fake the REST client so we don't hit real APIs
        envelope = _make_message_item("merge_forward", {}, sender_id="")
        sub1 = _make_message_item("text", {"text": "first message"}, sender_id="ou_alice")
        sub2 = _make_message_item("text", {"text": "second message"}, sender_id="ou_bob")

        fake_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(items=[envelope, sub1, sub2]),
        )
        fake_client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=SimpleNamespace(
                        get=lambda req: fake_response,
                    )
                )
            )
        )
        channel._client = fake_client
        # Mock sender name resolution to return known names
        with patch.object(channel, "_resolve_sender_display_name_sync", side_effect=lambda oid: {"ou_alice": "Alice", "ou_bob": "Bob"}.get(oid, oid)):
            text, images = channel._fetch_merge_forward_content_sync("om_test123")

        assert "[merged forward messages]" in text
        assert "Alice: first message" in text
        assert "Bob: second message" in text
        assert images == []

    def test_fallback_when_no_sub_messages(self):
        channel = _make_channel()
        envelope = _make_message_item("merge_forward", {})
        fake_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(items=[envelope]),
        )
        fake_client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=SimpleNamespace(get=lambda req: fake_response)
                )
            )
        )
        channel._client = fake_client
        text, images = channel._fetch_merge_forward_content_sync("om_test")
        assert text == "[merged forward messages]"
        assert images == []

    def test_fallback_on_api_failure(self):
        channel = _make_channel()
        fake_response = SimpleNamespace(
            success=lambda: False,
            code=400,
            msg="error",
        )
        fake_client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=SimpleNamespace(get=lambda req: fake_response)
                )
            )
        )
        channel._client = fake_client
        text, images = channel._fetch_merge_forward_content_sync("om_test")
        assert text == "[merged forward messages]"
        assert images == []

    def test_fallback_on_exception(self):
        channel = _make_channel()

        def raise_error(req):
            raise RuntimeError("network error")

        fake_client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=SimpleNamespace(get=raise_error)
                )
            )
        )
        channel._client = fake_client
        text, images = channel._fetch_merge_forward_content_sync("om_test")
        assert text == "[merged forward messages]"
        assert images == []

    def test_mixed_message_types(self):
        """Sub-messages can be different types (text, post, image, etc.)."""
        channel = _make_channel()
        envelope = _make_message_item("merge_forward", {})
        sub_text = _make_message_item("text", {"text": "hello"}, sender_id="ou_a", message_id="om_sub1")
        sub_post = _make_message_item("post", {
            "zh_cn": {"title": "", "content": [[{"tag": "text", "text": "rich text"}]]}
        }, sender_id="ou_b", message_id="om_sub2")
        # Image message: no text extractable, should use MSG_TYPE_MAP fallback
        sub_image = _make_message_item("image", {"image_key": "img_xxx"}, sender_id="ou_c", message_id="om_sub3")

        fake_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(items=[envelope, sub_text, sub_post, sub_image]),
        )
        fake_client = SimpleNamespace(
            im=SimpleNamespace(
                v1=SimpleNamespace(
                    message=SimpleNamespace(get=lambda req: fake_response)
                )
            )
        )
        channel._client = fake_client
        with patch.object(channel, "_resolve_sender_display_name_sync", return_value=""):
            text, images = channel._fetch_merge_forward_content_sync("om_test")

        assert "hello" in text
        assert "rich text" in text
        assert "[image]" in text
        assert images == [("img_xxx", "om_sub3")]


# ── _fetch_merge_forward_content (async) ─────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_merge_forward_content_no_client():
    channel = _make_channel()
    channel._client = None
    text, media = await channel._fetch_merge_forward_content("om_test")
    assert text == "[merged forward messages]"
    assert media == []


@pytest.mark.asyncio
async def test_fetch_merge_forward_content_no_message_id():
    channel = _make_channel()
    channel._client = object()  # non-None
    text, media = await channel._fetch_merge_forward_content("")
    assert text == "[merged forward messages]"
    assert media == []
