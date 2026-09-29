"""Agent profiles over REST (P1-04, FR-5.1, FR-5.9): registration, one master per
workspace, and the health check through the runner."""

from __future__ import annotations

import asyncio
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


async def _profile(client: SessionClient, profile_id: str) -> dict[str, Any]:
    response = await client.get("/v1/agents/profiles")
    assert response.status_code == 200, response.text
    found = [p for p in response.json()["items"] if p["id"] == profile_id]
    assert len(found) == 1
    item: dict[str, Any] = found[0]
    return item


@pytest.mark.req("FR-5.1", "FR-5.9")
@pytest.mark.wp("P1-04")
async def test_register_profile_and_health(
    dbos: type[DBOS], session_client: SessionClient, fake_runner: FakeRunnerFactory
) -> None:
    """T-P1-04-18
    A session registers the master profile on a connected runner (201, no health yet); a
    second master is refused (409 `master_exists`); `POST .../health-check` answers 202 and
    the check, run through the runner, fills `health` with reachable, authenticated and the
    Hermes version. The runner list shows the runner online with its profiles.
    """
    runner = fake_runner(profiles=["tumnis-master"])
    runner.script_health("tumnis-master", authenticated=True, hermes_version="0.9.1")

    created = await session_client.post(
        "/v1/agents/profiles",
        json={
            "name": "tumnis-master",
            "role": "master",
            "transport": "daemon",
            "runner_id": str(runner.runner_id),
        },
    )
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["role"] == "master"
    assert profile["runner_id"] == str(runner.runner_id)
    assert profile["health"] is None

    second = await session_client.post(
        "/v1/agents/profiles",
        json={
            "name": "second-master",
            "role": "master",
            "transport": "daemon",
            "runner_id": str(runner.runner_id),
        },
    )
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "master_exists"

    check = await session_client.post(f"/v1/agents/profiles/{profile['id']}/health-check")
    assert check.status_code == 202, check.text
    assert check.json()["profile_id"] == profile["id"]

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 20
    while (current := await _profile(session_client, profile["id"]))["health"] is None:
        assert loop.time() < deadline, "the health check never reported"
        await asyncio.sleep(0.1)
    health = current["health"]
    assert health["reachable"] is True
    assert health["authenticated"] is True
    assert health["version"] == "0.9.1"
    assert current["health_checked_at"] is not None
    checks = [m for m in runner.received if m.type == "health_check"]
    assert [m.profile for m in checks] == ["tumnis-master"]

    runners = await session_client.get("/v1/runners")
    assert runners.status_code == 200, runners.text
    (listed,) = runners.json()["items"]
    assert listed["id"] == str(runner.runner_id)
    assert listed["status"] == "online"
    assert listed["profiles"] == ["tumnis-master"]
    assert "token" not in listed


@pytest.mark.req("FR-5.1")
@pytest.mark.wp("P1-04")
async def test_profile_role_and_project_must_match(session_client: SessionClient) -> None:
    """A `project` profile names its project and a `master` profile names none (422
    `invalid_profile`): a project profile without one is never found for any project."""
    project = await session_client.post("/v1/projects", json={"name": "Acme site"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    for body in (
        {"name": "acme-site", "role": "project"},
        {"name": "tumnis-master", "role": "master", "project_id": project_id},
    ):
        refused = await session_client.post(
            "/v1/agents/profiles", json={**body, "transport": "daemon"}
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "invalid_profile"

    made = await session_client.post(
        "/v1/agents/profiles",
        json={"name": "acme-site", "role": "project", "project_id": project_id},
    )
    assert made.status_code == 201, made.text


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_runner_name_of_a_deleted_runner_is_409(
    session_client: SessionClient, db: DbUrls
) -> None:
    """A runner name stays taken after its runner is soft-deleted (the unique index spans
    deleted rows): a new runner with it is 409 `runner_exists`, not a 500."""
    first = await session_client.post("/v1/runners", json={"name": "old-hermes"})
    assert first.status_code == 201, first.text
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute("UPDATE runners SET deleted_at = now() WHERE id = %s", (first.json()["id"],))

    again = await session_client.post("/v1/runners", json={"name": "old-hermes"})
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "runner_exists"
