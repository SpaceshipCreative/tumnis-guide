"""The master key file is checked before the api or the worker starts (P0-08, SEC-6)."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REFUSED = "master key file must not be readable by group or others"
SETTINGS_ENV = (
    "DATABASE_URL",
    "DATABASE_DIRECT_URL",
    "DATABASE_OWNER_URL",
    "DBOS_SYSTEM_DATABASE_URL",
    "DEPLOYMENT_MODE",
    "DEPLOYMENT_ENV",
    "TUMNIS_ADAPTERS",
    "MASTER_KEY_FILE",
    "API_KEY_PEPPER_FILE",
    "TYPESAFE_API_KEY",
    "TUMNIS_DISABLED_MODULES",
)


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-08")
@pytest.mark.xfail(strict=True, reason="spec:P0-08")
@pytest.mark.parametrize("mode", [0o640, 0o644, 0o604, 0o600, 0o400])
def test_startup_fails_when_key_file_readable_by_group_or_others(
    mode: int, db: DbUrls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-08-10
    Given a key file with the mode under test and MASTER_KEY_FILE pointing at it, when
    create_app() and the worker entry through tumnis.cli start, then modes 0o640, 0o644
    and 0o604 raise MasterKeyError (the worker exits 78 with the message) and 0o600 and
    0o400 start.
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tests.fixtures import settings_for, write_master_key_file  # noqa: PLC0415
    from tumnis import cli  # noqa: PLC0415
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import crypto  # noqa: PLC0415
    from tumnis.core.crypto import MasterKeyError  # noqa: PLC0415

    path = write_master_key_file(
        tmp_path / "master_key.json", {1: secrets.token_bytes(32)}, active=1, mode=mode
    )
    refused = bool(mode & 0o077)
    settings = settings_for(db, master_key_file=str(path))
    try:
        if refused:
            with pytest.raises(MasterKeyError, match=REFUSED):
                create_app(settings=settings)
        else:
            app = create_app(settings=settings)
            assert app.state.master_keys.active == 1

        for name in SETTINGS_ENV:
            monkeypatch.delenv(name, raising=False)
        started: list[object] = []
        monkeypatch.setattr("tumnis.worker.main", started.append)
        result = CliRunner().invoke(
            cli.app,
            ["worker"],
            env={
                "DATABASE_URL": db.app,
                "DATABASE_DIRECT_URL": db.app,
                "DEPLOYMENT_ENV": "dev",
                "TUMNIS_ADAPTERS": "fake",
                "MASTER_KEY_FILE": str(path),
            },
        )
        if refused:
            assert result.exit_code == 78, result.output
            assert REFUSED in result.output
            assert started == []
        else:
            assert result.exit_code == 0, result.output
            assert len(started) == 1
    finally:
        crypto.reset_master_keys()
