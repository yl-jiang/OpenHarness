"""Tests for common/project_ai/external_context.py — git commit sync."""
from __future__ import annotations

import subprocess

from common.project_ai.external_context import (
    GitCommit,
    fetch_git_commits,
    filter_commits_by_project,
)


class TestFetchGitCommits:
    def test_fetch_commits_invalid_repo(self) -> None:
        result = fetch_git_commits("/nonexistent/path/to/repo")
        assert result == []

    def test_fetch_commits_real_repo(self, tmp_path) -> None:
        repo = tmp_path / "testrepo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo, check=True, capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "MyProject: initial setup"],
            cwd=repo, check=True, capture_output=True,
        )

        commits = fetch_git_commits(str(repo))
        assert len(commits) >= 1
        assert commits[0].subject == "MyProject: initial setup"
        assert commits[0].author == "Test"
        assert commits[0].hash  # non-empty


class TestFilterCommitsByProject:
    def _make_commits(self) -> list[GitCommit]:
        return [
            GitCommit(hash="aaa111", author="Alice", date="2026-06-10",
                      subject="MyProject: add login flow"),
            GitCommit(hash="bbb222", author="Bob", date="2026-06-11",
                      subject="fix typo in README"),
            GitCommit(hash="ccc333", author="Carol", date="2026-06-12",
                      subject="refactor utils module"),
        ]

    def test_filter_by_title(self) -> None:
        commits = self._make_commits()
        result = filter_commits_by_project(commits, "MyProject")
        assert len(result) == 1
        assert result[0].hash == "aaa111"

    def test_filter_by_alias(self) -> None:
        commits = [
            GitCommit(hash="aaa111", author="Alice", date="2026-06-10",
                      subject="fix typo in README"),
            GitCommit(hash="bbb222", author="Bob", date="2026-06-11",
                      subject="MP-42: add search feature"),
            GitCommit(hash="ccc333", author="Carol", date="2026-06-12",
                      subject="refactor utils module"),
        ]
        result = filter_commits_by_project(commits, "MyProject", aliases=["MP-42"])
        assert len(result) == 1
        assert result[0].hash == "bbb222"

    def test_filter_no_match(self) -> None:
        commits = self._make_commits()
        result = filter_commits_by_project(commits, "UnrelatedProject")
        assert result == []
