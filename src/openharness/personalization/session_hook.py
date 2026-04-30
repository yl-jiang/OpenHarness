"""Session-end hook to extract and persist local environment rules."""

from __future__ import annotations

from openharness.utils.log import get_logger

from openharness.engine.messages import ConversationMessage
from openharness.personalization.extractor import (
    extract_local_rules,
    facts_to_rules_markdown,
)
from openharness.personalization.rules import (
    load_facts,
    merge_facts,
    save_facts,
    save_local_rules,
)

logger = get_logger(__name__)


def update_rules_from_session(messages: list[ConversationMessage]) -> int:
    """Extract local facts from session messages and update rules.

    Called at session end. Returns the number of new facts extracted.

    Args:
        messages: The conversation messages from the session.

    Returns:
        Number of new facts found and persisted.
    """
    extracted_messages: list[dict[str, object]] = []
    for msg in messages:
        content: list[dict[str, str]] = []
        for block in msg.content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                content.append({"type": "text", "text": text})
        if content:
            extracted_messages.append({"role": msg.role, "content": content})

    if not extracted_messages:
        return 0

    new_facts = extract_local_rules(extracted_messages)
    if not new_facts:
        return 0

    # Merge with existing
    existing = load_facts()
    merged = merge_facts(existing, new_facts)
    save_facts(merged)

    # Regenerate rules markdown
    rules_md = facts_to_rules_markdown(merged["facts"])
    if rules_md:
        save_local_rules(rules_md)

    new_count = len(merged["facts"]) - len(existing.get("facts", []))
    logger.info(
        "Personalization: %d new facts extracted (%d total)",
        max(new_count, 0),
        len(merged["facts"]),
    )
    return max(new_count, 0)
