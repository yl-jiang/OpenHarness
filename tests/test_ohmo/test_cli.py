import json
from pathlib import Path

from typer.testing import CliRunner

from ohmo.cli import app


def test_ohmo_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "personal-agent app" in result.output
    assert "config" in result.output


def test_ohmo_init_and_doctor(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    result = runner.invoke(app, ["init", "--workspace", str(workspace), "--no-interactive"])
    assert result.exit_code == 0
    assert str(workspace) in result.output

    doctor = runner.invoke(app, ["doctor", "--cwd", str(tmp_path), "--workspace", str(workspace)])
    assert doctor.exit_code == 0
    assert "ohmo doctor:" in doctor.output
    assert "workspace: ok" in doctor.output


def test_ohmo_init_existing_workspace_points_to_config(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    first = runner.invoke(app, ["init", "--workspace", str(workspace), "--no-interactive"])
    assert first.exit_code == 0

    second = runner.invoke(app, ["init", "--workspace", str(workspace), "--no-interactive"])
    assert second.exit_code == 0
    assert "ohmo workspace already exists." in second.output
    assert "Use `ohmo config`" in second.output


def test_ohmo_self_log_records_workspace_local_entry(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"

    init = runner.invoke(app, ["self-log", "init", "--workspace", str(workspace)])
    assert init.exit_code == 0
    assert "Initialized ohmo self-log" in init.output

    record = runner.invoke(
        app,
        ["self-log", "record", "今天把 self-log 接进 ohmo 了", "--workspace", str(workspace)],
    )
    assert record.exit_code == 0
    assert "Recorded self-log entry" in record.output

    listing = runner.invoke(app, ["self-log", "list", "--workspace", str(workspace)])
    assert listing.exit_code == 0
    assert "今天把 self-log 接进 ohmo 了" in listing.output

    status = runner.invoke(app, ["self-log", "status", "--workspace", str(workspace)])
    assert status.exit_code == 0
    assert "entries: 1" in status.output


def test_ohmo_self_log_process_and_report_use_agent(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"

    class FakeAgent:
        async def process_record(self, raw_content, profile_context):
            return {
                "corrected_content": raw_content,
                "summary": "完成智能处理",
                "tags": "工作",
                "emotion": "积极",
                "emotion_reason": "有进展",
                "related_people": "",
                "related_places": "",
                "needs_clarification": False,
                "clarification_reason": "",
                "clarification_questions": [],
                "suggested_profile_updates": [],
            }

        async def generate_report(self, report_type, records, profile_context):
            return "## 本周概览\n- agent 已接入"

    monkeypatch.setattr("ohmo.cli.OpenHarnessSelfLogAgent", lambda profile=None: FakeAgent())

    runner.invoke(app, ["self-log", "record", "今天补齐 self-log agent", "--workspace", str(workspace)])
    process = runner.invoke(app, ["self-log", "process", "--workspace", str(workspace)])
    assert process.exit_code == 0
    assert "processed: 1" in process.output

    view = runner.invoke(app, ["self-log", "view", "--workspace", str(workspace)])
    assert view.exit_code == 0
    assert "完成智能处理" in view.output

    report = runner.invoke(app, ["self-log", "report", "weekly", "--workspace", str(workspace)])
    assert report.exit_code == 0
    assert "agent 已接入" in report.output


def test_ohmo_self_log_process_supports_backfill_prompt_and_content(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"

    class FakeAgent:
        async def process_record(self, raw_content, profile_context):
            return {
                "corrected_content": raw_content,
                "summary": "完成补录",
                "tags": "补录",
                "emotion": "中性",
                "emotion_reason": "",
                "related_people": "",
                "related_places": "",
                "needs_clarification": False,
                "clarification_reason": "",
                "clarification_questions": [],
                "suggested_profile_updates": [],
            }

    monkeypatch.setattr("ohmo.cli.OpenHarnessSelfLogAgent", lambda profile=None: FakeAgent())

    prompt = runner.invoke(app, ["self-log", "process", "2026-05-16", "--workspace", str(workspace)])
    backfill = runner.invoke(
        app,
        [
            "self-log",
            "process",
            "2026-05-16",
            "--backfill",
            "昨天补录了进展",
            "--workspace",
            str(workspace),
        ],
    )
    view = runner.invoke(app, ["self-log", "view", "--workspace", str(workspace)])

    assert prompt.exit_code == 0
    assert "2026-05-15" in prompt.output
    assert backfill.exit_code == 0
    assert "backfilled: 2026-05-15" in backfill.output
    assert "2026-05-15" in view.output
    assert "[补录]" in view.output


def test_ohmo_self_log_exposes_full_app_command_surface():
    runner = CliRunner()
    result = runner.invoke(app, ["self-log", "--help"])
    assert result.exit_code == 0
    for command in ("init", "listen", "stop", "status", "record", "process", "view", "report", "config"):
        assert command in result.output


def test_ohmo_self_log_listen_starts_gateway_in_default_record_mode(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    calls = []

    def fake_start_gateway_process(cwd, workspace, *, self_log_default_record=False):
        calls.append((cwd, workspace, self_log_default_record))
        return 4321

    monkeypatch.setattr("ohmo.cli.start_gateway_process", fake_start_gateway_process)

    result = runner.invoke(app, ["self-log", "listen", "--cwd", str(tmp_path), "--workspace", str(workspace)])

    assert result.exit_code == 0
    assert calls == [(str(tmp_path), str(workspace), True)]
    assert "ohmo self-log is listening" in result.output


def test_ohmo_init_interactive_writes_gateway_config(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    user_input = "\n".join(
        [
            "1",  # provider profile
            "y",  # enable telegram
            "*",  # allow_from
            "telegram-token",
            "y",  # reply_to_message
            "n",  # slack
            "n",  # discord
            "n",  # feishu
            "y",  # send_progress
            "y",  # send_tool_hints
            "n",  # allow_remote_admin_commands
        ]
    )
    result = runner.invoke(app, ["init", "--workspace", str(workspace)], input=user_input)
    assert result.exit_code == 0
    config = json.loads((workspace / "gateway.json").read_text(encoding="utf-8"))
    assert config["enabled_channels"] == ["telegram"]
    assert config["channel_configs"]["telegram"]["token"] == "telegram-token"


def test_ohmo_init_interactive_writes_feishu_gateway_config(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    user_input = "\n".join(
        [
            "1",         # provider profile
            "n",         # telegram
            "n",         # slack
            "n",         # discord
            "y",         # feishu
            "*",         # allow_from
            "cli_app",   # app_id
            "cli_secret",# app_secret
            "enc_key",   # encrypt_key
            "verify_me", # verification_token
            "OK",        # react_emoji
            "1",         # group_policy -> managed_or_mention
            "",          # bot_open_id
            "ohmo,openclaw", # bot_names
            "y",         # send_progress
            "n",         # send_tool_hints
            "n",         # allow_remote_admin_commands
        ]
    )
    result = runner.invoke(app, ["init", "--workspace", str(workspace)], input=user_input)
    assert result.exit_code == 0
    config = json.loads((workspace / "gateway.json").read_text(encoding="utf-8"))
    assert config["enabled_channels"] == ["feishu"]
    assert config["channel_configs"]["feishu"]["app_id"] == "cli_app"
    assert config["channel_configs"]["feishu"]["app_secret"] == "cli_secret"
    assert config["channel_configs"]["feishu"]["encrypt_key"] == "enc_key"
    assert config["channel_configs"]["feishu"]["verification_token"] == "verify_me"
    assert config["channel_configs"]["feishu"]["react_emoji"] == "OK"
    assert config["channel_configs"]["feishu"]["group_policy"] == "managed_or_mention"
    assert config["channel_configs"]["feishu"]["bot_names"] == "ohmo,openclaw"


def test_ohmo_config_interactive_can_restart_gateway(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--no-interactive"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("ohmo.cli.gateway_status", lambda cwd, workspace: type("State", (), {"running": True})())
    monkeypatch.setattr("ohmo.cli.stop_gateway_process", lambda cwd, workspace: True)
    monkeypatch.setattr("ohmo.cli.start_gateway_process", lambda cwd, workspace: 4321)
    user_input = "\n".join(
        [
            "4",          # provider profile -> codex
            "n",          # telegram
            "n",          # slack
            "n",          # discord
            "y",          # feishu
            "*",          # allow_from
            "cli_app",    # app_id
            "cli_secret", # app_secret
            "",           # encrypt_key
            "verify_me",  # verification_token
            "OK",         # react_emoji
            "1",          # group_policy -> managed_or_mention
            "",           # bot_open_id
            "ohmo,openclaw", # bot_names
            "y",          # send_progress
            "y",          # send_tool_hints
            "n",          # allow_remote_admin_commands
            "y",          # restart gateway
        ]
    )
    result = runner.invoke(app, ["config", "--workspace", str(workspace)], input=user_input)
    assert result.exit_code == 0
    assert "ohmo gateway restarted (pid=4321)" in result.output
    config = json.loads((workspace / "gateway.json").read_text(encoding="utf-8"))
    assert config["provider_profile"] == "codex"
    assert config["enabled_channels"] == ["feishu"]


def test_ohmo_config_keeps_existing_channel_when_not_reconfigured(tmp_path: Path, monkeypatch):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--no-interactive"])
    gateway_path = workspace / "gateway.json"
    config = json.loads(gateway_path.read_text(encoding="utf-8"))
    config["enabled_channels"] = ["feishu"]
    config["channel_configs"]["feishu"] = {
        "allow_from": ["*"],
        "app_id": "old_app",
        "app_secret": "old_secret",
        "encrypt_key": "",
        "verification_token": "old_verify",
        "react_emoji": "OK",
    }
    gateway_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("ohmo.cli.gateway_status", lambda cwd, workspace: type("State", (), {"running": False})())
    user_input = "\n".join(
        [
            "4",  # provider profile -> codex
            "n",  # telegram
            "n",  # slack
            "n",  # discord
            "n",  # reconfigure feishu? keep existing
            "y",  # send_progress
            "y",  # send_tool_hints
            "n",  # allow_remote_admin_commands
        ]
    )
    result = runner.invoke(app, ["config", "--workspace", str(workspace)], input=user_input)
    assert result.exit_code == 0
    updated = json.loads(gateway_path.read_text(encoding="utf-8"))
    assert updated["enabled_channels"] == ["feishu"]
    assert updated["channel_configs"]["feishu"]["app_id"] == "old_app"
    assert updated["channel_configs"]["feishu"]["app_secret"] == "old_secret"
