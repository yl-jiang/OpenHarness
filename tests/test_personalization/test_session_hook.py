from __future__ import annotations

from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock
from openharness.personalization import session_hook


def test_update_rules_from_session_ignores_tool_result_noise(monkeypatch):
    saved: dict[str, object] = {}

    monkeypatch.setattr(session_hook, "load_facts", lambda: {"facts": []})
    monkeypatch.setattr(session_hook, "save_facts", lambda facts: saved.setdefault("facts", facts))
    monkeypatch.setattr(session_hook, "save_local_rules", lambda rules: saved.setdefault("rules", rules))

    messages = [
        ConversationMessage(role="user", content=[TextBlock(text="ssh ops@10.0.0.8")]),
        ConversationMessage(
            role="assistant",
            content=[
                TextBlock(text='export OPENAI_BASE_URL="https://relay.nf.video/v1"'),
                ToolResultBlock(
                    tool_use_id="toolu_1",
                    content="Firefox cache at /home/uih/.mozilla/firefox/m3d7i3w3.default-esr/datareporting/session-state.json",
                ),
            ],
        ),
    ]

    new_count = session_hook.update_rules_from_session(messages)

    assert new_count == 2
    facts = saved["facts"]["facts"]  # type: ignore[index]
    assert {fact["type"] for fact in facts} == {"ssh_host", "env_var"}
