"""Which bytes `GET /v1/files/{id}` may serve (P1-16, SEC-10; review on #69): the document
folder keeps one file per document, the current version's. A newer version that is still
being scanned, or was refused, may already have replaced that file, so nothing is served
until it is released; and an older version's bytes are no longer there to serve."""

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


def _add_version(db: DbUrls, document_id: str, status: str) -> str:
    """A second version of the document, as a folder replacement being scanned makes."""
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO document_versions"
            " (workspace_id, created_by, document_id, version_no, content_hash, size, status,"
            " body_md)"  # NOT NULL: '' until extracted, as store.add_version writes it
            " SELECT workspace_id, created_by, document_id, 2, content_hash, size, %s, ''"
            " FROM document_versions WHERE document_id = %s AND version_no = 1"
            " RETURNING id",
            (status, document_id),
        ).fetchone()
    assert row is not None
    return str(row[0])


def _release(db: DbUrls, document_id: str, version_id: str) -> None:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute("UPDATE document_versions SET status = 'ready' WHERE id = %s", (version_id,))
        conn.execute(
            "UPDATE documents SET current_version_id = %s WHERE id = %s", (version_id, document_id)
        )


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_file_is_withheld_while_a_newer_version_is_unreleased(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """The document stays `ready` on version 1 while version 2 is scanned or after it is
    quarantined, but the file at its path may be version 2's: 409 `not_available`, with or
    without `?version=1`."""
    data = fixture_bytes("brief.docx")
    uploaded = await upload(session_client, extract_env.project_id, "brief.docx", data)
    document_id = uploaded.json()["id"]
    assert (await settled(session_client, document_id))["status"] == "ready"
    assert (await session_client.get(f"/v1/files/{document_id}?version=1")).status_code == 200

    _add_version(db, document_id, "quarantined")

    for url in (f"/v1/files/{document_id}", f"/v1/files/{document_id}?version=1"):
        refused = await session_client.get(url)
        assert refused.status_code == 409, url
        assert refused.json()["code"] == "not_available", url


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_only_the_current_version_is_served(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """Once version 2 is current, `?version=1` answers 409 `not_available` (its bytes were
    replaced) and `?version=2` and the unversioned URL serve the file."""
    data = fixture_bytes("brief.docx")
    uploaded = await upload(session_client, extract_env.project_id, "brief.docx", data)
    document_id = uploaded.json()["id"]
    assert (await settled(session_client, document_id))["status"] == "ready"

    _release(db, document_id, _add_version(db, document_id, "pending_scan"))

    old = await session_client.get(f"/v1/files/{document_id}?version=1")
    assert old.status_code == 409
    assert old.json()["code"] == "not_available"
    for url in (f"/v1/files/{document_id}", f"/v1/files/{document_id}?version=2"):
        served = await session_client.get(url)
        assert served.status_code == 200, url
        assert served.content == data, url
