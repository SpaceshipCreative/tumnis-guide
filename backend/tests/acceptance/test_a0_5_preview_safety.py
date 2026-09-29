"""A0.5 · Preview safety (phase 0 acceptance, committed red by P0-05).

A preview boots on fakes against a database marked `preview` and refuses to start
(exit 78, EX_CONFIG) with real adapters, the production database, a Jev key or the
production master key. One test, one row per case: a row waiting for code carries its
own spec marker in CASES, removed as soon as that row passes. P0-04 turned every row but
`jev_key_setting` green (that one needs P0-08's workspace_settings check). The Playwright
part is frontend/e2e/acceptance/A0.5-preview-smoke.spec.ts (@smoke).
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests._pg import APP, OWNER, DbUrls
from tests.fixtures import write_master_key_file

pytestmark = [
    pytest.mark.integration,
    pytest.mark.req("REL-7"),
    pytest.mark.wp("P0-05"),
]

TUMNIS = Path(sys.executable).with_name("tumnis")
EXIT_CONFIG = 78
# Every variable the deployment settings read; each case starts from none of them.
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
)
PROD_KEY = "<fingerprint of this case's master key file>"


@dataclass(frozen=True)
class PreviewCase:
    env: dict[str, str]
    markers: tuple[tuple[str, str | None], ...] = (("preview", None),)
    jev_setting: bool = False
    refusal: str | None = None  # SettingsError code; None with boots=False: any refusal
    boots: bool = False


# A row still waiting for code carries this marker; it comes off the moment the row passes.
SPEC = pytest.mark.xfail(strict=True, reason="spec:P0-05")


def _case(case_id: str, case: PreviewCase, *marks: Any) -> Any:
    return pytest.param(case, id=case_id, marks=marks)


PREVIEW = {"DEPLOYMENT_ENV": "preview", "TUMNIS_ADAPTERS": "fake"}
CASES = [
    _case("happy_path", PreviewCase(PREVIEW, boots=True)),
    _case(
        "real_adapters",
        PreviewCase(PREVIEW | {"TUMNIS_ADAPTERS": "real"}, refusal="preview_requires_fakes"),
    ),
    _case(
        "prod_database",
        PreviewCase(PREVIEW, markers=(("prod", None),), refusal="preview_on_prod_database"),
    ),
    _case(
        "jev_key_env",
        PreviewCase(
            PREVIEW | {"TYPESAFE_API_KEY": "jev-test-key"},
            refusal="preview_has_production_secret",
        ),
    ),
    _case(
        "jev_key_setting",
        PreviewCase(PREVIEW, jev_setting=True, refusal="preview_has_production_secret"),
    ),
    _case(
        "prod_master_key",
        PreviewCase(PREVIEW, markers=(("preview", None), ("prod", PROD_KEY))),
    ),
]


def _owner(db: DbUrls) -> Any:
    import psycopg  # noqa: PLC0415

    return psycopg.connect(db.libpq(OWNER), autocommit=True)


def _fingerprint(path: Path) -> str:
    from tumnis.settings import master_key_fingerprint  # noqa: PLC0415 (P0-04)

    return str(master_key_fingerprint(str(path)))


def _add_jev_setting(db: DbUrls) -> None:
    """A workspace with a `decisions.jev` provider row in workspace_settings (P0-08)."""
    with _owner(db) as conn:
        row = conn.execute(
            "INSERT INTO workspaces (name, timezone) VALUES ('Jev', 'UTC') RETURNING id"
        ).fetchone()
        conn.execute(
            "INSERT INTO workspace_settings (workspace_id, key, value_enc, key_version) "
            "VALUES (%s, 'decisions.jev', %s, 1)",
            (row[0] if row else None, secrets.token_bytes(32)),
        )


def prepare(
    case: PreviewCase,
    db: DbUrls,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> dict[str, str]:
    """Marks the database, writes the key file, sets the case's environment through
    monkeypatch and returns it (the CLI runs as a child process with it)."""
    # A valid key file, owner-only (P0-08: create_app refuses any other mode).
    key_file = write_master_key_file(tmp_path / "master.key", {1: secrets.token_bytes(32)}, 1)
    markers = [
        (env, _fingerprint(key_file) if fingerprint == PROD_KEY else fingerprint)
        for env, fingerprint in case.markers
    ]
    with _owner(db) as conn:
        conn.execute("DELETE FROM deployment_marker")
        for env, fingerprint in markers:
            conn.execute(
                "INSERT INTO deployment_marker (env, master_key_fingerprint) VALUES (%s, %s)",
                (env, fingerprint),
            )
    if case.jev_setting:
        _add_jev_setting(db)
    for name in SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    env = {
        "DATABASE_URL": db.app,
        "DATABASE_DIRECT_URL": db.app,
        "DATABASE_OWNER_URL": db.owner,
        "MASTER_KEY_FILE": str(key_file),
        **case.env,
    }
    if case.boots:
        request.getfixturevalue("dbos")  # DBOS system tables for /health/ready
        dbos_sys_db: DbUrls = request.getfixturevalue("dbos_sys_db")
        env["DBOS_SYSTEM_DATABASE_URL"] = dbos_sys_db.url(APP)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return dict(os.environ)


def run_cli(*args: str, environ: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """`tumnis <args>` as a real process. A refused boot must exit before serving, so a
    timeout (the server started) fails the test."""
    return subprocess.run(  # noqa: S603  # our own console script
        [str(TUMNIS), *args], env=environ, capture_output=True, text=True, timeout=60, check=False
    )


async def ready_status() -> tuple[int, str]:
    """create_app() from the environment, then GET /health/ready in process."""
    import httpx  # noqa: PLC0415

    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            response = await client.get("/health/ready")
    finally:
        await core_db.dispose()  # the test database is dropped after the test
    return response.status_code, response.text


@pytest.mark.parametrize("case", CASES)
async def test_preview_boot_matrix(
    case: PreviewCase,
    db: DbUrls,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """T-P0-05-05
    Given a preview environment built per case, when the app boots, then the happy path
    starts (seed loads, /health/ready is 200) and every unsafe case exits 78 with its
    SettingsError code before serving anything.
    """
    environ = prepare(case, db, tmp_path, monkeypatch, request)

    if case.boots:
        seeded = run_cli("seed", environ=environ)
        assert seeded.returncode == 0, seeded.stderr
        status, body = await ready_status()
        assert status == 200, body
        return

    refused = run_cli("api", environ=environ)
    assert refused.returncode == EXIT_CONFIG, refused.stderr
    if case.refusal is not None:
        assert case.refusal in refused.stderr
