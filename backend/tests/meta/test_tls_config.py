"""Every database hop in the compose stack uses TLS that verifies the server (P0-16, SEC-9).

The Postgres side is proven live by T-P0-16-14 (test_pg_tls.py); this checks the wiring
around it: PgBouncer's two hops, the URLs the app containers get and the CA they verify
against.
"""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
APP_SERVICES = ("migrate", "api", "worker")
CA = "/etc/tumnis/tls/ca.crt"


def _url_params(url: str) -> dict[str, str]:
    return {key: values[-1] for key, values in parse_qs(urlsplit(url).query).items()}


@pytest.mark.req("SEC-9")
@pytest.mark.wp("P0-16")
def test_database_hops_use_verified_tls() -> None:
    """PgBouncer requires TLS from clients and verifies Postgres (verify-full against the
    database CA); the app containers' database URLs all say verify-full with the CA they
    mount; pg_hba.conf has no plain `host` line, only `hostssl` and a `hostnossl` reject."""
    ini = configparser.ConfigParser()
    ini.read(DEPLOY / "pgbouncer" / "pgbouncer.ini")
    bouncer = ini["pgbouncer"]
    assert bouncer["client_tls_sslmode"].strip() == "require"
    assert bouncer["client_tls_cert_file"].strip()
    assert bouncer["client_tls_key_file"].strip()
    assert bouncer["server_tls_sslmode"].strip() == "verify-full"
    assert bouncer["server_tls_ca_file"].strip() == CA

    compose: dict[str, Any] = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    services = compose["services"]
    for name in APP_SERVICES:
        service = services[name]
        mounts = [str(volume) for volume in service.get("volumes") or []]
        assert any(m.startswith("db_tls_ca:/etc/tumnis/tls") for m in mounts), (name, mounts)
        for key, url in service["environment"].items():
            if key.startswith("DATABASE") and key.endswith("URL"):
                params = _url_params(str(url))
                assert params.get("sslmode") == "verify-full", (name, key)
                assert params.get("sslrootcert") == CA, (name, key)
    assert services["postgres"]["depends_on"]["db-tls"]["condition"] == (
        "service_completed_successfully"
    )

    hba = [
        line.split()
        for line in (DEPLOY / "postgres" / "pg_hba.conf").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert all(fields[0] in {"local", "hostssl", "hostnossl"} for fields in hba), hba
    assert ["hostnossl", "all", "all", "all", "reject"] in hba
