"""Shared daemon test helpers: a config in a temporary state dir, and Run messages."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path
from typing import Any

import pytest
from tumnis_daemon.config import DaemonConfig
from tumnis_daemon.protocol import Run, SchemaRef, envelope

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
STUB_HERMES = TESTS / "stubs" / "hermes"
RECORDINGS = TESTS / "recordings" / "stream_json"


@pytest.fixture
def cfg(tmp_path: Path) -> DaemonConfig:
    token = tmp_path / "runner.token"
    token.write_text("tmd_abcdefgh_SECRET\n")
    STUB_HERMES.chmod(STUB_HERMES.stat().st_mode | stat.S_IXUSR)
    return DaemonConfig(
        server_url="wss://tumnis.example.org",
        runner_name="homelab-hermes",
        token_file=token,
        state_dir=tmp_path / "state",
        hermes_bin=str(STUB_HERMES),
        profiles=("acme-site",),
    )


def make_run(
    *,
    profile: str = "acme-site",
    skill: str = "enrich",
    prompt: str = "Use the skill.\n",
    timeout_s: int = 60,
) -> Run:
    """A Run as the server would send it; built without validation so a test can hand the
    runner names the protocol itself would refuse."""
    run_id = uuid.uuid4()
    fields: dict[str, Any] = {
        **envelope(f"run:{run_id}"),
        "schema_version": 1,
        "type": "run",
        "run_id": run_id,
        "profile": profile,
        "skill": skill,
        "packet": {"prompt_text": prompt, "skill": skill, "run_id": str(run_id)},
        "output_schema": SchemaRef(family="enrichment", name="result", version=1),
        "timeout_s": timeout_s,
        "workdir_policy": "none",
    }
    return Run.model_construct(**fields)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
