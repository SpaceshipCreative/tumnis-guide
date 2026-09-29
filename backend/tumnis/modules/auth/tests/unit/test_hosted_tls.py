"""Hosted mode refuses to start without TLS (P0-13, Hosted readiness)."""

from __future__ import annotations

import pytest

DSN = "postgresql+psycopg://tumnis_app:x@db.invalid:5432/tumnis"


@pytest.mark.req("Hosted readiness", "SEC-1")
@pytest.mark.wp("P0-13")
def test_hosted_mode_needs_an_https_public_base_url() -> None:
    """T-P0-13-28
    `require_hosted_tls` refuses DEPLOYMENT_MODE=hosted with no PUBLIC_BASE_URL or an
    http:// one (`hosted_requires_https`); an https:// one, and self-hosted mode with none,
    pass.
    """
    from tumnis.settings import Settings, SettingsError, require_hosted_tls  # noqa: PLC0415

    for base in (None, "http://tumnis.example"):
        settings = Settings(
            database_url=DSN,
            database_direct_url=DSN,
            deployment_mode="hosted",
            public_base_url=base,
        )
        with pytest.raises(SettingsError) as refused:
            require_hosted_tls(settings)
        assert refused.value.code == "hosted_requires_https"
    require_hosted_tls(
        Settings(
            database_url=DSN,
            database_direct_url=DSN,
            deployment_mode="hosted",
            public_base_url="https://tumnis.example",
        )
    )
    require_hosted_tls(Settings(database_url=DSN, database_direct_url=DSN))
