"""Tests for personalization fact extraction."""

from openharness.personalization.extractor import (
    extract_facts_from_text,
    extract_local_rules,
    facts_to_rules_markdown,
)
from openharness.personalization.rules import merge_facts


class TestExtractFacts:
    def test_extracts_ssh_host(self):
        text = "ssh konghm@192.168.91.212 'tail -20 /var/log/syslog'"
        facts = extract_facts_from_text(text)
        ssh_facts = [f for f in facts if f["type"] == "ssh_host"]
        assert len(ssh_facts) == 1
        assert "konghm@192.168.91.212" in ssh_facts[0]["value"]

    def test_extracts_data_path(self):
        text = "ls /ext/data_auto_stage/landing/CS_sp/1d/"
        facts = extract_facts_from_text(text)
        path_facts = [f for f in facts if f["type"] == "data_path"]
        assert any("/ext/data_auto_stage" in f["value"] for f in path_facts)

    def test_extracts_conda_env(self):
        text = "conda activate dev312"
        facts = extract_facts_from_text(text)
        conda_facts = [f for f in facts if f["type"] == "conda_env"]
        assert len(conda_facts) == 1
        assert conda_facts[0]["value"] == "dev312"

    def test_extracts_env_var(self):
        text = 'export OPENAI_BASE_URL="https://relay.nf.video/v1"'
        facts = extract_facts_from_text(text)
        env_facts = [f for f in facts if f["type"] == "env_var"]
        assert any("OPENAI_BASE_URL" in f["value"] for f in env_facts)

    def test_extracts_api_endpoint(self):
        text = "curl https://api.minimax.chat/v1/chat/completions"
        facts = extract_facts_from_text(text)
        api_facts = [f for f in facts if f["type"] == "api_endpoint"]
        assert any("minimax" in f["value"] for f in api_facts)

    def test_skips_low_value_ip_addresses(self):
        text = "ssh ops@10.0.0.8\nping 127.0.0.1\nconnect to 192.168.1.20"
        facts = extract_facts_from_text(text)
        ip_facts = [f for f in facts if f["type"] == "ip_address"]
        assert len(ip_facts) == 0

    def test_skips_low_value_python_version_mentions(self):
        text = "Python 3.11.9\npython 3.10\nuse Python 3.12 later"
        facts = extract_facts_from_text(text)
        assert [f for f in facts if f["type"] == "python_env"] == []

    def test_skips_low_value_cron_schedules(self):
        text = "0 2 * * * /usr/local/bin/nightly-sync"
        facts = extract_facts_from_text(text)
        assert [f for f in facts if f["type"] == "cron_schedule"] == []

    def test_data_path_requires_business_context(self):
        text = (
            "Use dataset root /home/uih/project/data_manual/P700_org/ as the training input.\n"
            "Firefox wrote /home/uih/.mozilla/firefox/m3d7i3w3.default-esr/datareporting/session-state.json"
        )
        facts = extract_facts_from_text(text)
        path_facts = [f for f in facts if f["type"] == "data_path"]
        assert path_facts == [
            {
                "key": "data_path:/home/uih/project/data_manual/P700_org/",
                "type": "data_path",
                "label": "Data path",
                "value": "/home/uih/project/data_manual/P700_org/",
                "confidence": 0.9,
            }
        ]

    def test_data_path_skips_noisy_runtime_artifacts(self):
        text = (
            "Load from dataset /home/uih/F/JYL/docker/dataset/qdrant/collections/demo/0/segments/abc/payload_index/010700.log\n"
            "Output at /home/uih/.cache/app/data/cache.bin"
        )
        facts = extract_facts_from_text(text)
        assert [f for f in facts if f["type"] == "data_path"] == []

    def test_deduplicates(self):
        text = "ssh user@10.0.0.1\nssh user@10.0.0.1\nssh user@10.0.0.1"
        facts = extract_facts_from_text(text)
        ssh_facts = [f for f in facts if f["type"] == "ssh_host"]
        assert len(ssh_facts) == 1


class TestMergeFacts:
    def test_merge_new_facts(self):
        existing = {"facts": [{"key": "ssh_host:a@1.1.1.1", "value": "a@1.1.1.1", "confidence": 0.7}]}
        new = [{"key": "conda_env:dev312", "value": "dev312", "confidence": 0.7}]
        merged = merge_facts(existing, new)
        assert len(merged["facts"]) == 2

    def test_merge_updates_higher_confidence(self):
        existing = {"facts": [{"key": "ssh_host:a@1.1.1.1", "value": "a@1.1.1.1", "confidence": 0.5}]}
        new = [{"key": "ssh_host:a@1.1.1.1", "value": "a@1.1.1.1", "confidence": 0.9}]
        merged = merge_facts(existing, new)
        assert len(merged["facts"]) == 1
        assert merged["facts"][0]["confidence"] == 0.9

    def test_merge_keeps_existing_if_higher(self):
        existing = {"facts": [{"key": "ssh_host:a@1.1.1.1", "value": "a@1.1.1.1", "confidence": 0.9}]}
        new = [{"key": "ssh_host:a@1.1.1.1", "value": "a@1.1.1.1", "confidence": 0.5}]
        merged = merge_facts(existing, new)
        assert merged["facts"][0]["confidence"] == 0.9


class TestFactsToMarkdown:
    def test_empty_facts(self):
        assert facts_to_rules_markdown([]) == ""

    def test_generates_sections(self):
        facts = [
            {"key": "ssh_host:a@1.1", "type": "ssh_host", "value": "a@1.1", "confidence": 0.7},
            {"key": "conda_env:dev312", "type": "conda_env", "value": "dev312", "confidence": 0.7},
        ]
        md = facts_to_rules_markdown(facts)
        assert "## SSH Hosts" in md
        assert "## Python Environments" in md
        assert "`a@1.1`" in md
        assert "`dev312`" in md


class TestExtractLocalRules:
    def test_extract_local_rules_only_uses_user_and_assistant_text(self):
        messages = [
            {"role": "user", "content": "ssh ops@10.0.0.8"},
            {
                "role": "assistant",
                "content": [{"text": 'export OPENAI_BASE_URL="https://relay.nf.video/v1"'}],
            },
            {
                "role": "user",
                "content": [{"text": "Use dataset /home/uih/project/data_manual/P700_org/ as input"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "tool_result", "text": "curl https://example.com/v1/chat"}],
            },
            {
                "role": "tool",
                "content": "ssh ignored@192.168.1.20\nconda activate ignored-env",
            },
        ]

        facts = extract_local_rules(messages)

        assert {fact["type"] for fact in facts} == {"ssh_host", "env_var", "data_path"}
