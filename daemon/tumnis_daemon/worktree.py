"""Per-run git worktrees (P2-07, SEC-8, R-26; https://git-scm.com/docs/git-worktree).

A run with `workdir_policy: worktree` works in a linked worktree at
`<state_dir>/worktrees/<run id>`, detached at the project's HEAD (a `path` location) or at
the default branch of a mirror clone the daemon keeps at `<state_dir>/repos/<sha256 of the
clone URL>.git` (a `repo` location). The worktree is removed once the run's result is in
the outbox, whatever the outcome, and every leftover is removed at start-up (runners hold
no task state, so anything under `worktrees/` then is stale).

A `path` must resolve (symlinks first) under the agent home or under a `ReadWritePaths`
entry of the unit's provisioning drop-in; anything else is refused before git runs, since
the hardened unit would not let the daemon write there anyway. Git runs without a shell,
with a fixed environment, and never with an argument that could read as an option.
"""

import configparser
import contextlib
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final
from uuid import UUID

from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import CodeLocation, PathLocation

GIT_TIMEOUT_S: Final = 600.0  # a mirror clone of a large repository can take a while
TUMNIS_DIR: Final = ".tumnis"  # the query file's folder in the worktree (git-ignored)
_REF: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_ENV_KEEP: Final = frozenset({"PATH", "HOME", "LANG"})


class WorktreeRefused(Exception):  # noqa: N818  # the plan's word
    """The run's code location cannot get a worktree; the message says why."""


def _git(cwd: Path | None, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP}
    env["GIT_TERMINAL_PROMPT"] = "0"
    done = subprocess.run(  # noqa: S603  # fixed argv, no shell
        ["git", "-c", "protocol.ext.allow=never", *args],  # noqa: S607  # git from PATH
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT_S,
    )
    return done.stdout


def worktrees_dir(cfg: DaemonConfig) -> Path:
    return cfg.state_dir / "worktrees"


def mirrors_dir(cfg: DaemonConfig) -> Path:
    return cfg.state_dir / "repos"


def _worktree(cfg: DaemonConfig, run_id: str) -> Path:
    return worktrees_dir(cfg) / str(UUID(run_id))  # a run id, never a path


def allowed_roots(cfg: DaemonConfig) -> list[Path]:
    """The agent home and every `ReadWritePaths` entry of the provisioning drop-in."""
    roots = [cfg.agent_home.resolve()]
    try:
        text = cfg.paths_dropin.read_text(encoding="utf-8")
    except OSError:
        return roots
    parser = configparser.ConfigParser(
        interpolation=None, strict=False, delimiters=("=",), comment_prefixes=("#", ";")
    )
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # keys keep case
    try:
        parser.read_string(text)
    except configparser.Error:
        return roots
    if parser.has_option("Service", "ReadWritePaths"):
        for entry in parser.get("Service", "ReadWritePaths").split():
            path = Path(entry.lstrip("-+"))
            if path.is_absolute():
                roots.append(path.resolve())
    return roots


def check_path(cfg: DaemonConfig, path: str) -> Path:
    """The repository directory, resolved; WorktreeRefused when it lies outside the agent
    home and the unit's ReadWritePaths."""
    given = Path(path)
    resolved = given.resolve() if given.is_absolute() else None
    if resolved is None or not any(resolved.is_relative_to(r) for r in allowed_roots(cfg)):
        shown = path if resolved is None or str(resolved) == path else f"{path} ({resolved})"
        raise WorktreeRefused(
            f"the code path {shown} is outside {cfg.agent_home} and not in the unit's "
            f"ReadWritePaths; provisioning adds it through {cfg.paths_dropin}"
        )
    if not resolved.is_dir():
        raise WorktreeRefused(f"the code path {path} is not a directory")
    return resolved


def mirror_path(cfg: DaemonConfig, clone_url: str) -> Path:
    return mirrors_dir(cfg) / (hashlib.sha256(clone_url.encode()).hexdigest() + ".git")


def _exclude_tumnis(worktree: Path) -> None:
    """`.tumnis/` goes into the repository's `info/exclude`, so the query file is never
    committed."""
    common = Path(_git(worktree, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = worktree / common
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    line = f"/{TUMNIS_DIR}/"
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if line not in current.splitlines():
        with exclude.open("a", encoding="utf-8") as f:
            f.write(("" if not current or current.endswith("\n") else "\n") + line + "\n")


def prepare(cfg: DaemonConfig, run_id: str, loc: CodeLocation) -> Path:
    """The run's worktree, made: detached at HEAD of the `path` repository, or at the
    default branch (else HEAD) of the mirror of the `repo` clone URL."""
    worktree = _worktree(cfg, run_id)
    worktrees_dir(cfg).mkdir(mode=0o700, parents=True, exist_ok=True)
    if isinstance(loc, PathLocation):
        repo = check_path(cfg, loc.path)
        _git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
    else:
        if loc.clone_url.startswith("-"):
            raise WorktreeRefused("the clone URL is not a URL")
        branch = loc.default_branch
        if branch is not None and not _REF.fullmatch(branch):
            raise WorktreeRefused("the default branch is not a branch name")
        mirror = mirror_path(cfg, loc.clone_url)
        mirrors_dir(cfg).mkdir(mode=0o700, parents=True, exist_ok=True)
        if not mirror.exists():
            _git(None, "clone", "--quiet", "--mirror", "--", loc.clone_url, str(mirror))
        else:
            _git(mirror, "fetch", "--quiet", "--prune", "origin")
        _git(mirror, "worktree", "add", "--detach", str(worktree), branch or "HEAD")
    _exclude_tumnis(worktree)
    (worktree / TUMNIS_DIR).mkdir(mode=0o700, exist_ok=True)
    return worktree


def owning_repo(worktree: Path) -> Path | None:
    """The main repository's git directory, from the worktree's `.git` file
    (`gitdir: <repo git dir>/worktrees/<name>`); None when there is none."""
    try:
        text = (worktree / ".git").read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("gitdir: "):
        return None
    admin = Path(text.removeprefix("gitdir: ").strip())
    if not admin.is_absolute():
        admin = worktree / admin
    return admin.parent.parent


def remove(cfg: DaemonConfig, run_id: str) -> Path | None:
    """Remove the run's worktree and its registration; returns the owning repository's git
    directory (None when there was none)."""
    worktree = worktrees_dir(cfg) / run_id
    if not worktree.exists() and not worktree.is_symlink():
        return None
    repo = owning_repo(worktree)
    if repo is not None:
        with contextlib.suppress(subprocess.SubprocessError, OSError):
            _git(None, f"--git-dir={repo}", "worktree", "remove", "--force", str(worktree))
    if worktree.is_symlink() or worktree.is_file():
        worktree.unlink()
    elif worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    if repo is not None:
        with contextlib.suppress(subprocess.SubprocessError, OSError):
            _git(None, f"--git-dir={repo}", "worktree", "prune")
    return repo


def cleanup_stale_worktrees(cfg: DaemonConfig) -> list[str]:
    """At start: every entry under `worktrees/` is stale (runners hold no task state), so
    remove each one, then prune the registered repositories (the ones they came from and
    every mirror). Returns the removed run ids for the log."""
    folder = worktrees_dir(cfg)
    removed: list[str] = []
    repos: set[Path] = set()
    if folder.is_dir():
        for entry in sorted(folder.iterdir()):
            repo = remove(cfg, entry.name)
            if repo is not None:
                repos.add(repo)
            removed.append(entry.name)
    if mirrors_dir(cfg).is_dir():
        repos.update(p for p in mirrors_dir(cfg).iterdir() if p.is_dir())
    for repo in sorted(repos):
        with contextlib.suppress(subprocess.SubprocessError, OSError):
            _git(None, f"--git-dir={repo}", "worktree", "prune")
    return removed
