"""The Obsidian Git vault reader (P3-12, FR-15.10, Data flow rule 1): a read-only clone
pulled with a deploy key. The command runner is injected; `FakeGitRunner` records every
argv list and answers from a script, so nothing here runs git or opens a socket."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis.core.clock import FixedClock

CONNECTION = UUID("0190a7a0-0000-7000-8000-0000000000c1")
REMOTE = "ssh://git@vault.example.com/scott/notes.git"
KNOWN_HOSTS = "vault.example.com ssh-ed25519 AAAA-example-host-key-for-tests"
DEPLOY_KEY = b"placeholder deploy key for tests\n"
PROBE_REF = "HEAD:refs/heads/tumnis-write-probe"
READ_ONLY = "ERROR: The key you are authenticating with has been marked as read only.\n"


def _verb(argv: list[str]) -> str:
    """The git subcommand of an argv list: the first word after `git` and its `-c k=v`
    and `-C dir` options."""
    assert argv[0] == "git", argv
    rest = argv[1:]
    while rest and rest[0] in {"-c", "-C"}:
        rest = rest[2:]
    return rest[0]


def _files_holding(root: Path, data: bytes) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.read_bytes() == data]


def _reader(tmp_path: Path, runner: Any, clock: FixedClock) -> Any:
    from tumnis.core.net import NetPolicy, ScriptedResolver  # noqa: PLC0415

    _git = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.git")
    GitReader = _git.GitReader  # noqa: N806

    return GitReader(
        connection_id=CONNECTION,
        remote=REMOTE,
        branch="main",
        data_dir=tmp_path / "obsidian",
        deploy_key=DEPLOY_KEY,
        known_hosts=KNOWN_HOSTS,
        runner=runner,
        net=NetPolicy(mode="self-hosted"),
        resolver=ScriptedResolver([["203.0.113.10"]]),
        clock=clock,
    )


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
@pytest.mark.xfail(strict=True, reason="spec:P3-12")
async def test_git_reader_only_fetches_and_resets(tmp_path: Path, clock: FixedClock) -> None:
    """T-P3-12-09
    Connecting clones the branch shallowly into `<data_dir>/<connection_id>/` and probes
    with `git push --dry-run origin HEAD:refs/heads/tumnis-write-probe` (refused: the key
    is read-only); each refresh runs `git fetch --depth 1 origin <branch>` then
    `git reset --hard FETCH_HEAD`. The runner sees only clone, fetch, reset and the dry-run
    probe, never a real push. Every network command uses the deploy key through
    `GIT_SSH_COMMAND` with IdentitiesOnly, strict host key checking against the pinned
    known_hosts and batch mode, never prompts, and leaves no key file behind.
    """
    _fake = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.fake")
    FakeGitRunner = _fake.FakeGitRunner  # noqa: N806

    runner = FakeGitRunner()
    runner.script("push", returncode=128, stderr=READ_ONLY)
    reader = _reader(tmp_path, runner, clock)

    await reader.connect()
    await reader.refresh()
    await reader.refresh()

    verbs = [_verb(call.argv) for call in runner.calls]
    assert verbs == ["clone", "push", "fetch", "reset", "fetch", "reset"]
    assert set(verbs) <= {"clone", "fetch", "reset", "push"}
    for call in runner.calls:
        if _verb(call.argv) == "push":
            assert "--dry-run" in call.argv
            assert call.argv[-2:] == ["origin", PROBE_REF]

    clone = runner.calls[0]
    assert "--depth" in clone.argv
    assert clone.argv[clone.argv.index("--branch") + 1] == "main"
    assert clone.argv[-3:] == ["--", REMOTE, str(tmp_path / "obsidian" / str(CONNECTION))]
    fetch = runner.calls[2]
    assert fetch.argv[-4:] == ["--depth", "1", "origin", "main"]
    assert runner.calls[3].argv[-2:] == ["--hard", "FETCH_HEAD"]

    for call in runner.calls:
        env = call.env
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert "ext" not in env.get("GIT_ALLOW_PROTOCOL", "").split(":")
        if _verb(call.argv) == "reset":
            continue
        ssh = env["GIT_SSH_COMMAND"]
        for option in (
            "IdentitiesOnly=yes",
            "StrictHostKeyChecking=yes",
            "BatchMode=yes",
            "UserKnownHostsFile=",
        ):
            assert option in ssh, ssh
        assert " -i " in ssh
        assert call.key_seen == DEPLOY_KEY  # the key file existed while git ran
    assert _files_holding(tmp_path, DEPLOY_KEY) == []


@pytest.mark.req("Data flow 1")
@pytest.mark.wp("P3-12")
@pytest.mark.xfail(strict=True, reason="spec:P3-12")
async def test_writable_key_refused(tmp_path: Path, clock: FixedClock) -> None:
    """T-P3-12-10
    When the dry-run push succeeds, the deploy key can write: connecting raises
    `WritableDeployKey` (code `writable_deploy_key`), removes the clone, and nothing is
    ever pushed for real.
    """
    _fake = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.fake")
    FakeGitRunner = _fake.FakeGitRunner  # noqa: N806
    _git = importlib.import_module("tumnis.modules.knowledge.adapters.obsidian.git")
    WritableDeployKey = _git.WritableDeployKey  # noqa: N806

    runner = FakeGitRunner()
    runner.script("push", returncode=0, stderr="To vault.example.com:scott/notes.git\n")
    reader = _reader(tmp_path, runner, clock)

    with pytest.raises(WritableDeployKey) as refused:
        await reader.connect()
    assert refused.value.code == "writable_deploy_key"
    assert not (tmp_path / "obsidian" / str(CONNECTION)).exists()
    pushes = [call.argv for call in runner.calls if _verb(call.argv) == "push"]
    assert pushes
    assert all("--dry-run" in argv for argv in pushes)
