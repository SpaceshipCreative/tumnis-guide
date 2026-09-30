"""Shared daemon test helpers: a config in a temporary state dir, Run messages, a git
repository with a bare remote (`tmp_git_repo`), and the in-memory transport pair the replay
property test talks through (`MemoryLink` to a `ServerDouble`)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, settings
from websockets.exceptions import ConnectionClosed

from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import Run, SchemaRef, envelope

# The random-disconnect property test runs 500 examples in CI (P2-07 done checklist) and
# fewer locally, so `make check` stays under its budget.
settings.register_profile(
    "ci", max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.register_profile(
    "dev", max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile("ci" if os.environ.get("CI") else "dev")

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
STUB_HERMES = TESTS / "stubs" / "hermes"
RECORDINGS = TESTS / "recordings" / "stream_json"


@pytest.fixture
def cfg(tmp_path: Path) -> DaemonConfig:
    token = tmp_path / "runner.token"
    token.write_text("tmd_abcdefgh_SECRET\n")
    STUB_HERMES.chmod(STUB_HERMES.stat().st_mode | stat.S_IXUSR)
    return DaemonConfig(
        server_url="wss://tumnis.example.org",
        runner_name="homelab-hermes",
        token_file=token,
        state_dir=tmp_path / "state",
        hermes_bin=str(STUB_HERMES),
        profiles=("acme-site",),
    )


def make_run(
    *,
    profile: str = "acme-site",
    skill: str = "enrich",
    prompt: str = "Use the skill.\n",
    timeout_s: int = 60,
    workdir_policy: str = "none",
    code_location: dict[str, Any] | None = None,
) -> Run:
    """A Run as the server would send it; built without validation so a test can hand the
    runner names the protocol itself would refuse."""
    run_id = uuid.uuid4()
    fields: dict[str, Any] = {
        **envelope(f"run:{run_id}"),
        "schema_version": 1,
        "type": "run",
        "run_id": run_id,
        "profile": profile,
        "skill": skill,
        "packet": {
            "prompt_text": prompt,
            "skill": skill,
            "run_id": str(run_id),
            "body": {"project": {"code_location": code_location}},
        },
        "output_schema": SchemaRef(family="enrichment", name="result", version=1),
        "timeout_s": timeout_s,
        "workdir_policy": workdir_policy,
    }
    return Run.model_construct(**fields)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- P2-07: agent home, git repositories, the in-memory transport ------------------------

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Tumnis Test",
    "GIT_AUTHOR_EMAIL": "test@example.org",
    "GIT_COMMITTER_NAME": "Tumnis Test",
    "GIT_COMMITTER_EMAIL": "test@example.org",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(cwd: Path | None, *args: str) -> str:
    """Run git for a test (no shell); its stdout."""
    env = {**os.environ, **GIT_ENV, "HOME": str(cwd or Path.cwd())}
    argv = ["git", *args]  # git from PATH, as the daemon runs it
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return done.stdout


def agent_config(cfg: DaemonConfig, tmp_path: Path) -> DaemonConfig:
    """The test config with the agent home and the unit's ReadWritePaths drop-in under
    `tmp_path` (called in a test body: the fields arrive with P2-07)."""
    return replace(cfg, agent_home=tmp_path / "home", paths_dropin=tmp_path / "paths.conf")


def worktree_paths(repo: Path) -> list[str]:
    """`git worktree list --porcelain` of a repository: the path of each worktree."""
    listing = git(repo, "worktree", "list", "--porcelain")
    return [line.split(" ", 1)[1] for line in listing.splitlines() if line.startswith("worktree ")]


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> tuple[Path, Path]:
    """(repo, remote): a repository with one commit on `main` under the test agent home
    (`tmp_path/home/code/acme`), and a bare copy of it as the clone URL's remote."""
    repo = tmp_path / "home" / "code" / "acme"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# Acme site\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "first")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(remote))
    return repo, remote


class ServerDouble:
    """The server's side of the replay property: every frame it receives is counted, a
    frame is stored once per `message_id` (the dedupe rule), and each receipt is acked
    after it is stored; `take_acks` hands over the ids acked since the last call."""

    def __init__(self) -> None:
        self.stored: dict[str, dict[str, Any]] = {}
        self.receipts: Counter[str] = Counter()
        self._acks: list[str] = []

    def receive(self, frame: str) -> None:
        message = json.loads(frame)
        message_id = str(message["message_id"])
        self.receipts[message_id] += 1
        self.stored.setdefault(message_id, message)
        self._acks.append(message_id)

    def take_acks(self) -> list[str]:
        acks, self._acks = self._acks, []
        return acks


class MemoryLink:
    """The daemon's end of one in-memory connection to a ServerDouble: `send` delivers the
    frame at once; after `close` it raises ConnectionClosed, as a dropped socket does."""

    def __init__(self, server: ServerDouble) -> None:
        self.server = server
        self.closed = False

    async def send(self, message: str, /) -> None:
        if self.closed:
            raise ConnectionClosed(None, None)
        self.server.receive(message)

    def close(self) -> None:
        self.closed = True


# --- P2-18: a profile home to archive -----------------------------------------------------


@dataclass(frozen=True)
class ProfileHome:
    """`cfg` with the Hermes profiles under `tmp_path`, and the profile `name` living at
    `home` (remembered by the daemon, so its register lists it)."""

    cfg: DaemonConfig
    name: str
    home: Path


@pytest.fixture
def tmp_profile_home(cfg: DaemonConfig, tmp_path: Path) -> ProfileHome:
    """The profile `acme-site` with nested files, an empty file, an executable, a private
    `.env` (0600) and a symlink inside the tree (archived as a link, never followed)."""
    profiles = tmp_path / "hermes" / "profiles"
    home = profiles / "acme-site"
    (home / "memories").mkdir(parents=True)
    (home / "sessions" / "2026").mkdir(parents=True)
    (home / "skills" / "tumnis").mkdir(parents=True)
    (home / "config.yaml").write_text("model: local\n" * 40)
    (home / "SOUL.md").write_text("# Acme site agent\n\nWrite plainly.\n" * 50)
    (home / ".env").write_text("TUMNIS_MCP_URL=https://tumnis.example.org/mcp\n")
    (home / ".env").chmod(0o600)
    (home / "memories" / "notes.md").write_text("The client prefers short emails.\n" * 200)
    (home / "sessions" / "2026" / "01.jsonl").write_text('{"role": "user", "text": "hi"}\n' * 300)
    (home / "skills" / "tumnis" / "empty.txt").write_bytes(b"")
    (home / "skills" / "tumnis" / "run.sh").write_text("#!/bin/sh\necho ok\n")
    (home / "skills" / "tumnis" / "run.sh").chmod(0o755)
    (home / "latest-session").symlink_to("sessions/2026/01.jsonl")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "profiles.json").write_text(json.dumps(["acme-site"]))
    # `cfg` also names the profile in daemon.toml: archiving must drop it from the live
    # list all the same.
    daemon_cfg = replace(cfg, hermes_profiles_dir=profiles, hermes_home=profiles.parent)
    return ProfileHome(cfg=daemon_cfg, name="acme-site", home=home)
