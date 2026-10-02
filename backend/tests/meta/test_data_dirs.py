"""Runtime data folders exist for the image's user and persist where they must (FIX-data-dirs).

Every `KnowledgeSettings` field ending in `_dir` is a folder a container writes at runtime.
The image runs as `tumnis` (uid 10001), so the Dockerfile must create each one owned by that
user: an empty named volume mounted there takes the folder's contents and owner from the
image, and without the folder a `mkdir` under a root-owned parent fails with EACCES. Each
folder is also a named volume on every service that writes or reads it: on the container
layer its data would be lost on every recreate, and a folder two services share (the spool)
would not be shared at all.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.meta.test_compose import load_compose
from tumnis.settings import KnowledgeSettings

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
RUNTIME_USER = "tumnis"
RUNTIME_UID = "10001"

# field -> (named volume, the services that write or read the folder)
RUNTIME_DIRS: dict[str, tuple[str, frozenset[str]]] = {
    # Uploads (api), S3 linked-source objects and vault attachments (worker, sync queue)
    # wait here for worker-extract, which reads and removes them (P1-16, P3-12, P3-13).
    "spool_dir": ("spool", frozenset({"api", "worker", "worker-extract"})),
    # worker-extract's own working copies (P1-16).
    "scratch_dir": ("scratch", frozenset({"worker-extract"})),
    # The worker's Git vault clones and git's HOME, with the per-command key files (P3-12).
    "obsidian_dir": ("obsidian", frozenset({"worker"})),
}


def _defaults() -> dict[str, str]:
    settings = KnowledgeSettings()
    return {name: str(getattr(settings, name)) for name in RUNTIME_DIRS}


def _runtime_stage() -> list[str]:
    """The Dockerfile's last stage, one instruction per entry (continuations joined)."""
    text = (DEPLOY / "Dockerfile").read_text()
    lines: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if pending and line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        lines.append(pending + line)
        pending = ""
    last_from = max(i for i, line in enumerate(lines) if line.upper().startswith("FROM "))
    return lines[last_from:]


def _commands(instruction: str) -> list[list[str]]:
    """The shell commands of one RUN instruction (its --mount flags dropped), split on &&."""
    body = re.sub(r"^RUN\s+(--\S+\s+)*", "", instruction, flags=re.IGNORECASE)
    return [shlex.split(part) for part in body.split("&&") if part.strip()]


def _owned_dirs(stage: list[str]) -> set[str]:
    """Folders made with `install -d -o <runtime user> -g <runtime user>`."""
    made: set[str] = set()
    for instruction in stage:
        if not instruction.upper().startswith("RUN "):
            continue
        for argv in _commands(instruction):
            if argv[:1] != ["install"] or "-d" not in argv:
                continue
            owner = argv[argv.index("-o") + 1] if "-o" in argv else None
            group = argv[argv.index("-g") + 1] if "-g" in argv else None
            if owner == RUNTIME_USER and group == RUNTIME_USER:
                made |= {arg for arg in argv[1:] if arg.startswith("/")}
    return made


@pytest.mark.req("FR-15.10", "FR-15.11", "SEC-10")
@pytest.mark.wp("P3-12")
def test_every_runtime_folder_setting_is_classified() -> None:
    """A new `*_dir` setting fails here until it names its volume and its services."""
    fields = {name for name in KnowledgeSettings.model_fields if name.endswith("_dir")}
    assert fields == set(RUNTIME_DIRS)


@pytest.mark.req("FR-15.10", "FR-15.11", "SEC-10")
@pytest.mark.wp("P3-12")
def test_image_makes_every_runtime_folder_for_its_user() -> None:
    """The image runs as tumnis (uid 10001) and makes each runtime folder owned by it, so
    the worker can clone a vault under `obsidian_dir` (a root-owned /var/lib/tumnis refused
    the mkdir with EACCES)."""
    stage = _runtime_stage()
    users = [line.split()[1] for line in stage if line.upper().startswith("USER ")]
    assert users[-1:] == [RUNTIME_USER], users
    useradd = [
        argv
        for line in stage
        if line.upper().startswith("RUN ")
        for argv in _commands(line)
        if argv[:1] == ["useradd"]
    ]
    assert any(argv[-1] == RUNTIME_USER and RUNTIME_UID in argv for argv in useradd), useradd
    made = _owned_dirs(stage)
    missing = {name: path for name, path in _defaults().items() if path not in made}
    assert not missing, f"not made as {RUNTIME_USER}: {missing}"


def _mounts(service: dict[str, Any]) -> dict[str, str]:
    """target -> source of a service's short-syntax mounts, ':ro' ones left out."""
    found: dict[str, str] = {}
    for entry in service.get("volumes") or []:
        parts = str(entry).split(":")
        if len(parts) >= 2 and "ro" not in parts[2:]:
            found[parts[1]] = parts[0]
    return found


@pytest.mark.req("FR-15.10", "FR-15.11", "SEC-10")
@pytest.mark.wp("P3-12")
def test_compose_mounts_each_runtime_folder_on_every_service_that_uses_it() -> None:
    """Each runtime folder is a declared named volume, mounted read-write at the setting's
    default on every service that uses it: the worker shares the spool with the api and
    worker-extract, and keeps its vault clones in `obsidian` across a recreate."""
    compose = load_compose("compose.yaml")
    services = compose["services"]
    declared = set(compose.get("volumes") or {})
    defaults = _defaults()
    wrong: list[str] = []
    for name, (volume, users) in RUNTIME_DIRS.items():
        if volume not in declared:
            wrong.append(f"volume {volume!r} is not declared")
        for service in sorted(users):
            source = _mounts(services[service]).get(defaults[name])
            if source != volume:
                wrong.append(f"{service}: {defaults[name]} is {source!r}, not {volume!r}")
    assert not wrong, wrong


def _render(*files: str) -> dict[str, Any]:
    """`docker compose config` of `files` (no daemon needed), with no .env."""
    docker = shutil.which("docker")
    if docker is None:
        if os.environ.get("CI"):
            pytest.fail("docker is not available in CI")
        pytest.skip("docker is not available")
    command = [docker, "compose"]
    for name in files:
        command += ["-f", str(DEPLOY / name)]
    command += ["--project-directory", str(DEPLOY), "--env-file", os.devnull]
    command += ["config", "--format", "json"]
    done = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr
    rendered: dict[str, Any] = json.loads(done.stdout)
    return rendered


@pytest.mark.req("FR-15.10", "FR-15.11", "SEC-10")
@pytest.mark.wp("P3-12")
def test_preview_overrides_keep_the_runtime_volumes() -> None:
    """Rendered by Compose itself: the preview overrides (which the CI stack includes)
    replace each service's secrets mount but keep its runtime volumes, because Compose
    merges service volumes by target."""
    defaults = _defaults()
    services = _render("compose.yaml", "compose.preview.yaml")["services"]
    wrong: list[str] = []
    for name, (volume, users) in RUNTIME_DIRS.items():
        for service in sorted(users):
            mounts = {
                m.get("target"): (m.get("type"), m.get("source"), bool(m.get("read_only")))
                for m in services[service].get("volumes") or []
            }
            got = mounts.get(defaults[name])
            if got != ("volume", volume, False):
                wrong.append(f"{service}: {defaults[name]} is {got!r}")
    assert not wrong, wrong
