"""Throwaway git repositories in tmp_path for the CI guard tests.

Commits use a fixed author, date and an empty global config, so a developer's signing,
hooks or default-branch settings never leak into the test repos.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

_IDENTITY = {
    "GIT_AUTHOR_NAME": "Tumnis CI",
    "GIT_AUTHOR_EMAIL": "ci@tumnis.invalid",
    "GIT_AUTHOR_DATE": "2026-03-09T12:00:00Z",
    "GIT_COMMITTER_NAME": "Tumnis CI",
    "GIT_COMMITTER_EMAIL": "ci@tumnis.invalid",
    "GIT_COMMITTER_DATE": "2026-03-09T12:00:00Z",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


@dataclass(frozen=True)
class Repo:
    path: Path
    base: str  # SHA of the first commit


def git_env() -> dict[str, str]:
    """The ambient environment without GIT_* variables (a pre-commit hook sets GIT_DIR)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(_IDENTITY)
    return env


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["git", *args],  # noqa: S607 (git from PATH, as in CI)
        cwd=repo,
        env=git_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _write(root: Path, files: Mapping[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def make_repo(tmp_path: Path, files: Mapping[str, str]) -> Repo:
    """A new repository in tmp_path/repo with `files` committed as the base."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    _write(path, files)
    git(path, "add", "-A")
    git(path, "commit", "-q", "--no-verify", "-m", "base")
    return Repo(path=path, base=git(path, "rev-parse", "HEAD"))


def commit(repo: Repo, files: Mapping[str, str], delete: Iterable[str] = ()) -> str:
    """Write `files`, remove `delete`, commit on top of the current HEAD; returns the SHA."""
    for rel in delete:
        (repo.path / rel).unlink()
    _write(repo.path, files)
    git(repo.path, "add", "-A")
    git(repo.path, "commit", "-q", "--no-verify", "--allow-empty", "-m", "head")
    return git(repo.path, "rev-parse", "HEAD")
