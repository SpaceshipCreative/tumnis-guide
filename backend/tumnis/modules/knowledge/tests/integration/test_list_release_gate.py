"""The document list keeps the file-text release gate (P1-17 review follow-up, SAF-1): a
file's text is served only once the pipeline has released it (`ready`), in the list as in
a single read; an app text entry's body is always its own."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests._samples import fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import settled, upload

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

UNRELEASED = "Unreleased rate: 900 a day."


def _hold(db: DbUrls, document_id: str) -> None:
    """Back to `pending_scan`, with text in the row, as an upload is before its scan."""
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            "UPDATE documents SET status = 'pending_scan', body_md = %s WHERE id = %s",
            (UNRELEASED, document_id),
        )


@pytest.mark.req("SAF-1", "FR-15.6")
@pytest.mark.wp("P1-17")
async def test_list_hides_unreleased_file_text(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """A file held at `pending_scan` lists without its text, like its single read; a note
    in the same list keeps its body."""
    project = str(extract_env.project_id)
    data = fixture_bytes("brief.docx")
    uploaded = await upload(session_client, extract_env.project_id, "brief.docx", data)
    assert uploaded.status_code == 202, uploaded.text
    file_id = uploaded.json()["id"]
    assert (await settled(session_client, file_id))["status"] == "ready"
    _hold(db, file_id)
    note = await session_client.post(
        "/v1/knowledge/documents/text",
        json={"project_id": project, "title": "Kickoff", "body_md": "Logo first."},
    )
    assert note.status_code == 201, note.text

    listed = await session_client.get("/v1/knowledge/documents", params={"project_id": project})
    assert listed.status_code == 200, listed.text
    bodies = {item["id"]: item["body_md"] for item in listed.json()["items"]}
    assert bodies[file_id] is None
    assert bodies[note.json()["id"]] == "Logo first."
    single = await session_client.get(f"/v1/knowledge/documents/{file_id}")
    assert single.status_code == 200, single.text
    assert single.json()["body_md"] is None
