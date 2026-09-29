"""Compose files: no public ports, start order, pooling (P0-04, FR-9.1, FR-12.4, ADR-0006)."""

from __future__ import annotations

import configparser
import ipaddress
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
# compose.test.yaml is the CI stack and publishes 127.0.0.1:8080 on purpose: exempt by name.
SELF_HOSTED_FILES = ("compose.yaml", "compose.preview.yaml")
ALLOWED_HOST_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",
        "100.64.0.0/10",  # Tailscale
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
    )
)
_VARIABLE = re.compile(r"\$\{(\w+)(?::?[-?]([^}]*))?\}")


def load_compose(name: str) -> dict[str, Any]:
    """Parse a compose file with ${VAR:-default} expanded against an empty environment."""
    text = (DEPLOY / name).read_text()
    expanded = _VARIABLE.sub(lambda m: m.group(2) or "", text.replace("$$", "\0"))
    data = yaml.safe_load(expanded.replace("\0", "$"))
    assert isinstance(data, dict), name
    return data


def _host_ips(entry: Any) -> list[str]:
    """The host IP of one `ports` entry, short or long syntax; "" when none is given."""
    if isinstance(entry, dict):
        return [str(entry.get("host_ip", ""))]
    spec = str(entry).rsplit("/", 1)[0]
    if spec.startswith("["):  # [::1]:8080:80
        return [spec[1 : spec.index("]")]]
    parts = spec.split(":")
    return [parts[0]] if len(parts) == 3 else [""]  # ip:host:container


def _is_private(host_ip: str) -> bool:
    try:
        address = ipaddress.ip_address(host_ip)
    except ValueError:
        return False
    return any(address in net for net in ALLOWED_HOST_NETWORKS)


def _environment(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {str(k): str(v) for k, v in env.items()}


def _depends_on(service: dict[str, Any]) -> dict[str, str]:
    deps = service.get("depends_on") or {}
    if isinstance(deps, list):
        return dict.fromkeys(deps, "service_started")
    return {name: spec.get("condition", "service_started") for name, spec in deps.items()}


@pytest.mark.req("FR-9.1")
@pytest.mark.wp("P0-04")
@pytest.mark.xfail(strict=True, reason="spec:P0-04")
def test_self_hosted_compose_publishes_no_public_port() -> None:
    """T-P0-04-09
    No service in compose.yaml or compose.preview.yaml publishes a port on 0.0.0.0, :: or an
    empty host IP: every published port names a loopback, Tailscale or private address.
    """
    base = load_compose("compose.yaml")["services"]
    assert {"migrate", "api", "worker", "postgres", "pgbouncer"} <= base.keys()
    for name in SELF_HOSTED_FILES:
        for service_name, service in (load_compose(name).get("services") or {}).items():
            for entry in (service or {}).get("ports") or []:
                for host_ip in _host_ips(entry):
                    assert _is_private(host_ip), f"{name}: {service_name} publishes {entry!r}"


@pytest.mark.req("FR-12.4", "ADR-0006")
@pytest.mark.wp("P0-04")
@pytest.mark.xfail(strict=True, reason="spec:P0-04")
def test_compose_order_and_pooling() -> None:
    """T-P0-04-10
    api and worker wait for migrate to complete; api reaches Postgres through PgBouncer;
    the worker and DBOS use the direct URL; PgBouncer pools in transaction mode; the
    Postgres image is pgvector on Postgres 18 with pgBackRest.
    """
    services = load_compose("compose.yaml")["services"]
    for name in ("api", "worker"):
        assert _depends_on(services[name]).get("migrate") == "service_completed_successfully"

    api_env = _environment(services["api"])
    assert urlsplit(api_env["DATABASE_URL"]).hostname == "pgbouncer"
    worker_env = _environment(services["worker"])
    direct = urlsplit(worker_env["DATABASE_DIRECT_URL"])
    assert (direct.hostname, direct.port) == ("postgres", 5432)
    for env in (api_env, worker_env):
        dbos_url = env.get("DBOS_SYSTEM_DATABASE_URL")  # unset: derived from the direct URL
        if dbos_url:
            assert urlsplit(dbos_url).hostname == "postgres"

    pgbouncer = services["pgbouncer"]
    mounts = [str(v) for v in pgbouncer.get("volumes") or []]
    assert any(
        m.split(":")[0].endswith("pgbouncer/pgbouncer.ini")
        and m.split(":")[1] == "/etc/pgbouncer/pgbouncer.ini"
        for m in mounts
    ), mounts
    ini = configparser.ConfigParser()
    ini.read(DEPLOY / "pgbouncer" / "pgbouncer.ini")
    assert ini["pgbouncer"]["pool_mode"].strip() == "transaction"
    assert int(ini["pgbouncer"]["max_prepared_statements"]) > 0

    build = services["postgres"]["build"]
    context = build if isinstance(build, str) else build["context"]
    dockerfile = (DEPLOY / context / "Dockerfile").read_text()
    base_image = re.search(r"^FROM\s+(\S+)", dockerfile, re.MULTILINE)
    assert base_image is not None
    assert base_image.group(1).split("@")[0] == "pgvector/pgvector:pg18"
    assert re.search(r"\bpgbackrest\b", dockerfile)
