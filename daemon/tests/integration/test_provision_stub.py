"""The daemon's `provision` handler against a stub `hermes` (P1-06, FR-5.10): create
installs the bundled project template once, a second create answers `exists`, a template
version the daemon does not ship is refused before Hermes runs, link checks existence, and
the new profile gets the operator's `.env` with mode 0600."""

from __future__ import annotations

import stat
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

import pytest

from tests.conftest import REPO

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis_daemon.config import DaemonConfig

pytestmark = [pytest.mark.integration]

TEMPLATE = REPO / "profiles" / "project-template"


def _provision(profile: str, mode: Literal["create", "link"], version: str) -> Any:
    from tumnis_daemon.protocol import Provision, envelope  # noqa: PLC0415

    request_id = uuid.uuid4()
    return Provision(
        **envelope(f"provision:{request_id}"),
        request_id=request_id,
        profile=profile,
        mode=mode,
        template="project-template",
        template_version=version,
    )


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [line.split("\t") for line in log.read_text().splitlines() if line]


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
@pytest.mark.xfail(strict=True, reason="spec:P1-06")
async def test_create_is_idempotent_and_version_checked(
    cfg: DaemonConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P1-06-09
    With a stub `hermes` recording its argv: a create runs `profile show` then
    `profile install <template> --name <n> --yes` once and answers `created` with the
    template's version; a second create answers `exists` without installing; a mismatched
    template version answers `template_version_mismatch` without running Hermes at all;
    link answers `linked` for an existing profile and `not_found` otherwise. The created
    profile's `.env` is the operator's profile env, mode 0600.
    """
    from tumnis_daemon.provision import handle_provision  # noqa: PLC0415

    log = tmp_path / "hermes-calls.tsv"
    profiles_dir = tmp_path / "hermes" / "profiles"
    profile_env = tmp_path / "profile.env"
    profile_env.write_text("TYPESAFE_API_KEY=placeholder\n")
    monkeypatch.setenv("HERMES_STUB_LOG", str(log))
    monkeypatch.setenv("HERMES_STUB_PROFILES_DIR", str(profiles_dir))
    cfg = replace(
        cfg,
        template_dir=TEMPLATE,
        hermes_profiles_dir=profiles_dir,
        profile_env_file=profile_env,
    )
    version = (TEMPLATE / "VERSION").read_text().strip()
    assert cfg.bundled_template_version == version

    created = await handle_provision(_provision("beta-app", "create", version), cfg)
    assert (created.status, created.error_code) == ("created", None)
    assert created.distribution_version == version
    assert created.profile == "beta-app"
    installs = [c for c in _calls(log) if c[:2] == ["profile", "install"]]
    assert installs == [["profile", "install", str(TEMPLATE), "--name", "beta-app", "--yes"]]
    env = profiles_dir / "beta-app" / ".env"
    assert env.read_text() == "TYPESAFE_API_KEY=placeholder\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600

    again = await handle_provision(_provision("beta-app", "create", version), cfg)
    assert (again.status, again.error_code) == ("exists", None)
    assert len([c for c in _calls(log) if c[:2] == ["profile", "install"]]) == 1

    before = len(_calls(log))
    refused = await handle_provision(_provision("gamma-app", "create", "999.0.0"), cfg)
    assert (refused.status, refused.error_code) == ("failed", "template_version_mismatch")
    assert len(_calls(log)) == before

    linked = await handle_provision(_provision("beta-app", "link", version), cfg)
    assert (linked.status, linked.error_code) == ("linked", None)
    missing = await handle_provision(_provision("nobody-here", "link", version), cfg)
    assert (missing.status, missing.error_code) == ("failed", "not_found")
    assert not [c for c in _calls(log) if "nobody-here" in c and "install" in c]
