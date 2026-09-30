"""The daemon remembers the profiles it provisioned (P1-06, FR-5.10), so its next register
lists them, and answers every `provision` reliably (kept until acked)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from tests.conftest import REPO
from tumnis_daemon.protocol import Provision, envelope
from tumnis_daemon.provision import provision, remembered_profiles
from tumnis_daemon.state import StateStore

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis_daemon.config import DaemonConfig

TEMPLATE = REPO / "profiles" / "project-template"


def _request(profile: str, mode: str = "create") -> Provision:
    request_id = uuid.uuid4()
    return Provision(
        **envelope(f"provision:{request_id}"),
        request_id=request_id,
        profile=profile,
        mode=mode,  # type: ignore[arg-type]
        template="project-template",
        template_version=(TEMPLATE / "VERSION").read_text().strip(),
    )


@pytest.mark.wp("P1-06")
async def test_provisioned_profiles_are_remembered(
    cfg: DaemonConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_dir = tmp_path / "profiles"
    monkeypatch.setenv("HERMES_STUB_PROFILES_DIR", str(profiles_dir))
    cfg = replace(cfg, template_dir=TEMPLATE, hermes_profiles_dir=profiles_dir)
    state = StateStore(cfg.state_dir)
    assert remembered_profiles(cfg.state_dir) == []

    await provision(_request("beta-app"), state, cfg)
    await provision(_request("beta-app"), state, cfg)  # exists: still one entry
    await provision(_request("nobody-here", "link"), state, cfg)  # not_found: not kept

    assert remembered_profiles(cfg.state_dir) == ["beta-app"]
    assert len(state.unacked()) == 3  # every answer waits for its ack


@pytest.mark.wp("P1-06")
async def test_a_profile_without_its_env_is_removed(
    cfg: DaemonConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The install succeeded but the profile's `.env` could not be written: the daemon
    deletes the profile before answering `failed`, so the retry installs it again rather
    than answering `exists` for a profile without its env."""
    from tumnis_daemon import provision as provision_mod  # noqa: PLC0415

    profiles_dir = tmp_path / "profiles"
    log = tmp_path / "hermes.log"
    monkeypatch.setenv("HERMES_STUB_PROFILES_DIR", str(profiles_dir))
    monkeypatch.setenv("HERMES_STUB_LOG", str(log))
    cfg = replace(cfg, template_dir=TEMPLATE, hermes_profiles_dir=profiles_dir)

    def no_env(_cfg: DaemonConfig, _name: str) -> None:
        raise PermissionError("profile.env is not readable")

    monkeypatch.setattr(provision_mod, "_copy_profile_env", no_env)
    answer = await provision_mod.handle_provision(_request("beta-app"), cfg)

    assert answer.status == "failed"
    assert answer.error_code == "hermes_error"
    assert not (profiles_dir / "beta-app").exists()
    assert "profile\tdelete\tbeta-app\t--yes" in log.read_text()

    monkeypatch.undo()
    monkeypatch.setenv("HERMES_STUB_PROFILES_DIR", str(profiles_dir))
    retried = await provision_mod.handle_provision(_request("beta-app"), cfg)
    assert retried.status == "created"


@pytest.mark.wp("P1-06")
def test_remembered_profiles_ignores_a_bad_file(cfg: DaemonConfig) -> None:
    cfg.state_dir.mkdir(parents=True)
    (cfg.state_dir / "profiles.json").write_text('["ok-name", "Bad Name", 3]')
    assert remembered_profiles(cfg.state_dir) == ["ok-name"]
    (cfg.state_dir / "profiles.json").write_text("{not json")
    assert remembered_profiles(cfg.state_dir) == []
