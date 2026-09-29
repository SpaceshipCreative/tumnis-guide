"""The daemon refuses to run as root (P1-04, FR-5.11, R-26)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis_daemon import main as daemon_main

if TYPE_CHECKING:
    from tumnis_daemon.config import DaemonConfig


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_refuses_to_run_as_root(cfg: DaemonConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P1-04-17
    With geteuid patched to 0, `main` exits 78 before it reads the token or connects.
    """
    connected: list[object] = []

    def connect(*args: object, **kwargs: object) -> object:
        connected.append((args, kwargs))
        raise AssertionError("connected as root")

    monkeypatch.setattr(daemon_main.os, "geteuid", lambda: 0)
    monkeypatch.setattr(daemon_main, "connect", connect)
    cfg.token_file.unlink()  # reading the token would raise first

    with pytest.raises(SystemExit) as exited:
        await daemon_main.main(cfg)
    assert exited.value.code == daemon_main.EX_CONFIG == 78
    assert connected == []
