"""The profile health check with tool allowlists and token reach (P2-10, SAF-2, SAF-3,
FR-5.9): the runner reports the profile's MCP servers and each token's reach; drift from
the project's allowlist, or a token reaching another project, marks the profile degraded
and queues one `drift` review item (never a second for the same finding)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

TEMPLATE = ("tumnis", "jev", "github", "coolify")


def _servers(*names: str) -> dict[str, Any]:
    """The health report's server fields for these names."""
    return {
        "mcp_servers": list(names),
        "mcp_server_details": [
            {"name": name, "transport": "stdio", "target": "npx"} for name in names
        ],
    }


def _open_drift_items(db: DbUrls) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(
            b"SELECT project_id, payload, dedupe_key FROM review_items"
            b" WHERE kind = 'drift' AND decided_at IS NULL AND deleted_at IS NULL"
            b" ORDER BY created_at"
        ).fetchall()


async def _project(client: SessionClient, name: str, links: list[dict[str, str]]) -> str:
    made = await client.post("/v1/projects", json={"name": name, "links": links})
    assert made.status_code == 201, made.text
    project_id: str = made.json()["id"]
    return project_id


async def _profile(client: SessionClient, profile_id: str) -> dict[str, Any]:
    listed = await client.get("/v1/agents/profiles")
    assert listed.status_code == 200, listed.text
    (item,) = [p for p in listed.json()["items"] if p["id"] == profile_id]
    found: dict[str, Any] = item
    return found


async def _check(client: SessionClient, profile_id: str) -> dict[str, Any]:
    """Ask for a health check and wait until it has been recorded; the profile after it."""
    before = (await _profile(client, profile_id))["health_checked_at"]
    accepted = await client.post(f"/v1/agents/profiles/{profile_id}/health-check")
    assert accepted.status_code == 202, accepted.text
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 20
    while (current := await _profile(client, profile_id))["health_checked_at"] == before:
        assert loop.time() < deadline, "the health check never reported"
        await asyncio.sleep(0.1)
    return current


@pytest.mark.req("SAF-2")
@pytest.mark.wp("P2-10")
@pytest.mark.xfail(strict=True, reason="spec:P2-10")
async def test_drift_marks_degraded_and_adds_review_item(
    dbos: type[DBOS],
    session_client: SessionClient,
    fake_runner: FakeRunnerFactory,
    db: DbUrls,
) -> None:
    """T-P2-10-03
    The project's allowlist is the template's (tumnis, jev, github, coolify). The runner
    reports those four plus `shell`: the profile's health is `degraded`, and one open
    review item of kind `drift` names `shell` for the project. The same report on the next
    check adds no second item.
    """
    runner = fake_runner(profiles=["acme-site"])
    project_id = await _project(session_client, "Acme site", [])
    profile_id = str(
        fake_runner.register_profile("acme-site", runner=runner, project_id=uuid.UUID(project_id))
    )
    runner.script_health("acme-site", **_servers(*TEMPLATE, "shell"))

    checked = await _check(session_client, profile_id)
    assert checked["health"]["status"] == "degraded"
    items = _open_drift_items(db)
    assert len(items) == 1
    item_project, payload, dedupe_key = items[0]
    assert str(item_project) == project_id
    assert "shell" in json.dumps(payload)
    assert dedupe_key

    again = await _check(session_client, profile_id)
    assert again["health"]["status"] == "degraded"
    assert len(_open_drift_items(db)) == 1


@pytest.mark.req("SAF-3")
@pytest.mark.wp("P2-10")
@pytest.mark.xfail(strict=True, reason="spec:P2-10")
async def test_foreign_reach_marks_degraded(
    dbos: type[DBOS],
    session_client: SessionClient,
    fake_runner: FakeRunnerFactory,
    db: DbUrls,
) -> None:
    """T-P2-10-06
    Acme links repo `acme/site` and app `app-acme`; Beta links repo `beta/app` and app
    `app-beta`. The health check sent to Acme's profile lists Acme's repos and apps as its
    own and Beta's as foreign. A report whose GitHub token reaches `beta/app` marks the
    profile degraded, with a `drift` review item that names Beta app; a later report whose
    Coolify token reaches `app-beta` does the same.
    """
    runner = fake_runner(profiles=["acme-site"])
    acme = await _project(
        session_client,
        "Acme site",
        [{"kind": "repo", "value": "acme/site"}, {"kind": "coolify_app", "value": "app-acme"}],
    )
    await _project(
        session_client,
        "Beta app",
        [{"kind": "repo", "value": "beta/app"}, {"kind": "coolify_app", "value": "app-beta"}],
    )
    profile_id = str(
        fake_runner.register_profile("acme-site", runner=runner, project_id=uuid.UUID(acme))
    )
    clean_coolify = {
        "token_present": True,
        "own_reachable": {"app-acme": True},
        "foreign_reachable": [],
        "errors": [],
    }
    runner.script_health(
        "acme-site",
        **_servers(*TEMPLATE),
        github={
            "token_present": True,
            "own_reachable": {"acme/site": True},
            "foreign_reachable": ["beta/app"],
            "errors": [],
        },
        coolify=clean_coolify,
    )

    checked = await _check(session_client, profile_id)
    checks: list[Any] = [m for m in runner.received if m.type == "health_check"]
    (sent,) = checks
    assert sent.own_repos == ["acme/site"]
    assert sent.foreign_repos == ["beta/app"]
    assert sent.own_apps == ["app-acme"]
    assert sent.foreign_apps == ["app-beta"]
    assert checked["health"]["status"] == "degraded"
    items = _open_drift_items(db)
    assert len(items) == 1
    github_item = json.dumps(items[0][1])
    assert "Beta app" in github_item
    assert "beta/app" in github_item

    runner.script_health(
        "acme-site",
        **_servers(*TEMPLATE),
        github={
            "token_present": True,
            "own_reachable": {"acme/site": True},
            "foreign_reachable": [],
            "errors": [],
        },
        coolify={**clean_coolify, "foreign_reachable": ["app-beta"]},
    )
    again = await _check(session_client, profile_id)
    assert again["health"]["status"] == "degraded"
    coolify_items = [
        json.dumps(payload)
        for _, payload, _ in _open_drift_items(db)
        if "app-beta" in json.dumps(payload)
    ]
    assert len(coolify_items) == 1
    assert "Beta app" in coolify_items[0]


@pytest.mark.req("FR-5.9")
@pytest.mark.wp("P2-10")
@pytest.mark.xfail(strict=True, reason="spec:P2-10")
async def test_health_reports_reachable_authenticated_version(
    dbos: type[DBOS],
    session_client: SessionClient,
    fake_runner: FakeRunnerFactory,
) -> None:
    """T-P2-10-08
    After a check, the profile (Settings > Agents) shows reachable, authenticated, the
    Hermes version and the profile version stamp; the profile's tools listing (Settings,
    read-only) shows the same with each server's allowlist match, and no drift for a
    profile that has exactly the template's servers.
    """
    runner = fake_runner(profiles=["acme-site"])
    project_id = await _project(session_client, "Acme site", [])
    profile_id = str(
        fake_runner.register_profile("acme-site", runner=runner, project_id=uuid.UUID(project_id))
    )
    runner.script_health(
        "acme-site",
        authenticated=True,
        hermes_version="0.9.1",
        profile_version="1.0.0",
        **_servers(*TEMPLATE),
    )

    checked = await _check(session_client, profile_id)
    health = checked["health"]
    assert health["reachable"] is True
    assert health["authenticated"] is True
    assert health["version"] == "0.9.1"
    assert health["profile_version"] == "1.0.0"
    assert health["status"] == "ok"
    assert checked["profile_version"] == "1.0.0"

    tools = await session_client.get(f"/v1/agents/profiles/{profile_id}/tools")
    assert tools.status_code == 200, tools.text
    body = tools.json()
    assert body["reachable"] is True
    assert body["authenticated"] is True
    assert body["hermes_version"] == "0.9.1"
    assert body["profile_version"] == "1.0.0"
    assert sorted(body["allowlist"]) == sorted(TEMPLATE)
    assert sorted(s["name"] for s in body["servers"]) == sorted(TEMPLATE)
    assert all(s["allowed"] for s in body["servers"])
    assert body["extra"] == []
    assert body["missing"] == []
