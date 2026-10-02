"""Pinning an Obsidian Git remote's host key (P3-12, Scott decision 82): the probe answers
a known_hosts line and its fingerprint to confirm; a pasted line is checked against the
remote's host; GitReader looks the key up under the same name."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncssh
import pytest

from tumnis.core.net import NetPolicy, ScriptedResolver, SsrfBlocked
from tumnis.modules.knowledge.adapters.obsidian.fake import FakeGitRunner
from tumnis.modules.knowledge.adapters.obsidian.git import GitReader, HostKeyChanged, InvalidRemote
from tumnis.modules.knowledge.adapters.sftp import ProbedKey, fingerprint
from tumnis.modules.knowledge.obsidian import setup

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis.core.clock import FixedClock

SELF_HOSTED = NetPolicy(mode="self-hosted")


def _host_key() -> str:
    """A fresh ed25519 public key in known_hosts form ('<type> <base64>')."""
    key = asyncssh.generate_private_key("ssh-ed25519")
    return " ".join(key.export_public_key("openssh").decode().split()[:2])


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
def test_pasted_line_pins_under_the_lookup_name() -> None:
    """T-P3-12-09
    A pasted known_hosts line for the remote's host is pinned as `<host> <type> <key>`
    (comment dropped), or `[host]:port` off port 22; the fingerprint is the key's SHA256.
    """
    key = _host_key()
    pinned = setup.pinned_known_hosts(
        "git@vault.example.com:scott/notes.git",
        f"vault.example.com,203.0.113.10 {key} comment",
        net=SELF_HOSTED,
    )
    assert pinned.known_hosts == f"vault.example.com {key}"
    assert pinned.sha256 == fingerprint(key)
    assert pinned.sha256.startswith("SHA256:")

    off_port = setup.pinned_known_hosts(
        "ssh://git@vault.example.com:2222/scott/notes.git",
        f"[vault.example.com]:2222 {key}",
        net=SELF_HOSTED,
    )
    assert off_port.known_hosts == f"[vault.example.com]:2222 {key}"


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
@pytest.mark.parametrize(
    "line",
    [
        "other.example.com {key}",
        "[vault.example.com]:2222 {key}",  # another port than the remote's
        "|1|c2FsdA==|aGFzaA== {key}",  # hashed: the host cannot be checked
        "@cert-authority vault.example.com {key}",
        "vault.example.com ssh-ed25519",
        "vault.example.com ssh-ed25519 not-a-key",
        "",
    ],
)
def test_unusable_lines_are_refused(line: str) -> None:
    """T-P3-12-09
    A line for another host or port, hashed or marked, short or with a key that does not
    parse is refused with `invalid_host_key`; nothing is pinned.
    """
    with pytest.raises(setup.InvalidHostKey) as refused:
        setup.pinned_known_hosts(
            "git@vault.example.com:scott/notes.git",
            line.format(key=_host_key()),
            net=SELF_HOSTED,
        )
    assert refused.value.code == "invalid_host_key"


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
async def test_probe_answers_the_key_to_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P3-12-09
    The probe asks the remote's host on the remote's port, through the SSRF guard, and
    answers the known_hosts line and fingerprint to show; a `file://` remote has no host
    key, and a blocked host is refused before anything is sent.
    """
    key = _host_key()
    asked: list[tuple[str, int]] = []

    async def probe(host: str, port: int, **_kw: Any) -> ProbedKey:
        asked.append((host, port))
        return ProbedKey(openssh=key, sha256=fingerprint(key))

    monkeypatch.setattr(setup, "probe_host_key", probe)
    found = await setup.probe_git_host(
        "ssh://git@vault.example.com:2222/scott/notes.git", net=SELF_HOSTED
    )
    assert asked == [("vault.example.com", 2222)]
    assert found.known_hosts == f"[vault.example.com]:2222 {key}"
    assert found.sha256 == fingerprint(key)

    with pytest.raises(InvalidRemote):
        await setup.probe_git_host("file:///srv/notes.git", net=SELF_HOSTED)

    monkeypatch.undo()
    with pytest.raises(SsrfBlocked):
        await setup.probe_git_host(
            "git@vault.example.com:scott/notes.git",
            net=NetPolicy(mode="hosted"),
            resolver=ScriptedResolver([["10.0.0.5"]]),
        )


@pytest.mark.req("FR-15.10")
@pytest.mark.wp("P3-12")
async def test_a_changed_host_key_refuses_the_sync(tmp_path: Path, clock: FixedClock) -> None:
    """T-P3-12-09
    When ssh reports a failed host key check, the refresh raises `host_key_changed` and
    nothing is accepted: the pinned line is the only one the command saw.
    """
    runner = FakeGitRunner()
    runner.script("push", returncode=128, stderr="ERROR: read only\n")
    pinned = f"vault.example.com {_host_key()}"
    reader = GitReader(
        connection_id=UUID("0190a7a0-0000-7000-8000-0000000000c3"),
        remote="git@vault.example.com:scott/notes.git",
        branch="main",
        data_dir=tmp_path,
        deploy_key=b"placeholder deploy key for tests\n",
        known_hosts=pinned,
        runner=runner,
        net=SELF_HOSTED,
        resolver=ScriptedResolver([["203.0.113.10"]] * 4),
        clock=clock,
    )
    await reader.connect()
    runner.script(
        "fetch",
        returncode=128,
        stderr="@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n"
        "Host key verification failed.\nfatal: Could not read from remote repository.\n",
    )
    with pytest.raises(HostKeyChanged) as refused:
        await reader.refresh()
    assert refused.value.code == "host_key_changed"
    assert "HostKeyAlias=vault.example.com" in runner.calls[-1].env["GIT_SSH_COMMAND"]
