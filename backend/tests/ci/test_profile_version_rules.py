"""profile_version_check beyond the spec test: new and deleted profiles, and a VERSION
that changes without going up (P1-05)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.ci._gitrepo import Repo, commit, make_repo
from tests.ci._scripts import load

if TYPE_CHECKING:
    from pathlib import Path

BASE = {
    "profiles/master/VERSION": "1.2.0\n",
    "profiles/master/SOUL.md": "Master.\n",
    "profiles/shared/jev-mcp/server.py": "x = 1\n",
}


def _check(repo: Repo, head: str) -> int:
    code: int = load("profile_version_check").main(
        ["--base", repo.base, "--head", head, "--repo", str(repo.path)]
    )
    return code


def test_a_new_profile_needs_no_bump(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, BASE)
    head = commit(repo, {"profiles/scout/VERSION": "1.0.0\n", "profiles/scout/SOUL.md": "S\n"})
    assert _check(repo, head) == 0


def test_a_directory_without_version_is_not_a_profile(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, BASE)
    assert _check(repo, commit(repo, {"profiles/shared/jev-mcp/server.py": "x = 2\n"})) == 0


def test_deleting_a_profile_passes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, BASE)
    head = commit(repo, {}, delete=["profiles/master/VERSION", "profiles/master/SOUL.md"])
    assert _check(repo, head) == 0


def test_the_version_must_go_up(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, BASE)
    down = commit(repo, {"profiles/master/VERSION": "1.1.9\n", "profiles/master/SOUL.md": "M\n"})
    assert _check(repo, down) == 1
    (tmp_path / "two").mkdir()
    repo2 = make_repo(tmp_path / "two", BASE)
    junk = commit(repo2, {"profiles/master/VERSION": "next\n"})
    assert _check(repo2, junk) == 1
