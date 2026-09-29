"""Preview safety in the deployment settings (P0-04, REL-7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

DSN = "postgresql+psycopg://tumnis_app:pw@127.0.0.1:5432/tumnis"
# Every variable Settings reads; each test starts from none of them.
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


def _environment(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("DATABASE_DIRECT_URL", DSN)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _cli_exit_code(command: str) -> int:
    from typer.testing import CliRunner  # noqa: PLC0415

    from tumnis.cli import app  # noqa: PLC0415

    return CliRunner().invoke(app, [command]).exit_code


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
def test_preview_refuses_real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P0-04-05
    DEPLOYMENT_ENV=preview with TUMNIS_ADAPTERS=real raises SettingsError
    `preview_requires_fakes`, and `tumnis api` and `tumnis worker` exit 78.
    """
    from tumnis.settings import EXIT_CONFIG, Settings, SettingsError  # noqa: PLC0415

    _environment(monkeypatch, DEPLOYMENT_ENV="preview", TUMNIS_ADAPTERS="real")
    with pytest.raises(SettingsError) as raised:
        Settings()  # values come from the environment
    assert raised.value.code == "preview_requires_fakes"
    assert EXIT_CONFIG == 78
    assert _cli_exit_code("api") == 78
    assert _cli_exit_code("worker") == 78


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
def test_preview_refuses_jev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P0-04-06
    DEPLOYMENT_ENV=preview with fakes but TYPESAFE_API_KEY set raises SettingsError
    `preview_has_production_secret`, and `tumnis api` exits 78.
    """
    from tumnis.settings import Settings, SettingsError  # noqa: PLC0415

    _environment(
        monkeypatch, DEPLOYMENT_ENV="preview", TUMNIS_ADAPTERS="fake", TYPESAFE_API_KEY="ts-live"
    )
    with pytest.raises(SettingsError) as raised:
        Settings()  # values come from the environment
    assert raised.value.code == "preview_has_production_secret"
    assert _cli_exit_code("api") == 78


TLS_BASE = "postgresql+psycopg://tumnis_app:pw@db.example:5432/tumnis"
VERIFY_FULL = f"{TLS_BASE}?sslmode=verify-full&sslrootcert=/etc/tumnis/tls/ca.crt"
DATABASE_URL_FIELDS = (
    "database_url",
    "database_direct_url",
    "database_owner_url",
    "dbos_system_database_url",
)


@pytest.mark.req("SEC-9")
@pytest.mark.wp("P0-16")
@pytest.mark.xfail(strict=True, reason="spec:P0-16")
def test_prod_requires_verify_full_dsn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """T-P0-16-15
    DEPLOYMENT_ENV=prod with `sslmode=require` (or disable, verify-ca, or none) on any
    database URL raises SettingsError `database_tls_required`, and `tumnis api`, `worker`
    and `migrate` exit 78 with it before connecting; verify-full passes, and dev and
    preview accept `require`.
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tumnis.cli import app  # noqa: PLC0415
    from tumnis.settings import Settings, SettingsError  # noqa: PLC0415

    _environment(monkeypatch)
    weak_urls = (
        TLS_BASE,
        f"{TLS_BASE}?sslmode=require",
        f"{TLS_BASE}?sslmode=disable",
        f"{TLS_BASE}?sslmode=verify-ca&sslrootcert=/etc/tumnis/tls/ca.crt",
    )
    for weak in weak_urls:
        for field in DATABASE_URL_FIELDS:
            values: dict[str, Any] = {
                "database_url": VERIFY_FULL,
                "database_direct_url": VERIFY_FULL,
                "deployment_env": "prod",
                field: weak,
            }
            with pytest.raises(SettingsError) as raised:
                Settings(**values).check_database_tls()
            assert raised.value.code == "database_tls_required", (field, weak)

    Settings(
        database_url=VERIFY_FULL,
        database_direct_url=VERIFY_FULL,
        database_owner_url=VERIFY_FULL,
        deployment_env="prod",
    ).check_database_tls()
    required = f"{TLS_BASE}?sslmode=require"
    for deployment_env in ("dev", "preview"):
        Settings(
            database_url=required,
            database_direct_url=required,
            deployment_env=deployment_env,
            tumnis_adapters="fake",
        ).check_database_tls()

    token = tmp_path / "metrics_token"
    token.write_text("scrape-me\n")
    env = {
        "DATABASE_URL": required,
        "DATABASE_DIRECT_URL": required,
        "DATABASE_OWNER_URL": required,
        "DEPLOYMENT_ENV": "prod",
        "METRICS_TOKEN_FILE": str(token),
    }
    for command in ("api", "worker", "migrate"):
        result = CliRunner().invoke(app, [command], env=env)
        assert result.exit_code == 78, (command, result.output)
        assert "database_tls_required" in result.output, (command, result.output)
