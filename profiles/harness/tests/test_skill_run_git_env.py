"""The case working directory's git ignores the caller's repository variables
(FIX-followups-2, from CodeRabbit's review of #140).

`prepare_workdir` makes the case's working directory a fresh repository with `git init`,
`git add -A` and `git commit`. Git reads `GIT_DIR`, `GIT_WORK_TREE` and `GIT_INDEX_FILE`
from the environment before the working directory (a git hook or a worktree-scoped shell
exports them), so with them inherited it would init, stage and commit in the caller's
repository and leave the case's directory without one."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from harness.tests._cases import write_case

pytestmark = [pytest.mark.wp("P2-12"), pytest.mark.req("FR-5.3", "FR-5.6")]

REPOSITORY_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in REPOSITORY_VARIABLES}


def test_workdir_git_ignores_inherited_repository_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE pointing at another directory, the
    case's working directory still becomes its own repository with the starting commit,
    and nothing is written where the variables point."""
    from harness.cases import load_case
    from harness.skill_run import prepare_workdir

    case = load_case(
        write_case(
            tmp_path,
            output_schema={"tool": "post_result"},
            mock={"repo": "calc"},
            expect={"suite": {"last": "green"}},
        )
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("GIT_DIR", str(elsewhere / "repo.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(elsewhere))
    monkeypatch.setenv("GIT_INDEX_FILE", str(elsewhere / "index"))

    work = prepare_workdir(case, tmp_path / "run", tmp_path / "calls.jsonl")

    assert work is not None
    assert (work / ".git").is_dir()
    log = subprocess.run(
        ["git", "log", "--format=%s"],  # noqa: S607  # git from PATH, as in CI
        cwd=work,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.splitlines() == ["calc as the case starts"]
    assert list(elsewhere.iterdir()) == []
