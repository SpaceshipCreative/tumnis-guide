"""Serving an uploaded file back (P1-16, SEC-10): always as a download with the original
bytes, never rendered by the browser, and only once the pipeline has released it."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import psycopg
import pytest

from tests._pg import OWNER
from tests.meta.test_headers_sweep import security_header_problems
from tumnis.modules.knowledge.tests._samples import fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import settled, upload

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

NAME = "Rate card \N{EN DASH} Q1.docx"


def _set_status(db: DbUrls, document_id: str, status: str) -> None:
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        conn.execute("UPDATE documents SET status = %s WHERE id = %s", (status, document_id))


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
async def test_attachment_and_nosniff(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """T-P1-16-10
    A ready upload is served as `attachment; filename*=UTF-8''<percent-encoded name>` with
    `application/octet-stream`, `nosniff`, the P0-16 security headers and its original
    bytes. A document that is `pending_scan`, `extracting` or `quarantined` answers 409
    `not_available`, with the same headers.
    """
    env = extract_env
    data = fixture_bytes("brief.docx")
    uploaded = await upload(session_client, env.project_id, NAME, data)
    document_id = uploaded.json()["id"]
    assert (await settled(session_client, document_id))["status"] == "ready"

    served = await session_client.get(f"/v1/files/{document_id}")

    assert served.status_code == 200, served.text
    assert (
        served.headers["content-disposition"]
        == f"attachment; filename*=UTF-8''{quote(NAME, safe='')}"
    )
    assert served.headers["content-type"] == "application/octet-stream"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert security_header_problems(served) == []
    assert served.content == data

    for status in ("pending_scan", "extracting", "quarantined"):
        _set_status(db, document_id, status)
        refused = await session_client.get(f"/v1/files/{document_id}")
        assert refused.status_code == 409, status
        assert refused.json()["code"] == "not_available", status
        assert refused.headers["x-content-type-options"] == "nosniff", status
        assert security_header_problems(refused) == [], status
