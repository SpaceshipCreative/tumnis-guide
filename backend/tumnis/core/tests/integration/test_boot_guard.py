"""Boot checks against the deployment marker in the target database (P0-04, REL-7)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from tests._pg import OWNER, DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

TUMNIS = Path(sys.executable).with_name("tumnis")
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


def _mark(db: DbUrls, env: str) -> None:
    """Replace the database's deployment marker, as the owner role."""
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute("DELETE FROM deployment_marker")
        conn.execute("INSERT INTO deployment_marker (env) VALUES (%s)", (env,))


def _run_api(db: DbUrls, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """`tumnis api` as a real process. The boot checks must stop it before it serves, so a
    timeout (the server started) fails the test."""
    environ = {k: v for k, v in os.environ.items() if k not in SETTINGS_ENV}
    environ |= {
        "DATABASE_URL": db.app,
        "DATABASE_DIRECT_URL": db.app,
        "MASTER_KEY_FILE": str(tmp_path / "no-master-key"),
        **env,
    }
    return subprocess.run(  # noqa: S603  # our own console script
        [str(TUMNIS), "api"], env=environ, capture_output=True, text=True, timeout=60, check=False
    )


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
@pytest.mark.xfail(strict=True, reason="spec:P0-04")
def test_preview_refuses_production_database(db: DbUrls, tmp_path: Path) -> None:
    """T-P0-04-07
    Given a marker env='prod' in the target database, when `tumnis api` starts in preview
    with fakes, then it exits 78 with `preview_on_prod_database`.
    """
    _mark(db, "prod")
    result = _run_api(db, tmp_path, DEPLOYMENT_ENV="preview", TUMNIS_ADAPTERS="fake")
    assert result.returncode == 78, result.stderr
    assert "preview_on_prod_database" in result.stderr


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
@pytest.mark.xfail(strict=True, reason="spec:P0-04")
def test_env_must_match_database_marker(db: DbUrls, tmp_path: Path) -> None:
    """T-P0-04-08
    Given a marker env='preview', when `tumnis api` starts with DEPLOYMENT_ENV=dev, then it
    exits 78 with `deployment_env_mismatch`.
    """
    _mark(db, "preview")
    result = _run_api(db, tmp_path, DEPLOYMENT_ENV="dev", TUMNIS_ADAPTERS="fake")
    assert result.returncode == 78, result.stderr
    assert "deployment_env_mismatch" in result.stderr
