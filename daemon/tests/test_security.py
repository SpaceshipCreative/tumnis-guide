"""The daemon runs unprivileged under a hardened unit (P2-07, SEC-8, R-26): it refuses to run
as root, and the systemd unit carries every hardening directive the plan requires."""

from __future__ import annotations

import configparser
from typing import TYPE_CHECKING

import pytest

from tests.conftest import REPO
from tumnis_daemon import main as daemon_main

if TYPE_CHECKING:
    from pathlib import Path

UNIT = REPO / "daemon" / "systemd" / "tumnis-daemon.service"

# Every directive the plan's unit sets, with its value (P2-07 Interfaces, systemd.exec).
REQUIRED: dict[str, str] = {
    "User": "tumnis-agent",
    "Group": "tumnis-agent",
    "ExecStart": "/opt/tumnis-daemon/bin/tumnis-daemon run --config /etc/tumnis/daemon.toml",
    "Restart": "on-failure",
    "StateDirectory": "tumnis-daemon",
    "StateDirectoryMode": "0700",
    "UMask": "0077",
    "NoNewPrivileges": "yes",
    "CapabilityBoundingSet": "",
    "AmbientCapabilities": "",
    "ProtectSystem": "strict",
    "ReadWritePaths": "/var/lib/tumnis-daemon /home/tumnis-agent",
    "PrivateTmp": "yes",
    "PrivateDevices": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes",
    "ProtectClock": "yes",
    "ProtectHostname": "yes",
    "RestrictSUIDSGID": "yes",
    "RestrictRealtime": "yes",
    "RestrictNamespaces": "yes",
    "LockPersonality": "yes",
    "SystemCallArchitectures": "native",
    "RestrictAddressFamilies": "AF_INET AF_INET6 AF_UNIX",
    "KillMode": "control-group",
}


def _service_section() -> dict[str, str]:
    parser = configparser.ConfigParser(
        interpolation=None, strict=True, delimiters=("=",), comment_prefixes=("#", ";")
    )
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # keys keep their case
    parser.read_string(UNIT.read_text(encoding="utf-8"))
    return dict(parser["Service"])


@pytest.mark.req("SEC-8")
@pytest.mark.wp("P2-07")
def test_refuses_to_run_as_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """T-P2-07-07
    With `geteuid() == 0`, `tumnis-daemon run` exits 78 (EX_CONFIG) with a message on
    stderr, before it reads its configuration or dials anywhere.
    """

    def connect(*args: object, **kwargs: object) -> object:
        raise AssertionError("connected as root")

    monkeypatch.setattr(daemon_main.os, "geteuid", lambda: 0)
    monkeypatch.setattr(daemon_main, "connect", connect)

    with pytest.raises(SystemExit) as exited:
        daemon_main.cli(["run", "--config", str(tmp_path / "no-such-daemon.toml")])
    assert exited.value.code == 78
    assert "refusing to run as root" in capsys.readouterr().err


@pytest.mark.req("SEC-8")
@pytest.mark.wp("P2-07")
def test_systemd_unit_hardening() -> None:
    """T-P2-07-10
    The unit parses; every required directive is present with its value; `User` is not
    root; `MemoryDenyWriteExecute` is absent (Claude Code and Codex need writable
    executable memory for their JIT runtimes).
    """
    service = _service_section()
    missing = {key: value for key, value in REQUIRED.items() if service.get(key) != value}
    assert missing == {}, f"directives missing or different: {missing}"
    assert service["User"] not in {"root", "0"}
    assert service["Group"] not in {"root", "0"}
    assert "MemoryDenyWriteExecute" not in service
