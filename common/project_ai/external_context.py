"""External context sync: git commits.

Calendar and mail integrations require external API credentials and are
deferred to a later phase.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class GitCommit:
    hash: str
    author: str
    date: str
    subject: str

    def to_dict(self) -> dict:
        return {
            "hash": self.hash,
            "author": self.author,
            "date": self.date,
            "subject": self.subject,
        }


def fetch_git_commits(
    repo_path: str,
    *,
    since_days: int = 7,
    max_count: int = 20,
    branch: str = "",
) -> list[GitCommit]:
    """Run `git log` on a local repo and return recent commits.

    Returns an empty list if the repo path is invalid or git is not available.
    """
    cmd = [
        "git", "-C", repo_path, "log",
        f"--since={since_days} days ago",
        f"--max-count={max_count}",
        "--format=%H%n%an%n%aI%n%s",
    ]
    if branch:
        cmd.append(branch)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    lines = result.stdout.strip().split("\n")
    commits: list[GitCommit] = []
    i = 0
    while i + 3 < len(lines):
        commits.append(GitCommit(
            hash=lines[i][:12],
            author=lines[i + 1],
            date=lines[i + 2][:10],
            subject=lines[i + 3],
        ))
        i += 4
    return commits


def filter_commits_by_project(
    commits: list[GitCommit],
    project_title: str,
    aliases: list[str] | None = None,
) -> list[GitCommit]:
    """Filter commits whose subject mentions the project title or aliases."""
    keywords = [project_title.lower()]
    if aliases:
        keywords.extend(a.lower() for a in aliases)

    matched: list[GitCommit] = []
    for c in commits:
        subject_lower = c.subject.lower()
        if any(kw in subject_lower for kw in keywords if kw):
            matched.append(c)
    return matched
