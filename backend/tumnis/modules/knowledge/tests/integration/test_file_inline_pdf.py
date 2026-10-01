"""Citations open the cited page (P2-17, FR-15.4; Scott decision 47): a ready PDF is served
inline as `application/pdf`, so the browser's viewer opens `/v1/files/<id>#page=n`. The
P0-16 security headers and `nosniff` stay; every other type is still a download (T-P1-16-10)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest

from tests.meta.test_headers_sweep import security_header_problems
from tumnis.modules.knowledge.tests._samples import fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import settled, upload

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PDF_NAME = "Brand guide \N{EN DASH} 2026.pdf"


@pytest.mark.req("FR-15.4")
@pytest.mark.wp("P2-17")
async def test_pdf_served_inline_for_citations(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient
) -> None:
    """A ready upload whose bytes sniff as a PDF is served `inline; filename*=UTF-8''<name>`
    with `application/pdf`, `nosniff`, the P0-16 security headers and its original bytes. The
    type comes from the sniffed bytes (P1-16), never from the name."""
    env = extract_env
    data = fixture_bytes("text-2p.pdf")
    uploaded = await upload(session_client, env.project_id, PDF_NAME, data)
    document_id = uploaded.json()["id"]
    assert (await settled(session_client, document_id))["status"] == "ready"

    served = await session_client.get(f"/v1/files/{document_id}")

    assert served.status_code == 200, served.text
    assert (
        served.headers["content-disposition"]
        == f"inline; filename*=UTF-8''{quote(PDF_NAME, safe='')}"
    )
    assert served.headers["content-type"] == "application/pdf"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert security_header_problems(served) == []
    assert served.content == data
