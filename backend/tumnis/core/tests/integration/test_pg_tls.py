"""Postgres refuses unencrypted connections (P0-16, SEC-9).

Runs against `pg_tls_container`: the P0-02 image with the repository's own
postgresql.conf, pg_hba.conf and initdb scripts, and a certificate made at test time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._tls import pg_tls_container  # noqa: F401  # the fixture

if TYPE_CHECKING:
    from tests._tls import TlsPostgres

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("SEC-9")
@pytest.mark.wp("P0-16")
@pytest.mark.xfail(strict=True, reason="spec:P0-16")
def test_non_tls_connection_is_refused(pg_tls_container: TlsPostgres) -> None:  # noqa: F811
    """T-P0-16-14
    With the repository's pg_hba.conf and postgresql.conf, `sslmode=disable` fails with a
    "no encryption" rejection for the app and owner roles; `sslmode=require` connects over
    TLS 1.2 or later, and verify-full succeeds against the server's certificate.
    """
    pg = pg_tls_container
    for role in ("tumnis_app", "tumnis_owner"):
        with pytest.raises(psycopg.OperationalError) as refused:
            psycopg.connect(pg.conninfo(role, sslmode="disable"))
        assert "no encryption" in str(refused.value)

    with psycopg.connect(pg.conninfo(sslmode="require")) as conn:
        ssl, version = conn.execute(
            "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
        ).fetchone() or (None, None)
        assert ssl is True
        assert version in {"TLSv1.2", "TLSv1.3"}
        assert conn.execute("SHOW ssl_min_protocol_version").fetchone() == ("TLSv1.2",)

    verified = pg.conninfo(sslmode="verify-full", sslrootcert=str(pg.root_cert))
    with psycopg.connect(verified.replace(f"host={pg.host}", "host=localhost")) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
