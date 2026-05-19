from pathlib import Path
import hashlib

import pytest

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.engine.stream_events import AssistantTurnComplete
from openharness.tools.base import ToolExecutionContext
from openharness.tools.skill_manager_tool import SkillManagerToolInput

from wolo.config import save_config
from wolo.models import WoloConfig
from wolo.runner import WoloQueryRunner
from wolo.session import save_conversation
from wolo.store import WoloStore
from wolo.workspace import get_sessions_dir, get_skills_dir, initialize_workspace

def test_wolo_workspace_and_config_are_independent(tmp_path: Path, monkeypatch):
    from wolo.models import WoloConfig
    from wolo.store import WoloStore
    from wolo.workspace import (
        get_config_path,
        get_data_dir,
        get_soul_path,
        get_workspace_root,
        initialize_workspace,
        workspace_health,
    )

    workspace = tmp_path / ".wolo"
    monkeypatch.setenv("WOLO_WORKSPACE", str(workspace))

    root = initialize_workspace()
    store = WoloStore()

    assert root == workspace.resolve()
    assert get_workspace_root() == workspace.resolve()
    assert get_config_path() == workspace.resolve() / "config.json"
    assert get_data_dir() == workspace.resolve() / "data"
    assert workspace_health()["config"] is True
    assert workspace_health()["attachments_dir"] is True
    assert workspace_health()["skills_dir"] is True
    assert store.root == workspace.resolve() / "data"
    assert WoloConfig().provider_profile == "deepseek"
    assert "work log assistant" in get_soul_path().read_text(encoding="utf-8")
    assert get_skills_dir() == workspace.resolve() / "skills"


def test_wolo_command_prefix_help_and_work_actions():
    from wolo.commands import extract_wolo_content, parse_wolo_command, wolo_help_text

    assert extract_wolo_content("/wolo record fixed the flaky gateway test") == (
        "fixed the flaky gateway test"
    )

    report = parse_wolo_command("/wolo report monthly")
    assert report is not None
    assert report.action == "report"
    assert report.report_type == "monthly"

    default = parse_wolo_command("记录今天完成 PR review", default_record=True)
    assert default is not None
    assert default.action == "record"

    help_text = wolo_help_text()
    assert "/wolo process" in help_text
    assert "工作记录" in help_text


def test_wolo_tool_names_and_descriptions_are_work_focused(tmp_path: Path):
    from wolo.store import WoloStore
    from wolo.tools import WoloToolRegistry

    registry = WoloToolRegistry(WoloStore(tmp_path / ".wolo"))
    schemas = registry.tool_schemas()
    names = {schema["name"] for schema in schemas}

    assert "wolo_record" in names
    assert "wolo_report" in names
    assert all(not name.startswith("solo_") for name in names)

    record_schema = next(schema for schema in schemas if schema["name"] == "wolo_record")
    description = record_schema["description"]
    fields = record_schema["parameters"]["properties"]
    assert "work" in description.lower()
    assert "project" in fields["tags"]["description"].lower()
    assert "prompt" in fields["tags"]["description"].lower()
    assert "tool" in fields["tags"]["description"].lower()


def test_wolo_prompts_are_optimized_for_work_logs():
    from wolo.agent import _PROCESS_RECORD_SYSTEM_PROMPT, _report_system_prompt
    from wolo.runner import _WOLO_TOOL_ROUTER_PROMPT

    prompt_text = "\n".join(
        [
            _WOLO_TOOL_ROUTER_PROMPT,
            _PROCESS_RECORD_SYSTEM_PROMPT,
            _report_system_prompt("weekly"),
        ]
    )

    for expected in ("工作", "项目", "会议", "prompt", "tool", "blocker"):
        assert expected in prompt_text


def test_wolo_readme_documents_standalone_usage():
    readme = Path("wolo/README.md")

    assert readme.exists()
    content = readme.read_text(encoding="utf-8")
    assert "# wolo" in content
    assert "~/.wolo" in content
    assert "uv run wolo --help" in content
    assert "/wolo report weekly" in content
    assert "prompt" in content
    assert "tool" in content


def test_standalone_wolo_gateway_logging_writes_workspace_log_file(tmp_path: Path):
    import logging as stdlib_logging

    from openharness.utils.log import get_logger, reset_logging
    from wolo.cli import _configure_gateway_logging
    from wolo.workspace import get_logs_dir

    workspace = tmp_path / ".wolo"
    save_config(WoloConfig(log_level="INFO"), workspace)

    reset_logging()
    try:
        _configure_gateway_logging(workspace)
        get_logger("wolo.gateway.bridge").info("workspace log test", project="openharness")
        stdlib_logging.shutdown()
        log_path = get_logs_dir(workspace) / "gateway.log"
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "workspace log test" in content
    finally:
        reset_logging()
        stdlib_logging.getLogger().handlers.clear()


@pytest.mark.asyncio
async def test_wolo_query_runner_passes_settings_and_autodream_context(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
    skill_dir = get_skills_dir(workspace) / "release-retro"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: release-retro\ndescription: Summarize release outcomes and follow-ups.\n---\n\n# Release Retro\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages: list[ConversationMessage] = []
            self.tool_metadata = kwargs["tool_metadata"]

        def set_system_prompt(self, prompt: str):
            captured["refreshed_system_prompt"] = prompt

        def load_messages(self, messages):
            self.messages = list(messages)

        async def submit_message(self, prompt):
            self.messages.append(
                prompt if isinstance(prompt, ConversationMessage) else ConversationMessage.from_user_text(prompt)
            )
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="已记录工作")]),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("wolo.runner.QueryEngine", FakeQueryEngine)
    runner = WoloQueryRunner(store, api_client=object())

    result = await runner.run("今天修了一个 flaky test", session_key="feishu:chat-1")

    assert result == "已记录工作"
    assert captured["settings"] is not None
    assert "release-retro" in captured["system_prompt"]
    assert "Summarize release outcomes and follow-ups." in captured["system_prompt"]
    tool_metadata = captured["tool_metadata"]
    assert tool_metadata["extra_skill_dirs"] == (str(workspace / "skills"),)
    assert tool_metadata["user_skills_dir"] == str(workspace / "skills")
    assert tool_metadata["skill_registry_cwd"] is None
    assert callable(tool_metadata["system_prompt_refresher"])
    assert tool_metadata["autodream_context"] == {
        "memory_dir": str(workspace / "memory"),
        "session_dir": str(workspace / "sessions"),
        "app_label": "wolo work memory",
        "runner_module": "ohmo",
    }


@pytest.mark.asyncio
async def test_wolo_skill_manager_writes_workspace_local_skills(tmp_path: Path):
    from wolo.tools import WoloToolRegistry, build_oh_registry

    workspace = initialize_workspace(tmp_path / ".wolo")
    registry = build_oh_registry(WoloToolRegistry(WoloStore(workspace)))
    skill_tool = registry.get("skill_manager")
    assert skill_tool is not None

    context = ToolExecutionContext(
        cwd=tmp_path,
        metadata={
            "extra_skill_dirs": (str(workspace / "skills"),),
            "user_skills_dir": str(workspace / "skills"),
            "skill_registry_cwd": None,
        },
    )
    content = "---\nname: standup-brief\ndescription: Capture blockers and next actions.\n---\n\n# Standup Brief\nSummarize progress.\n"

    result = await skill_tool.execute(
        SkillManagerToolInput(action="write", name="standup-brief", content=content),
        context,
    )

    assert result.is_error is False
    skill_path = workspace / "skills" / "standup-brief" / "SKILL.md"
    assert skill_path.exists()
    assert skill_path.read_text(encoding="utf-8") == content


def test_wolo_save_conversation_writes_session_snapshot_for_autodream(tmp_path: Path):
    workspace = initialize_workspace(tmp_path / ".wolo")
    session_key = "feishu:chat-1"
    session_id = "wolo-session-1"

    save_conversation(
        workspace,
        session_key,
        [ConversationMessage.from_user_text("hello wolo")],
        session_id=session_id,
    )

    token = hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:12]
    latest_path = get_sessions_dir(workspace) / f"latest-{token}.json"
    session_path = get_sessions_dir(workspace) / f"session-{session_id}.json"

    assert latest_path.exists()
    assert session_path.exists()
    assert '"session_id": "wolo-session-1"' in session_path.read_text(encoding="utf-8")
