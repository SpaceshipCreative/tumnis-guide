"""Editing projects (P0-17, FR-2.1, FR-3.8): PATCH changes what it names and emits
`project.updated`; links are replaced whole; the code-location, name and version rules
answer 422, 409 and 409 with the current project."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _owner(db: DbUrls, query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode()).fetchall()


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
async def test_patch_changes_named_fields_and_replaces_links(
    app: FastAPI, session_client: SessionClient, db: DbUrls
) -> None:
    created = await session_client.post(
        "/v1/projects",
        json={
            "name": "Site",
            "client": "Acme",
            "links": [{"kind": "domain", "value": "acme.example"}],
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["links"] == [{"kind": "domain", "value": "acme.example"}]
    assert project["health"] == "on_track"
    assert project["brief_md"] == ""

    patched = await session_client.patch(
        f"/v1/projects/{project['id']}",
        json={
            "version": project["version"],
            "goal": "Launch",
            "client": None,
            "repo_url": "https://example.com/acme/site.git",
            "links": [{"kind": "person", "value": "ana@acme.example"}],
        },
    )
    assert patched.status_code == 200, patched.text
    out = patched.json()
    assert (out["goal"], out["client"], out["name"]) == ("Launch", None, "Site")
    assert out["repo_url"] == "https://example.com/acme/site.git"
    assert out["links"] == [{"kind": "person", "value": "ana@acme.example"}]
    assert out["version"] == project["version"] + 1

    [(payload,)] = _owner(db, "SELECT payload FROM outbox WHERE name = 'project.updated'")
    assert payload["changed_fields"] == ["client", "goal", "links", "repo_url"]
    assert _owner(db, "SELECT kind, value FROM project_links") == [("person", "ana@acme.example")]

    links_only = await session_client.patch(
        f"/v1/projects/{project['id']}", json={"version": out["version"], "links": []}
    )
    assert links_only.status_code == 200, links_only.text
    assert links_only.json()["links"] == []


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
async def test_edit_rules_answer_problems(app: FastAPI, session_client: SessionClient) -> None:
    first = (await session_client.post("/v1/projects", json={"name": "First"})).json()
    second = (
        await session_client.post("/v1/projects", json={"name": "Second", "code_path": "/srv/s"})
    ).json()
    path = f"/v1/projects/{second['id']}"

    both = await session_client.patch(
        path, json={"version": second["version"], "repo_url": "https://example.com/s.git"}
    )
    assert both.status_code == 422, both.text
    assert both.json()["code"] == "code_location_conflict"

    relative = await session_client.post(
        "/v1/projects", json={"name": "Third", "code_path": "srv/third"}
    )
    assert relative.status_code == 422, relative.text
    assert relative.json()["code"] == "invalid_code_location"

    taken = await session_client.patch(path, json={"version": second["version"], "name": "first"})
    assert taken.status_code == 409, taken.text
    assert taken.json()["code"] == "project_name_taken"
    duplicate = await session_client.post("/v1/projects", json={"name": "FIRST"})
    assert duplicate.status_code == 409, duplicate.text

    no_name = await session_client.patch(path, json={"version": second["version"], "name": None})
    assert no_name.status_code == 422, no_name.text

    stale = await session_client.patch(path, json={"version": second["version"] + 5, "goal": "x"})
    assert stale.status_code == 409, stale.text
    problem = stale.json()
    assert problem["code"] == "stale_version"
    assert problem["current"]["id"] == second["id"]
    assert problem["current"]["version"] == second["version"]

    missing = await session_client.get(f"/v1/projects/{uuid.uuid4()}")
    assert missing.status_code == 404, missing.text

    backwards = await session_client.post(
        f"/v1/projects/{first['id']}/reorder",
        json={"after_id": second["id"], "before_id": first["id"], "version": first["version"]},
    )
    assert backwards.status_code == 422, backwards.text
    assert backwards.json()["code"] == "invalid_rank"
    out_of_order = await session_client.post(
        f"/v1/projects/{second['id']}/reorder",
        json={"after_id": None, "before_id": first["id"], "version": second["version"]},
    )
    assert out_of_order.status_code == 200, out_of_order.text
    listed = await session_client.get("/v1/projects")
    assert [p["name"] for p in listed.json()["items"]] == ["Second", "First"]
    unknown = await session_client.post(
        f"/v1/projects/{first['id']}/reorder",
        json={"after_id": str(uuid.uuid4()), "version": first["version"]},
    )
    assert unknown.status_code == 404, unknown.text


@pytest.mark.req("FR-3.8")
@pytest.mark.wp("P0-17")
async def test_subtask_threshold_falls_back_to_the_workspace(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.versioning import NotFound  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api  # noqa: PLC0415

    made = (await session_client.post("/v1/projects", json={"name": "Threshold"})).json()
    project_id = uuid.UUID(made["id"])
    async with tenant_session(workspace.ctx) as s:
        assert await api.effective_subtask_threshold(s, project_id) == 30
        assert await api.project_exists(s, project_id)
        assert not await api.project_exists(s, uuid.uuid4())
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute("UPDATE projects SET subtask_threshold_min = 45")
    async with tenant_session(workspace.ctx) as s:
        assert await api.effective_subtask_threshold(s, project_id) == 45
        with pytest.raises(NotFound):
            await api.get_policy(s, uuid.uuid4())
        # No relay ran: the brief does not exist yet.
        with pytest.raises(NotFound):
            await knowledge.get_brief(project_id, session=s)
