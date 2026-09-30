"""The project page's Brief rail reads the brief and saves it as a text entry (P0-24,
FR-2.7): `GET /v1/projects/{id}/brief` and `PATCH /v1/knowledge/documents/{id}`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-2.7")
@pytest.mark.wp("P0-24")
async def test_brief_reads_and_saves_through_the_rail_routes(  # noqa: PLR0917  # fixtures
    app: FastAPI,
    db: DbUrls,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    session_client: SessionClient,
    key_client: KeyClientFactory,
) -> None:
    """The brief reads as a `DocumentDTO`; a PATCH with its version replaces the body and
    bumps the version; a stale version is 409 `stale_version` with the current entry; a
    synced file is 409 `not_text`; a key without `knowledge:write` is 403."""
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    async with tenant_session(workspace.ctx) as s:
        project = await projects.create_project(
            s, workspace.ctx.actor, projects.ProjectCreate(name="Acme"), now=clock.now()
        )
        await knowledge.put_text_document(
            s, project.id, title="Acme brief", body_md="Logo refresh", role="brief"
        )
        other = await knowledge.put_text_document(
            s, project.id, title="Style guide", body_md="", role=None
        )
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute("UPDATE documents SET kind = 'file' WHERE id = %s", (other,))

    got = await session_client.get(f"/v1/projects/{project.id}/brief")
    assert got.status_code == 200, got.text
    brief = got.json()
    assert (brief["role"], brief["kind"], brief["body_md"]) == ("brief", "text", "Logo refresh")

    url = f"/v1/knowledge/documents/{brief['id']}"
    saved = await session_client.patch(
        url, json={"body_md": "Logo refresh for Acme", "version": brief["version"]}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["body_md"] == "Logo refresh for Acme"
    assert saved.json()["version"] > brief["version"]

    stale = await session_client.patch(url, json={"body_md": "x", "version": brief["version"]})
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"
    assert stale.json()["current"]["body_md"] == "Logo refresh for Acme"

    not_text = await session_client.patch(
        f"/v1/knowledge/documents/{other}", json={"body_md": "x", "version": 1}
    )
    assert not_text.status_code == 409, not_text.text
    assert not_text.json()["code"] == "not_text"

    reader = await key_client(["context:read"])
    assert (await reader.get(f"/v1/projects/{project.id}/brief")).status_code == 200
    refused = await reader.patch(url, json={"body_md": "x", "version": saved.json()["version"]})
    assert refused.status_code == 403, refused.text
