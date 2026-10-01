"""Knowledge items (P1-17, FR-15.1, FR-15.5, FR-15.6): text entries, uploads and
agent-written items with their trust defaults, versions on every edit, tags, pins and the
trash. Text entries in a project with a folder write through P1-15's note path to
`notes/`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests._samples import fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import rows, settled, upload

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import KeyClientFactory
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


async def _text_entry(
    http: SessionClient, project_id: object | None, title: str, body_md: str
) -> dict[str, Any]:
    body: dict[str, Any] = {"title": title, "body_md": body_md}
    if project_id is not None:
        body["project_id"] = str(project_id)
    made = await http.post("/v1/knowledge/documents/text", json=body)
    assert made.status_code == 201, made.text
    doc: dict[str, Any] = made.json()
    return doc


@pytest.mark.req("FR-15.5", "SAF-1")
@pytest.mark.wp("P1-17")
async def test_trust_defaults_applied_on_create(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    session_client: SessionClient,
    key_client: KeyClientFactory,
    db: DbUrls,
) -> None:
    """T-P1-17-07
    A text entry written in the app is trusted and untainted; an upload is untrusted and
    tainted; an agent-written entry (`create_text_entry(origin="agent")`) is untrusted,
    untainted and labeled `agent` until a person marks it trusted through
    `POST /v1/knowledge/documents/{id}/trust`, which writes one `document.trust_changed`
    audit row and drops the label. An API key cannot mark anything trusted (session only).
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    env = extract_env
    note = await _text_entry(session_client, env.project_id, "Kickoff notes", "Logo first.")
    assert (note["trust"], note["tainted"], note["label"]) == ("trusted", False, None)

    accepted = await upload(
        session_client, env.project_id, "rate-card-table.pdf", fixture_bytes("rate-card-table.pdf")
    )
    assert accepted.status_code == 202, accepted.text
    uploaded = await settled(session_client, accepted.json()["id"])
    assert (uploaded["trust"], uploaded["tainted"]) == ("untrusted", True)

    async with tenant_session(env.ws.ctx) as s:
        written = await knowledge.create_text_entry(
            s, env.project_id, "Agent summary", "The client wants a new logo.", origin="agent"
        )
    assert (written.trust, written.tainted, written.label) == ("untrusted", False, "agent")

    key = await key_client(["knowledge:write", "context:read"])
    refused = await key.post(f"/v1/knowledge/documents/{written.id}/trust", json={"trusted": True})
    assert refused.status_code == 403, refused.text

    marked = await session_client.post(
        f"/v1/knowledge/documents/{written.id}/trust", json={"trusted": True}
    )
    assert marked.status_code == 200, marked.text
    assert (marked.json()["trust"], marked.json()["label"]) == ("trusted", None)
    assert marked.json()["tainted"] is False
    audit = rows(
        db,
        "SELECT action FROM audit_log WHERE action = 'document.trust_changed' AND target_id = %s",
        written.id,
    )
    assert len(audit) == 1


@pytest.mark.req("FR-15.6")
@pytest.mark.wp("P1-17")
async def test_edits_keep_versions(
    extract_env: ExtractEnv, session_client: SessionClient, db: DbUrls
) -> None:
    """T-P1-17-08
    Three writes of a text entry (the create and two edits with the entry's `version`) give
    versions 1 to 3, each readable with its own body from
    `GET /v1/knowledge/documents/{id}/versions`; the entry's note file in the project
    folder's `notes/` holds the latest body. An edit with a stale `version` answers 409
    `stale_version` with the current entry.
    """
    env = extract_env
    doc = await _text_entry(session_client, env.project_id, "Rates", "Senior designer: 150.")
    url = f"/v1/knowledge/documents/{doc['id']}"
    first_version = doc["version"]
    for body in ("Senior designer: 155.", "Senior designer: 160."):
        saved = await session_client.patch(url, json={"body_md": body, "version": doc["version"]})
        assert saved.status_code == 200, saved.text
        doc = saved.json()
    assert doc["body_md"] == "Senior designer: 160."

    versions = await session_client.get(f"{url}/versions")
    assert versions.status_code == 200, versions.text
    assert [(v["version_no"], v["body_md"]) for v in versions.json()] == [
        (1, "Senior designer: 150."),
        (2, "Senior designer: 155."),
        (3, "Senior designer: 160."),
    ]

    files = rows(
        db,
        "SELECT path FROM folder_files WHERE document_id = %s AND deleted_at IS NULL",
        doc["id"],
    )
    assert len(files) == 1
    assert files[0]["path"].startswith(f"{env.folder}/notes/")

    stale = await session_client.patch(url, json={"body_md": "x", "version": first_version})
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "stale_version"
    assert stale.json()["current"]["body_md"] == "Senior designer: 160."


@pytest.mark.req("FR-15.6")
@pytest.mark.wp("P1-17")
async def test_tags_and_pins_persist_and_trash_restores(
    extract_env: ExtractEnv, session_client: SessionClient
) -> None:
    """T-P1-17-09
    Tags and the pin set with `PATCH /v1/knowledge/documents/{id}` survive a reload (the
    document and the project's list); `DELETE` sends the entry to the trash, which hides it
    from the list, from search and from reads (404); `POST .../restore` brings it back to
    all three.
    """
    env = extract_env
    doc = await _text_entry(session_client, env.project_id, "Palette", "Teal and amber swatches.")
    url = f"/v1/knowledge/documents/{doc['id']}"
    listing = f"/v1/knowledge/documents?project_id={env.project_id}"
    search = f"/v1/knowledge/search?q=amber swatches&project_id={env.project_id}"

    async def listed() -> list[str]:
        got = await session_client.get(listing)
        assert got.status_code == 200, got.text
        return [d["id"] for d in got.json()["items"]]

    async def found() -> list[str]:
        got = await session_client.get(search)
        assert got.status_code == 200, got.text
        hits = got.json()
        return [h["document_id"] for h in (hits["items"] if isinstance(hits, dict) else hits)]

    saved = await session_client.patch(
        url, json={"tags": ["brand", "colour"], "pinned": True, "version": doc["version"]}
    )
    assert saved.status_code == 200, saved.text
    reloaded = (await session_client.get(url)).json()
    assert (reloaded["tags"], reloaded["pinned"]) == (["brand", "colour"], True)
    items = (await session_client.get(listing)).json()["items"]
    [mine] = [d for d in items if d["id"] == doc["id"]]
    assert (mine["tags"], mine["pinned"]) == (["brand", "colour"], True)
    assert doc["id"] in await found()

    trashed = await session_client.delete(url)
    assert trashed.status_code == 204, trashed.text
    assert doc["id"] not in await listed()
    assert doc["id"] not in await found()
    assert (await session_client.get(url)).status_code == 404

    restored = await session_client.post(f"{url}/restore")
    assert restored.status_code == 200, restored.text
    assert doc["id"] in await listed()
    assert doc["id"] in await found()
    back = (await session_client.get(url)).json()
    assert (back["tags"], back["pinned"]) == (["brand", "colour"], True)
