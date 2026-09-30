"""The profile health probe (P2-10, SAF-2, SAF-3): the MCP server list read from the
profile's own config, redacted to names, transports and hosts, and each GitHub and Coolify
token's reach, probed on the host against fakes (respx). The server only ever hears
booleans and target names: no token, argument, env value or header leaves the host.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import string
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.conftest import STUB_HERMES, TESTS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

FIXTURES = TESTS / "fixtures"
GITHUB = "https://api.github.com"
COOLIFY = "https://coolify.example.org"


def _repo(push: bool, *, admin: bool = False, private: bool = False) -> dict[str, Any]:
    """The parts of GitHub's repository answer the probe reads."""
    return {
        "full_name": "example/repo",
        "private": private,
        "permissions": {"admin": admin, "maintain": False, "push": push, "pull": True},
    }


@pytest.fixture
def profile_dir(tmp_path: Path) -> Path:
    found = tmp_path / "hermes" / "profiles" / "acme-site"
    found.mkdir(parents=True)
    shutil.copy(FIXTURES / "profile_config.yaml", found / "config.yaml")
    shutil.copy(FIXTURES / "profile.env", found / ".env")
    return found


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=5.0) as opened:
        yield opened


@pytest.mark.req("SAF-2")
@pytest.mark.wp("P2-10")
@pytest.mark.xfail(strict=True, reason="spec:P2-10")
def test_parses_mcp_server_list_redacted(profile_dir: Path) -> None:
    """T-P2-10-01
    The fixture config (the template's four servers plus `shell`) yields each server's
    name, transport (stdio for a command, http for a URL) and a redacted target: the
    command's name without its path, or the URL's host. No argument, env value, header, URL
    credential or query appears anywhere in the report.
    """
    from tumnis_daemon.health import mcp_servers  # noqa: PLC0415

    found = mcp_servers(profile_dir)
    as_json = [json.loads(s.model_dump_json()) for s in found]
    assert as_json == [
        {"name": "coolify", "transport": "stdio", "target": "coolify-mcp"},
        {"name": "github", "transport": "stdio", "target": "npx"},
        {"name": "jev", "transport": "stdio", "target": "uvx"},
        {"name": "shell", "transport": "stdio", "target": "bash"},
        {"name": "tumnis", "transport": "http", "target": "tumnis.example.org"},
    ]
    text = json.dumps(as_json)
    assert "SECRET" not in text
    assert "example.org/jev-mcp" not in text  # arguments never travel


@pytest.mark.req("SAF-3")
@pytest.mark.wp("P2-10")
@pytest.mark.xfail(strict=True, reason="spec:P2-10")
async def test_cross_project_denial_on_fake_github(client: httpx.AsyncClient) -> None:
    """T-P2-10-04
    A token scoped to its own project: its own repo answers 200 with push; another
    project's private repos answer 404; another project's public repo answers 200 with
    `permissions.push: false` (any token may read a public repo). The report shows its own
    repo reachable and no foreign reach, and every request went to api.github.com with the
    token as a Bearer header.
    """
    from tumnis_daemon.health import probe_github  # noqa: PLC0415

    with respx.mock(base_url=GITHUB, assert_all_called=True) as fake:
        own = fake.get("/repos/acme/site").respond(200, json=_repo(True, private=True))
        fake.get("/repos/beta/app").respond(404, json={"message": "Not Found"})
        fake.get("/repos/gamma/internal").respond(404, json={"message": "Not Found"})
        fake.get("/repos/delta/public-docs").respond(200, json=_repo(False))

        reach = await probe_github(
            client,
            "fake-github-token-for-tests",
            own=["acme/site"],
            foreign=["beta/app", "gamma/internal", "delta/public-docs"],
        )

    assert reach.token_present is True
    assert reach.own_reachable == {"acme/site": True}
    assert reach.foreign_reachable == []
    assert reach.errors == []
    sent = own.calls.last.request
    assert sent.headers["Authorization"] == "Bearer fake-github-token-for-tests"
    assert sent.url.host == "api.github.com"


@pytest.mark.req("SAF-3")
@pytest.mark.wp("P2-10")
@pytest.mark.xfail(strict=True, reason="spec:P2-10")
async def test_broad_token_reports_foreign_reach(client: httpx.AsyncClient) -> None:
    """T-P2-10-05
    A broad token: another project's repo answers 200 with `permissions.push: true`, a
    third answers 200 with `permissions.admin: true`. Both are listed in
    `foreign_reachable`; a foreign repo it can only read is not.
    """
    from tumnis_daemon.health import probe_github  # noqa: PLC0415

    with respx.mock(base_url=GITHUB, assert_all_called=True) as fake:
        fake.get("/repos/acme/site").respond(200, json=_repo(True))
        fake.get("/repos/beta/app").respond(200, json=_repo(True, private=True))
        fake.get("/repos/gamma/ops").respond(200, json=_repo(False, admin=True, private=True))
        fake.get("/repos/delta/public-docs").respond(200, json=_repo(False))

        reach = await probe_github(
            client,
            "fake-github-token-for-tests",
            own=["acme/site"],
            foreign=["beta/app", "gamma/ops", "delta/public-docs"],
        )

    assert reach.own_reachable == {"acme/site": True}
    assert reach.foreign_reachable == ["beta/app", "gamma/ops"]


class _Frames:
    """Stands in for the server socket: every frame the daemon sends."""

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def send(self, message: str, /) -> None:
        self.frames.append(message)


# A token-like prefix keeps a generated token from colliding with a word the report
# naturally contains (a field name, a host).
TOKENS = st.text(
    alphabet=string.ascii_letters + string.digits + "_-|.", min_size=16, max_size=64
).map(lambda s: f"tkn_{s}")


def _hostile_fakes(fake: respx.MockRouter, token: str) -> None:
    """Fakes that echo the token back in every way a careless probe could pass it on."""
    fake.get(f"{GITHUB}/repos/acme/site").respond(
        200, json={**_repo(True), "description": f"token {token}"}
    )
    fake.get(f"{GITHUB}/repos/beta/app").respond(401, json={"message": f"Bad credentials: {token}"})
    fake.get(f"{COOLIFY}/api/v1/applications/app-own").respond(
        200, json={"uuid": "app-own", "name": f"app {token}"}
    )
    fake.get(f"{COOLIFY}/api/v1/applications/app-beta").mock(
        side_effect=httpx.ConnectError(f"refused for {token}")
    )


async def _report_frames(token: str, root: Path) -> list[str]:
    from tumnis_daemon.config import DaemonConfig  # noqa: PLC0415
    from tumnis_daemon.protocol import HealthCheck, envelope  # noqa: PLC0415
    from tumnis_daemon.runner import check_health  # noqa: PLC0415
    from tumnis_daemon.state import StateStore  # noqa: PLC0415

    home = root / "hermes"
    profile = home / "profiles" / "acme-site"
    profile.mkdir(parents=True)
    config = {
        "mcp_servers": {
            "tumnis": {
                "url": f"https://agent:{token}@tumnis.example.org/mcp?token={token}",
                "headers": {"Authorization": f"Bearer {token}"},
            },
            "github": {"command": "npx", "args": ["--token", token], "env": {"T": token}},
        }
    }
    (profile / "config.yaml").write_text(yaml.safe_dump(config))
    (profile / ".env").write_text(f"GITHUB_TOKEN={token}\nCOOLIFY_TOKEN={token}\n")
    (root / "runner.token").write_text("tmd_abcdefgh_DEVICE\n")
    cfg = DaemonConfig(
        server_url="wss://tumnis.example.org",
        runner_name="homelab-hermes",
        token_file=root / "runner.token",
        state_dir=root / "state",
        hermes_bin=str(STUB_HERMES),
        profiles=("acme-site",),
        hermes_home=home,
    )
    request_id = uuid.uuid4()
    check = HealthCheck(
        **envelope(f"req:{request_id}"),
        request_id=request_id,
        profile="acme-site",
        own_repos=["acme/site"],
        foreign_repos=["beta/app"],
        own_apps=["app-own"],
        foreign_apps=["app-beta"],
        coolify_base_url=COOLIFY,
    )
    state = StateStore(cfg.state_dir)
    socket = _Frames()
    state.ws = socket
    with respx.mock(assert_all_called=False) as fake:
        _hostile_fakes(fake, token)
        await check_health(check, state, cfg)
    return socket.frames + state.unacked()


@pytest.mark.req("SAF-3")
@pytest.mark.wp("P2-10")
@pytest.mark.xfail(strict=True, reason="spec:P2-10")
@settings(max_examples=25, deadline=None)
@given(token=TOKENS)
def test_token_never_leaves_host(token: str) -> None:
    """T-P2-10-07
    Property: for random token strings written to the profile's `.env` and config, with
    fakes that echo the token back in bodies and errors, no frame the daemon sends to the
    server (nor any it keeps to resend) contains the token, while the report still carries
    the probe's booleans.
    """
    with tempfile.TemporaryDirectory() as tmp:
        frames = asyncio.run(_report_frames(token, Path(tmp)))

    assert frames, "the health report was never sent"
    for frame in frames:
        assert token not in frame
    report = json.loads(frames[0])
    assert report["type"] == "health_report"
    assert report["github"]["own_reachable"] == {"acme/site": True}
    assert report["github"]["foreign_reachable"] == []
    assert report["coolify"]["own_reachable"] == {"app-own": True}
