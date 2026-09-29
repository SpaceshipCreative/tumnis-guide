"""API keys on the projects routes (P0-17, FR-14.10, R-28): a key with `tasks:read` reads
projects, a key limited to project P lists only P and gets 404 for any other project, and
no key writes (project writes are session-only in phase 0)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests.fixtures import KeyClientFactory
    from tumnis.modules.projects.tests.conftest import MakeProject

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# Every scope a key can hold (FR-14.10); spelled out, since module tests reach auth only
# through its api.
EVERY_SCOPE = frozenset(
    {
        "tasks:read",
        "tasks:write",
        "context:read",
        "knowledge:write",
        "drafts:write",
        "delegate",
        "ingest",
    }
)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P0-17")
async def test_project_limited_key_lists_only_its_projects(
    app: FastAPI, key_client: KeyClientFactory, make_project: MakeProject
) -> None:
    """A key limited to P: `GET /v1/projects` (archived ones too) lists only P; P's detail
    answers 200 and Q's 404 `not_found`. An unlimited read key lists both."""
    mine = await make_project(name="Mine")
    other = await make_project(name="Other")

    limited = await key_client(frozenset({"tasks:read"}), projects={mine.id})
    async with limited:
        for params in ({}, {"include_archived": "true"}):
            listed = await limited.get("/v1/projects", params=params)
            assert listed.status_code == 200, listed.text
            assert [item["id"] for item in listed.json()["items"]] == [str(mine.id)]
        assert (await limited.get(f"/v1/projects/{mine.id}")).status_code == 200
        hidden = await limited.get(f"/v1/projects/{other.id}")
        assert hidden.status_code == 404, hidden.text
        assert hidden.json()["code"] == "not_found"

    everything = await key_client(frozenset({"tasks:read"}))
    async with everything:
        listed = await everything.get("/v1/projects")
    assert {item["name"] for item in listed.json()["items"]} == {"Mine", "Other"}


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P0-17")
async def test_keys_cannot_write_projects(
    app: FastAPI, key_client: KeyClientFactory, make_project: MakeProject
) -> None:
    """Every project write answers 403 `session_required` to a full-scope key."""
    project = await make_project(name="Keyless")
    client = await key_client(EVERY_SCOPE)
    body = {"version": project.version}
    async with client:
        answers = [
            await client.post("/v1/projects", json={"name": "By key"}),
            await client.patch(f"/v1/projects/{project.id}", json={**body, "goal": "x"}),
            await client.post(f"/v1/projects/{project.id}/archive", json=body),
            await client.post(f"/v1/projects/{project.id}/unarchive", json=body),
            await client.post(f"/v1/projects/{project.id}/reorder", json=body),
        ]
    for answer in answers:
        assert answer.status_code == 403, answer.text
        assert answer.json()["code"] == "session_required"
