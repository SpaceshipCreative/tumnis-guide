"""Compose files: no public ports, start order, pooling (P0-04, FR-9.1, FR-12.4, ADR-0006)."""

from __future__ import annotations

import configparser
import ipaddress
import json
import os
import re
import shutil
import subprocess
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


def _mem_limit_bytes(service: dict[str, Any]) -> int:
    """`mem_limit` (or the deploy limit) as bytes: '4g', '512m' or a plain byte count."""
    raw = service.get("mem_limit") or (
        ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}
    ).get("memory")
    assert raw, "no memory limit"
    match = re.fullmatch(r"(\d+)\s*([kmg]?)b?", str(raw).strip().lower())
    assert match is not None, raw
    return int(match.group(1)) * {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}[match.group(2)]


@pytest.mark.req("ADR-0007")
@pytest.mark.wp("P1-16")
def test_worker_extract_has_memory_limit_and_clamd() -> None:
    """T-P1-16-14
    compose.yaml defines `clamd` and `worker-extract`: the extract worker runs
    `tumnis worker --queues extract` under a memory limit, mounts the shared `spool` volume
    read-write and its own `scratch` volume, and waits for clamd; the main worker does not
    listen to `extract`, and the api shares the spool.
    """
    compose = load_compose("compose.yaml")
    services = compose["services"]
    assert {"clamd", "worker-extract"} <= services.keys()
    extract = services["worker-extract"]
    assert extract["command"] == ["tumnis", "worker", "--queues", "extract"]
    assert 0 < _mem_limit_bytes(extract) <= 8 << 30
    volumes = [str(v) for v in extract.get("volumes") or []]
    assert any(v.startswith("spool:") and not v.endswith(":ro") for v in volumes), volumes
    assert any(v.startswith("scratch:") for v in volumes), volumes
    assert "clamd" in _depends_on(extract)
    assert _environment(extract)["KNOWLEDGE__CLAMD_HOST"] == "clamd"
    assert "--queues" not in services["worker"]["command"]
    assert any(str(v).startswith("spool:") for v in services["api"].get("volumes") or [])
    assert {"spool", "scratch"} <= compose["volumes"].keys()


# The hosted speech and embedding providers' variables (Scott decisions 75 and 90).
HOSTED_KEYS = (
    "SPEECH__HOSTED_BASE_URL",
    "SPEECH__HOSTED_MODEL",
    "SPEECH__HOSTED_VOICE",
    "SPEECH__HOSTED_API_KEY",
    "EMBEDDINGS__HOSTED_BASE_URL",
    "EMBEDDINGS__HOSTED_MODEL",
    "EMBEDDINGS__HOSTED_DIMS",
    "EMBEDDINGS__HOSTED_API_KEY",
)
HOSTED_KEYS_FILE = "hosted-keys.env"


def _env_files(service: dict[str, Any]) -> list[dict[str, Any]]:
    """A service's `env_file` entries in long form ({path, required})."""
    raw = service.get("env_file") or []
    entries = [raw] if isinstance(raw, str | dict) else list(raw)
    return [e if isinstance(e, dict) else {"path": e, "required": True} for e in entries]


@pytest.mark.req("REL-4")
def test_hosted_keys_reach_only_the_worker_and_only_when_set() -> None:
    """Rollback compatibility (Scott decision 90): compose.yaml never passes the hosted
    provider variables itself, so an image older than them (its settings forbid unknown
    keys) still starts under this compose file. They reach the worker, and only the worker,
    through the optional deploy/hosted-keys.env (`required: false`), which holds just those
    keys; the repository ships a commented-out example and never the real file."""
    services = load_compose("compose.yaml")["services"]
    for name, service in services.items():
        leaked = sorted(set(_environment(service or {})) & set(HOSTED_KEYS))
        assert not leaked, f"{name} passes {leaked} even when they are unset"

    worker_files = [e for e in _env_files(services["worker"]) if e["path"] == HOSTED_KEYS_FILE]
    assert worker_files == [{"path": HOSTED_KEYS_FILE, "required": False}], services["worker"]
    for name, service in services.items():
        if name != "worker":
            paths = [str(e["path"]) for e in _env_files(service or {})]
            assert not any(p.endswith(HOSTED_KEYS_FILE) for p in paths), (name, paths)

    example = (DEPLOY / f"{HOSTED_KEYS_FILE}.example").read_text().splitlines()
    assert not [line for line in example if line.strip() and not line.startswith("#")], (
        "every line of the example is a comment: copied as it is, it sets nothing"
    )
    listed = {line.lstrip("# ").split("=", 1)[0] for line in example if "=" in line}
    assert set(HOSTED_KEYS) <= listed
    ignored = (REPO / ".gitignore").read_text().splitlines()
    assert f"deploy/{HOSTED_KEYS_FILE}" in ignored


def _render(project_dir: Path) -> dict[str, Any]:
    """`docker compose config` of compose.yaml (no daemon needed), with no .env and the
    hosted keys file looked up in `project_dir`."""
    docker = shutil.which("docker")
    if docker is None:
        if os.environ.get("CI"):
            pytest.fail("docker is not available in CI")
        pytest.skip("docker is not available")
    command = [docker, "compose", "-f", str(DEPLOY / "compose.yaml")]
    command += ["--project-directory", str(project_dir), "--env-file", os.devnull]
    command += ["config", "--format", "json"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SPEECH__", "EMBEDDINGS__"))}
    done = subprocess.run(command, env=env, capture_output=True, text=True, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr
    rendered: dict[str, Any] = json.loads(done.stdout)
    return rendered


@pytest.mark.req("REL-4")
def test_rendered_compose_gives_hosted_keys_to_the_worker_only_when_the_file_has_them(
    tmp_path: Path,
) -> None:
    """Rendered by Compose itself (Scott decision 90): without the hosted keys file the
    worker gets none of the hosted variables, not even as empty strings; with the file, the
    worker gets exactly its values and no other service gets any."""
    services = _render(tmp_path)["services"]
    for name, service in services.items():
        leaked = sorted(set(service.get("environment") or {}) & set(HOSTED_KEYS))
        assert not leaked, f"{name} gets {leaked} without the hosted keys file"

    values = {key: f"test-{key.lower()}" for key in HOSTED_KEYS}  # placeholders, not keys
    (tmp_path / HOSTED_KEYS_FILE).write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    services = _render(tmp_path)["services"]
    worker_env = services["worker"].get("environment") or {}
    assert {k: worker_env.get(k) for k in HOSTED_KEYS} == values
    for name, service in services.items():
        if name != "worker":
            leaked = sorted(set(service.get("environment") or {}) & set(HOSTED_KEYS))
            assert not leaked, f"{name} gets {leaked}"
