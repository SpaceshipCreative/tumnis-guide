"""Upload safety (P1-16, SEC-10): an infected upload is quarantined before anything reads
or places it, the type comes from the content and never from the name or the client's
Content-Type, and the 50 MiB limit is enforced while the body streams."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.core.net import NetPolicy
from tumnis.modules.knowledge.rules import MAX_UPLOAD_BYTES
from tumnis.modules.knowledge.tests._samples import fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import scalar, settled, upload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

MIB = 1024 * 1024
UPLOADS = "SELECT count(*) FROM documents WHERE project_id = %s AND source = 'upload'"


async def _folder_files(env: ExtractEnv) -> list[str]:
    """Every file under the project's folder on its location."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with (
        tenant_session(env.ws.ctx) as s,
        knowledge.open_backend(s, env.location_id, net=NetPolicy(mode="self-hosted")) as backend,
    ):
        page = await backend.list(env.folder + "/", None)
    return [item.path for item in page.items]


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
async def test_eicar_quarantined_never_extracted(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """T-P1-16-01
    The EICAR test file uploaded to a project answers 202 `pending_scan` and ends
    `quarantined`: no chunks, nothing handed to the extractor, nothing under the project
    folder, no copy left in the spool, and one `upload.quarantined` audit row for the
    document.
    """
    env = extract_env
    response = await upload(
        session_client, env.project_id, "eicar.txt", fixture_bytes("eicar.txt"), title="Virus"
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["status"] == "pending_scan"

    doc = await settled(session_client, accepted["id"])

    assert doc["status"] == "quarantined"
    assert scalar(db, "SELECT count(*) FROM chunks WHERE document_id = %s", doc["id"]) == 0
    assert env.extractor.calls == []
    assert await _folder_files(env) == []
    assert list(env.dirs.spool.iterdir()) == []
    assert (
        scalar(
            db,
            "SELECT count(*) FROM audit_log WHERE action = 'upload.quarantined' AND target_id = %s",
            doc["id"],
        )
        == 1
    )


TYPE_CASES = {
    # case: (fixture, uploaded as, claimed Content-Type, status, reason, kind)
    "html_named_pdf": (
        "html-named.pdf",
        "invoice.pdf",
        "application/pdf",
        "failed",
        "type_mismatch",
        None,
    ),
    "pdf_named_bin": (
        "rate-card-table.pdf",
        "rate-card.bin",
        "application/pdf",
        "failed",
        "type_mismatch",
        None,
    ),
    "docx_named_docx": (
        "brief.docx",
        "brief.docx",
        "application/octet-stream",
        "ready",
        None,
        "docx",
    ),
}


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
@pytest.mark.parametrize("case", list(TYPE_CASES))
async def test_type_sniffed_from_content(
    case: str,
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    session_client: SessionClient,
    db: DbUrls,
) -> None:
    """T-P1-16-02
    An HTML file named `.pdf` (claiming `application/pdf`) is refused `type_mismatch`; a
    real PDF named `.bin` is refused too, and neither is placed or extracted; a DOCX with
    its right name becomes ready, kind `docx`, stored at `uploads/brief.docx`.
    """
    fixture, name, claimed, status, reason, kind = TYPE_CASES[case]
    env = extract_env
    response = await upload(
        session_client, env.project_id, name, fixture_bytes(fixture), content_type=claimed
    )
    assert response.status_code == 202, response.text

    doc = await settled(session_client, response.json()["id"])

    assert doc["status"] == status
    assert doc["status_reason"] == reason
    files = await _folder_files(env)
    if status == "failed":
        assert files == []
        assert env.extractor.calls == []
    else:
        assert doc["kind"] == kind
        assert doc["path"] == f"uploads/{name}"
        assert files == [f"{env.folder}/uploads/{name}"]


def _multipart(project_id: object, name: str, size: int, sent: list[int]) -> AsyncIterator[bytes]:
    """A multipart body of one `size`-byte file, generated as it is read; `sent[0]` counts
    the bytes handed to the server."""
    boundary = "----tumnis-limit"

    async def body() -> AsyncIterator[bytes]:
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="project_id"\r\n\r\n'
            f"{project_id}\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        sent[0] += len(head)
        yield head
        left = size
        while left > 0:
            chunk = b"a" * min(MIB, left)
            left -= len(chunk)
            sent[0] += len(chunk)
            yield chunk
        tail = f"\r\n--{boundary}--\r\n".encode()
        sent[0] += len(tail)
        yield tail

    return body()


@pytest.mark.req("SEC-10")
@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
async def test_51_mb_refused_50_mb_accepted(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """T-P1-16-03
    A 51 MiB body answers 413 `too_large` after the server has read at most the limit and
    two more megabytes (one 1 MiB chunk past the limit, plus the multipart head): no
    document, nothing left in the spool. A body of exactly
    `MAX_UPLOAD_BYTES` is accepted (202).
    """
    env = extract_env
    headers: dict[str, Any] = {"Content-Type": "multipart/form-data; boundary=----tumnis-limit"}
    sent = [0]
    refused = await session_client.post(
        "/v1/knowledge/documents",
        content=_multipart(env.project_id, "big.bin", 51 * MIB, sent),
        headers=headers,
    )
    assert refused.status_code == 413, refused.text
    assert refused.json()["code"] == "too_large"
    assert sent[0] <= MAX_UPLOAD_BYTES + 2 * MIB
    assert scalar(db, UPLOADS, env.project_id) == 0
    assert list(env.dirs.spool.iterdir()) == []

    sent = [0]
    accepted = await session_client.post(
        "/v1/knowledge/documents",
        content=_multipart(env.project_id, "big.bin", MAX_UPLOAD_BYTES, sent),
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    await settled(session_client, accepted.json()["id"], timeout_s=120)
    assert scalar(db, UPLOADS, env.project_id) == 1
