from pathlib import Path


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
    assert store.root == workspace.resolve() / "data"
    assert WoloConfig().provider_profile == "deepseek"
    assert "work log assistant" in get_soul_path().read_text(encoding="utf-8")


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
