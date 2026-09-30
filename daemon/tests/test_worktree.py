"""Per-run git worktrees (P2-07, SEC-8, R-26): each coding run works in its own worktree
under the state dir, which is removed after the result is kept, and at the next start
after a crash; a `path` location outside the agent home (and outside the unit's
ReadWritePaths) is refused."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import pytest

from tests.conftest import RECORDINGS, agent_config, git, make_run, worktree_paths

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis_daemon.config import DaemonConfig


def _mirrors(cfg: DaemonConfig) -> list[Path]:
    repos = cfg.state_dir / "repos"
    return sorted(repos.iterdir()) if repos.is_dir() else []


@pytest.mark.req("SEC-8")
@pytest.mark.wp("P2-07")
@pytest.mark.parametrize("kind", ["path", "repo"])
async def test_run_gets_worktree_removed_afterwards(
    kind: str,
    cfg: DaemonConfig,
    tmp_path: Path,
    tmp_git_repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P2-07-08
    A run with `workdir_policy: worktree` runs Hermes in a linked worktree at
    `<state_dir>/worktrees/<run id>` with the query file in its `.tumnis/`; once the result
    is kept, the worktree is gone and `git worktree list` in the owning repository (the
    project's own for `path`, the daemon's mirror for `repo`) shows only the main one.
    """
    from tumnis_daemon.runner import run_skill  # noqa: PLC0415
    from tumnis_daemon.state import StateStore  # noqa: PLC0415

    config = agent_config(cfg, tmp_path)
    repo, remote = tmp_git_repo
    location = (
        {"kind": "path", "path": str(repo)}
        if kind == "path"
        else {"kind": "repo", "clone_url": remote.as_uri(), "default_branch": "main"}
    )
    run = make_run(workdir_policy="worktree", code_location=location)
    note = tmp_path / "cwd.json"
    monkeypatch.setenv("HERMES_STUB_CWD_FILE", str(note))
    monkeypatch.setenv("HERMES_STUB_RECORDING", str(RECORDINGS / "enrich_ok.jsonl"))

    state = StateStore(config.state_dir)
    await run_skill(run, state, config)

    seen = json.loads(note.read_text())
    worktree = config.state_dir / "worktrees" / str(run.run_id)
    assert seen["cwd"] == str(worktree)
    assert seen["git_file"] is True  # a linked worktree, not a copy
    assert seen["query_in_tumnis"] is True
    assert not worktree.exists()

    owning = [repo] if kind == "path" else _mirrors(config)
    assert owning, "the clone URL was never mirrored"
    for main_repo in owning:
        assert worktree_paths(main_repo) == [str(main_repo)]
    results = [json.loads(f) for f in state.unacked() if json.loads(f)["type"] == "result"]
    assert [r["status"] for r in results] == ["succeeded"]


@pytest.mark.req("SEC-8")
@pytest.mark.wp("P2-07")
async def test_stale_worktrees_removed_at_start(
    cfg: DaemonConfig,
    tmp_path: Path,
    tmp_git_repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P2-07-09
    After a simulated crash (worktrees prepared, never removed, plus a half-made
    directory), start-up removes every leftover under `worktrees/` before it dials the
    server, and prunes the owning repositories: `git worktree list` is clean again.
    """
    from tumnis_daemon import main as daemon_main  # noqa: PLC0415
    from tumnis_daemon.protocol import PathLocation, RepoLocation  # noqa: PLC0415
    from tumnis_daemon.worktree import cleanup_stale_worktrees, prepare  # noqa: PLC0415

    config = agent_config(cfg, tmp_path)
    repo, remote = tmp_git_repo
    by_path, by_repo, half = (str(uuid.uuid4()) for _ in range(3))
    prepare(config, by_path, PathLocation(kind="path", path=str(repo)))
    prepare(config, by_repo, RepoLocation(kind="repo", clone_url=remote.as_uri()))
    (config.state_dir / "worktrees" / half).mkdir()
    assert len(worktree_paths(repo)) == 2

    removed = cleanup_stale_worktrees(config)
    assert sorted(removed) == sorted([by_path, by_repo, half])
    assert list((config.state_dir / "worktrees").iterdir()) == []
    for main_repo in [repo, *_mirrors(config)]:
        assert worktree_paths(main_repo) == [str(main_repo)]

    # Start-up does it before it connects.
    again = str(uuid.uuid4())
    prepare(config, again, PathLocation(kind="path", path=str(repo)))

    class DialledError(Exception):
        pass

    def connect(*args: object, **kwargs: object) -> object:
        assert not (config.state_dir / "worktrees" / again).exists()
        raise DialledError

    monkeypatch.setattr(daemon_main, "connect", connect)
    with pytest.raises(DialledError):
        await daemon_main.main(config)
    assert worktree_paths(repo) == [str(repo)]


@pytest.mark.req("SEC-8")
@pytest.mark.wp("P2-07")
def test_path_outside_agent_home_refused_unless_allowed(
    cfg: DaemonConfig, tmp_path: Path, tmp_git_repo: tuple[Path, Path]
) -> None:
    """T-P2-07-18
    A `path` location outside the agent home and not in the unit's ReadWritePaths (the
    provisioning drop-in) is refused with an error naming the path and what would allow
    it, and nothing is created; a symlink from the agent home to such a path is refused
    too. Once the drop-in lists it, the same location gets its worktree.
    """
    from tumnis_daemon.protocol import PathLocation  # noqa: PLC0415
    from tumnis_daemon.worktree import WorktreeRefused, prepare, remove  # noqa: PLC0415

    config = agent_config(cfg, tmp_path)
    home_repo, _remote = tmp_git_repo
    outside = tmp_path / "srv" / "code" / "beta"
    outside.mkdir(parents=True)
    git(outside, "init", "-q", "-b", "main")
    git(outside, "commit", "-q", "--allow-empty", "-m", "first")
    link = config.agent_home / "code" / "beta-link"
    link.symlink_to(outside)

    for refused_path in (outside, link):
        run_id = str(uuid.uuid4())
        with pytest.raises(WorktreeRefused) as refused:
            prepare(config, run_id, PathLocation(kind="path", path=str(refused_path)))
        message = str(refused.value)
        assert str(refused_path) in message
        assert "ReadWritePaths" in message
        assert not (config.state_dir / "worktrees" / run_id).exists()
    assert worktree_paths(outside) == [str(outside)]

    # Inside the agent home: allowed.
    inside = str(uuid.uuid4())
    assert prepare(config, inside, PathLocation(kind="path", path=str(home_repo))).is_dir()
    remove(config, inside)

    # Listed in the unit's ReadWritePaths drop-in: allowed.
    config.paths_dropin.write_text(
        f"[Service]\nReadWritePaths=/srv/other {tmp_path / 'srv'}\n", encoding="utf-8"
    )
    allowed = str(uuid.uuid4())
    worktree = prepare(config, allowed, PathLocation(kind="path", path=str(outside)))
    assert (worktree / ".git").is_file()
    remove(config, allowed)
    assert worktree_paths(outside) == [str(outside)]
