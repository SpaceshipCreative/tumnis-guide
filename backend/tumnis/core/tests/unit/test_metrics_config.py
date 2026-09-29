"""The /metrics bearer token is required in production (P0-27, FR-12.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

URLS: dict[str, Any] = {
    "database_url": "postgresql+psycopg://tumnis_app:x@db.invalid:5432/tumnis",
    "database_direct_url": "postgresql+psycopg://tumnis_app:x@db.invalid:5432/tumnis",
}


@pytest.mark.req("FR-12.3")
@pytest.mark.wp("P0-27")
@pytest.mark.xfail(strict=True, reason="spec:P0-27")
def test_prod_requires_metrics_token_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """T-P0-27-11
    In prod, a missing METRICS_TOKEN_FILE refuses startup (`metrics_token_file_required`),
    and so does a file that is missing or empty (`metrics_token_unreadable`); `tumnis api`
    exits 78 before it connects to anything. A readable file yields its token, stripped;
    dev without a file has no token (and /metrics answers 401 to everyone).
    """
    from typer.testing import CliRunner  # noqa: PLC0415

    from tumnis import cli  # noqa: PLC0415
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.settings import EXIT_CONFIG, Settings, SettingsError  # noqa: PLC0415

    monkeypatch.delenv("METRICS_TOKEN_FILE", raising=False)
    prod: dict[str, Any] = {**URLS, "deployment_env": "prod"}

    with pytest.raises(SettingsError) as missing:
        create_app(Settings(**prod))
    assert missing.value.code == "metrics_token_file_required"

    empty = tmp_path / "empty_token"
    empty.write_text("\n")
    for path in (tmp_path / "absent_token", empty):
        with pytest.raises(SettingsError) as unreadable:
            Settings(**prod, metrics_token_file=str(path)).metrics_token()
        assert unreadable.value.code == "metrics_token_unreadable"

    token_file = tmp_path / "metrics_token"
    token_file.write_text("scrape-me\n")
    assert Settings(**prod, metrics_token_file=str(token_file)).metrics_token() == "scrape-me"
    assert Settings(**URLS, deployment_env="dev").metrics_token() is None

    env = {
        "DATABASE_URL": URLS["database_url"],
        "DATABASE_DIRECT_URL": URLS["database_direct_url"],
        "DEPLOYMENT_ENV": "prod",
    }
    result = CliRunner().invoke(cli.app, ["api"], env=env)
    assert result.exit_code == EXIT_CONFIG, result.output
    assert "metrics_token_file_required" in result.output
