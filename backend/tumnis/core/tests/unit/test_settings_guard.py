"""Preview safety in the deployment settings (P0-04, REL-7)."""

from __future__ import annotations

import pytest

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
