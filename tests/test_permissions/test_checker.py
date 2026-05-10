"""Tests for permission decisions."""

import logging

import pytest

import openharness.permissions.checker as checker_module
from openharness.config.settings import PathRuleConfig, PermissionSettings
from openharness.permissions import PermissionChecker, PermissionMode
from openharness.permissions.checker import SENSITIVE_PATH_PATTERNS


def test_default_mode_allows_read_only():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate("read_file", is_read_only=True)
    assert decision.allowed is True
    assert decision.requires_confirmation is False


def test_default_mode_requires_confirmation_for_mutation():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate("write_file", is_read_only=False)
    assert decision.allowed is False
    assert decision.requires_confirmation is True
    assert "/permissions full_auto" in decision.reason


def test_permission_checker_caches_repeated_decisions(monkeypatch):
    calls = 0
    original_permission_name = checker_module._permission_name

    def _counting_permission_name(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_permission_name(*args, **kwargs)

    monkeypatch.setattr(checker_module, "_permission_name", _counting_permission_name)
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))

    first = checker.evaluate("read_file", is_read_only=True, file_path="/tmp/demo.txt")
    second = checker.evaluate("read_file", is_read_only=True, file_path="/tmp/demo.txt")

    assert first == second
    assert calls == 1


def test_permission_checker_invalidates_cache_after_remember_allow():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    first = checker.evaluate("edit_file", is_read_only=False, file_path="/tmp/demo.py")
    cached = checker.evaluate("edit_file", is_read_only=False, file_path="/tmp/demo.py")
    assert cached.requires_confirmation is True

    checker.remember_allow(first)
    second = checker.evaluate("edit_file", is_read_only=False, file_path="/tmp/demo.py")

    assert second.allowed is True
    assert second.requires_confirmation is False
    assert "remembered" in second.reason


def test_default_mode_gives_package_install_hint_for_bash():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate(
        "bash",
        is_read_only=False,
        command="npm init -y && npm install next react react-dom",
    )
    assert decision.allowed is False
    assert decision.requires_confirmation is True
    assert "Package installation and scaffolding commands change the workspace" in decision.reason


def test_remembered_bash_allow_pattern_skips_repeated_prompt():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    first = checker.evaluate(
        "bash",
        is_read_only=False,
        command="git remote -v",
    )
    assert first.allowed is False
    assert first.requires_confirmation is True
    assert first.permission == "bash"
    assert first.patterns == ("git remote -v",)
    assert first.always_patterns == ("git remote *",)

    checker.remember_allow(first)

    second = checker.evaluate(
        "bash",
        is_read_only=False,
        command="git remote show origin",
    )
    assert second.allowed is True
    assert second.requires_confirmation is False
    assert "remembered" in second.reason


@pytest.mark.parametrize(
    "command",
    [
        "git --no-pager diff --stat",
        "git --no-pager diff HEAD -- src/openharness/services/autodream/ --stat",
        "git --no-pager diff HEAD -- src/openharness/services/autodream/service.py | head -100",
        "GIT_PAGER=cat git diff --name-only",
        "git branch --show-current",
    ],
)
def test_default_mode_auto_allows_safe_git_forms(command):
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate(
        "bash",
        is_read_only=False,
        command=command,
    )
    assert decision.allowed is True
    assert decision.requires_confirmation is False
    assert "safe bash command" in decision.reason


def test_default_mode_does_not_auto_allow_mutating_git_branch_command():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate("bash", is_read_only=False, command="git branch demo")

    assert decision.allowed is False
    assert decision.requires_confirmation is True


@pytest.mark.parametrize(
    ("command", "expected_pattern"),
    [
        ("rg TODO src", "rg *"),
        ("stat README.md", "stat *"),
        ("git ls-files src", "git ls-files *"),
    ],
)
def test_default_mode_auto_allows_additional_read_only_commands(command, expected_pattern):
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate("bash", is_read_only=False, command=command)

    assert decision.allowed is True
    assert decision.requires_confirmation is False
    assert decision.always_patterns == (expected_pattern,)
    assert "safe bash command" in decision.reason


def test_default_mode_does_not_auto_allow_safe_command_with_shell_redirection():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate("bash", is_read_only=False, command="rg TODO src > matches.txt")

    assert decision.allowed is False
    assert decision.requires_confirmation is True


def test_default_mode_does_not_auto_allow_shell_or_pipeline_mutation():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate("bash", is_read_only=False, command="git diff | tee diff.txt")

    assert decision.allowed is False
    assert decision.requires_confirmation is True


def test_remembered_edit_allow_is_limited_to_same_file(tmp_path):
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    target = tmp_path / "demo.py"
    other = tmp_path / "other.py"
    first = checker.evaluate(
        "edit_file",
        is_read_only=False,
        file_path=str(target),
    )
    assert first.requires_confirmation is True
    assert first.permission == "edit"
    assert first.always_patterns == (str(target),)

    checker.remember_allow(first)

    same_file = checker.evaluate("edit_file", is_read_only=False, file_path=str(target))
    other_file = checker.evaluate("edit_file", is_read_only=False, file_path=str(other))
    assert same_file.allowed is True
    assert other_file.allowed is False
    assert other_file.requires_confirmation is True


def test_remembered_allow_does_not_bypass_denied_commands():
    checker = PermissionChecker(
        PermissionSettings(
            mode=PermissionMode.DEFAULT,
            denied_commands=["git push *"],
        )
    )
    decision = checker.evaluate("bash", is_read_only=False, command="git status --short")
    checker.remember_allow(decision)

    blocked = checker.evaluate("bash", is_read_only=False, command="git push origin main")

    assert blocked.allowed is False
    assert blocked.requires_confirmation is False
    assert "Command matches deny pattern" in blocked.reason


def test_plan_mode_blocks_mutating_tools():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.PLAN))
    decision = checker.evaluate("bash", is_read_only=False)
    assert decision.allowed is False
    assert "plan mode" in decision.reason


def test_full_auto_allows_mutating_tools():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
    decision = checker.evaluate("bash", is_read_only=False)
    assert decision.allowed is True


# --- path_rules parsing tests ---


def _settings_with_rules(*rules) -> PermissionSettings:
    """Build a PermissionSettings with the given path_rule objects bypassing validation."""
    return PermissionSettings.model_construct(
        mode=PermissionMode.FULL_AUTO,
        allowed_tools=[],
        denied_tools=[],
        denied_commands=[],
        path_rules=list(rules),
    )


@pytest.mark.parametrize(
    "bad_rule",
    [
        PathRuleConfig.model_construct(allow=False),                  # pattern attribute missing
        PathRuleConfig.model_construct(pattern="", allow=False),      # pattern empty string
        PathRuleConfig.model_construct(pattern="   ", allow=False),   # pattern whitespace-only
        PathRuleConfig.model_construct(pattern=42, allow=False),      # pattern non-string
        PathRuleConfig.model_construct(pattern=None, allow=False),    # pattern None
    ],
    ids=["missing", "empty", "whitespace-only", "non-string", "none"],
)
def test_invalid_pattern_rule_is_skipped_and_warns(bad_rule, caplog):
    """Rules with missing, empty, or non-string patterns are skipped with a warning."""
    settings = _settings_with_rules(bad_rule)
    with caplog.at_level(logging.WARNING, logger="openharness.permissions.checker"):
        checker = PermissionChecker(settings)

    assert checker._path_rules == []
    assert "Skipping path rule" in caplog.text


def test_valid_deny_rule_blocks_matching_path():
    """A valid deny rule prevents access to a matching file path."""
    rule = PathRuleConfig(pattern="/etc/*", allow=False)
    settings = _settings_with_rules(rule)
    checker = PermissionChecker(settings)

    decision = checker.evaluate("read_file", is_read_only=True, file_path="/etc/passwd")
    assert decision.allowed is False
    assert "/etc/passwd" in decision.reason


def test_valid_deny_rule_does_not_block_non_matching_path():
    """A deny rule does not affect paths that don't match the pattern."""
    rule = PathRuleConfig(pattern="/etc/*", allow=False)
    settings = _settings_with_rules(rule)
    checker = PermissionChecker(settings)

    decision = checker.evaluate("read_file", is_read_only=True, file_path="/home/user/file.txt")
    assert decision.allowed is True


def test_valid_allow_rule_is_added():
    """A rule with allow=True is accepted and stored without warnings."""
    rule = PathRuleConfig(pattern="/data/*", allow=True)
    settings = _settings_with_rules(rule)
    checker = PermissionChecker(settings)

    assert len(checker._path_rules) == 1
    assert checker._path_rules[0].pattern == "/data/*"
    assert checker._path_rules[0].allow is True


def test_valid_allow_rule_short_circuits_read_only_with_specific_reason():
    rule = PathRuleConfig(pattern="/data/*", allow=True)
    settings = PermissionSettings(
        mode=PermissionMode.DEFAULT,
        path_rules=[rule],
    )
    checker = PermissionChecker(settings)

    decision = checker.evaluate("read_file", is_read_only=True, file_path="/data/guide.md")

    assert decision.allowed is True
    assert decision.requires_confirmation is False
    assert "matches allow rule" in decision.reason


def test_pattern_with_surrounding_whitespace_is_stripped():
    """A pattern with leading/trailing whitespace is accepted with whitespace stripped."""
    rule = PathRuleConfig.model_construct(pattern="  /etc/*  ", allow=False)
    settings = _settings_with_rules(rule)
    checker = PermissionChecker(settings)

    assert len(checker._path_rules) == 1
    assert checker._path_rules[0].pattern == "/etc/*"

    decision = checker.evaluate("read_file", is_read_only=True, file_path="/etc/passwd")
    assert decision.allowed is False


# --- built-in sensitive path protection tests ---


class TestSensitivePathProtection:
    """Built-in sensitive path patterns must block access in every permission mode."""

    @pytest.mark.parametrize(
        "mode",
        [PermissionMode.FULL_AUTO, PermissionMode.DEFAULT, PermissionMode.PLAN],
        ids=["full_auto", "default", "plan"],
    )
    def test_ssh_key_blocked_in_all_modes(self, mode):
        checker = PermissionChecker(PermissionSettings(mode=mode))
        decision = checker.evaluate(
            "read_file", is_read_only=True, file_path="/home/user/.ssh/id_rsa"
        )
        assert decision.allowed is False
        assert ".ssh" in decision.reason

    def test_full_auto_blocks_sensitive_paths(self):
        """FULL_AUTO normally allows everything, but sensitive paths are still denied."""
        checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
        for path in (
            "/home/user/.ssh/id_ed25519",
            "/home/user/.aws/credentials",
            "/home/user/.config/gcloud/application_default_credentials.json",
            "/home/user/.gnupg/private-keys-v1.d/key.key",
            "/home/user/.azure/accessTokens.json",
            "/home/user/.docker/config.json",
            "/home/user/.kube/config",
            "/home/user/.openharness/credentials.json",
            "/home/user/.openharness/copilot_auth.json",
        ):
            decision = checker.evaluate("read_file", is_read_only=True, file_path=path)
            assert decision.allowed is False, f"Expected {path} to be denied"

    def test_sensitive_path_blocks_write_tools(self):
        """Sensitive path protection applies to write operations too."""
        checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
        decision = checker.evaluate(
            "write_file", is_read_only=False, file_path="/home/user/.ssh/authorized_keys"
        )
        assert decision.allowed is False

    def test_allowed_tools_does_not_bypass_sensitive_paths(self):
        """Even if read_file is explicitly allowed, sensitive paths are still denied."""
        checker = PermissionChecker(
            PermissionSettings(
                mode=PermissionMode.FULL_AUTO,
                allowed_tools=["read_file"],
            )
        )
        decision = checker.evaluate(
            "read_file", is_read_only=True, file_path="/home/user/.ssh/id_rsa"
        )
        assert decision.allowed is False

    def test_non_sensitive_paths_unaffected(self):
        """Normal project files are not blocked by sensitive path protection."""
        checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
        for path in (
            "/home/user/project/src/main.py",
            "/home/user/.bashrc",
            "/home/user/.config/nvim/init.lua",
            "/tmp/scratch.txt",
        ):
            decision = checker.evaluate("read_file", is_read_only=True, file_path=path)
            assert decision.allowed is True, f"Expected {path} to be allowed"

    def test_no_file_path_skips_sensitive_check(self):
        """Tools without a file path (e.g. bash) are not affected."""
        checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
        decision = checker.evaluate("bash", is_read_only=False, command="echo hello")
        assert decision.allowed is True

    @pytest.mark.parametrize(
        "pattern",
        SENSITIVE_PATH_PATTERNS,
        ids=[p.split("/")[-1] or p.split("/")[-2] for p in SENSITIVE_PATH_PATTERNS],
    )
    def test_every_builtin_pattern_has_coverage(self, pattern):
        """Verify every pattern in SENSITIVE_PATH_PATTERNS actually blocks something."""
        # Build a concrete path that should match the pattern
        example_paths = {
            "*/.ssh/*": "/home/u/.ssh/id_rsa",
            "*/.aws/credentials": "/home/u/.aws/credentials",
            "*/.aws/config": "/home/u/.aws/config",
            "*/.config/gcloud/*": "/home/u/.config/gcloud/creds.json",
            "*/.azure/*": "/home/u/.azure/tokens.json",
            "*/.gnupg/*": "/home/u/.gnupg/secring.gpg",
            "*/.docker/config.json": "/home/u/.docker/config.json",
            "*/.kube/config": "/home/u/.kube/config",
            "*/.openharness/credentials.json": "/home/u/.openharness/credentials.json",
            "*/.openharness/copilot_auth.json": "/home/u/.openharness/copilot_auth.json",
        }
        test_path = example_paths[pattern]
        checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
        decision = checker.evaluate("read_file", is_read_only=True, file_path=test_path)
        assert decision.allowed is False, f"Pattern {pattern!r} did not block {test_path}"
