"""TLS helpers for the integration tests (P0-16, SEC-9).

- `self_signed_cert(folder)`: a self-signed server certificate (`CN=localhost`, SAN
  `localhost` and `127.0.0.1`) and its key, made with `cryptography` at test time; returns
  the two paths. The certificate is its own CA, so it doubles as `sslrootcert`.
- `pg_tls_container` (fixture): the P0-02 image (pgvector/pgvector:pg18) started with the
  repository's own `deploy/postgres/postgresql.conf`, `pg_hba.conf` (at
  `/etc/tumnis/pg_hba.conf`) and initdb scripts, and the certificate mounted read-only.
  Postgres insists that its key belongs to the server user with mode 0600, so the
  entrypoint copies the mounted pair to the paths postgresql.conf names before starting.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import APP, OWNER, PASSWORDS

if TYPE_CHECKING:
    from collections.abc import Iterator

DEPLOY_POSTGRES = Path(__file__).resolve().parents[2] / "deploy" / "postgres"
PG_IMAGE = "pgvector/pgvector:pg18"
# Where deploy/postgres/postgresql.conf reads the server certificate and key.
PG_TLS_DIR = "/etc/tumnis/tls"
PG_CERT, PG_KEY = f"{PG_TLS_DIR}/postgres.crt", f"{PG_TLS_DIR}/postgres.key"
READY_TIMEOUT_S = 90.0


@dataclass(frozen=True)
class TlsPostgres:
    host: str
    port: int
    root_cert: Path
    # `show(name)`: a setting's value, read as the superuser over the local socket (peer):
    # the app roles may not read TLS settings, and the superuser has no network login.
    show: Callable[[str], str]

    def conninfo(self, role: str = APP, dbname: str = "tumnis", **params: str) -> str:
        extra = " ".join(f"{key}={value}" for key, value in params.items())
        return (
            f"host={self.host} port={self.port} dbname={dbname} user={role} "
            f"password={PASSWORDS[role]} connect_timeout=5 {extra}"
        ).strip()


def self_signed_cert(folder: Path, common_name: str = "localhost") -> tuple[Path, Path]:
    """server.crt and server.key in `folder`: an EC P-256 key and a self-signed certificate
    for `common_name`, valid for a day either side of now."""
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(common_name),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    folder.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = folder / "server.crt", folder / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


_ENTRYPOINT = f"""set -eu
mkdir -p {PG_TLS_DIR}
cp /tmp/tls-src/server.crt {PG_CERT}
cp /tmp/tls-src/server.key {PG_KEY}
chown postgres:postgres {PG_CERT} {PG_KEY}
chmod 0600 {PG_KEY}
exec docker-entrypoint.sh postgres -c config_file=/etc/postgresql/postgresql.conf \\
  -c ssl=on -c hba_file=/etc/tumnis/pg_hba.conf -c archive_mode=off
"""


def _wait_until_ready(pg: TlsPostgres) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while True:
        try:
            with psycopg.connect(pg.conninfo(OWNER, sslmode="require")):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture
def pg_tls_container(tmp_path: Path) -> Iterator[TlsPostgres]:
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415

    certs = tmp_path / "tls"
    cert_path, _ = self_signed_cert(certs)
    container = (
        DockerContainer(PG_IMAGE)
        .with_env("POSTGRES_PASSWORD", PASSWORDS["postgres"])
        .with_env("APP_DB_PASSWORD", PASSWORDS[APP])
        .with_env("OWNER_DB_PASSWORD", PASSWORDS[OWNER])
        .with_volume_mapping(
            str(DEPLOY_POSTGRES / "postgresql.conf"), "/etc/postgresql/postgresql.conf", "ro"
        )
        .with_volume_mapping(str(DEPLOY_POSTGRES / "conf.d"), "/etc/postgresql/conf.d", "ro")
        .with_volume_mapping(str(DEPLOY_POSTGRES / "pg_hba.conf"), "/etc/tumnis/pg_hba.conf", "ro")
        .with_volume_mapping(str(DEPLOY_POSTGRES / "initdb"), "/docker-entrypoint-initdb.d", "ro")
        .with_volume_mapping(str(certs), "/tmp/tls-src", "ro")  # noqa: S108  # inside the container
        .with_kwargs(entrypoint=["/bin/sh", "-c"])
        .with_command([_ENTRYPOINT])
        .with_exposed_ports(5432)
    )
    with container as running:

        def show(name: str) -> str:
            wrapped = running.get_wrapped_container()
            code, output = wrapped.exec_run(
                ["psql", "-U", "postgres", "-tAc", f"SHOW {name}"], user="postgres"
            )
            assert code == 0, output
            return str(output.decode()).strip()

        pg = TlsPostgres(
            running.get_container_host_ip(), int(running.get_exposed_port(5432)), cert_path, show
        )
        _wait_until_ready(pg)
        yield pg
