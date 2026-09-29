"""A1.5 · PDF with a table to packet, integration part (phase 1 acceptance, committed red
on the phase's first day).

An uploaded PDF is scanned, extracted with exact pages, searchable with its page, and its
passage reaches the task's packet; an infected upload is quarantined. The Playwright part
is frontend/e2e/acceptance/A1.5.spec.ts. Each step carries the spec marker of the work
package that turns it green: upload, scan and extraction P1-16; search and the packet
P1-17 (P1-16's done checklist names steps 1 to 3, but search is in P1-17's scope).

Fixtures: `db`, `dbos`, `minio`, `clamd`, `seed`; the real Docling pipeline; the fixture
`backend/fixtures/extraction/rate-card-table.pdf` (3 pages, a rate table on page 2 with the
row `Senior designer | 160`).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests.acceptance._phase1 import (
    ACME,
    MONDAY_PLAN_TIME,
    chunks_of,
    create_task,
    get_json,
    knowledge_app,
    project_id,
    rows,
    seed_client,
    seed_ctx,
    until_extracted,
    upload,
)

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests._services import ClamdEndpoint, S3Endpoint
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.slow,
    pytest.mark.req("A1.5", "FR-15.2", "FR-15.3", "FR-15.4", "SEC-10"),
]

RATE_CARD = "rate-card-table.pdf"
TABLE_PAGE = 2


async def _uploaded_rate_card(  # noqa: PLR0917
    db: DbUrls,
    seed: SeedResult,
    clock: FixedClock,
    app_factory: Any,
    minio: S3Endpoint,
    clamd: ClamdEndpoint,
) -> tuple[SessionClient, str, dict[str, Any], dict[str, Any]]:
    """Signs in, uploads the rate card to Acme site and waits for extraction: (client,
    project id, upload response, document after extraction)."""
    clock.set(MONDAY_PLAN_TIME)
    http = await seed_client(knowledge_app(app_factory, minio, clamd), clock)
    acme = await project_id(http, ACME)
    accepted = await upload(http, acme, RATE_CARD)
    return http, acme, accepted, await until_extracted(http, accepted["id"])


def _table_chunk(db: DbUrls, document_id: str) -> dict[str, Any]:
    """The chunk holding the rate table row."""
    hits = [c for c in chunks_of(db, document_id) if "Senior designer" in c["text"]]
    assert len(hits) == 1, f"{len(hits)} chunks hold the table row"
    return hits[0]


@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
async def test_pdf_is_scanned_extracted_and_filed(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    seed: SeedResult,
    clock: FixedClock,
    app_factory: Any,
    minio: S3Endpoint,
    clamd: ClamdEndpoint,
) -> None:
    """A1.5 steps 1 and 2
    Given the rate card PDF, when it is uploaded to project `Acme site` through
    `POST /v1/knowledge/documents`, then the answer is 202 with the document in
    `pending_scan`; after `extract_document` the document is `ready`, `tainted`, stored
    under the project folder at `uploads/rate-card-table.pdf`, and the table chunk has
    `page_from = page_to = 2` and a heading path ending in `Rates`.
    """
    _http, _acme, accepted, doc = await _uploaded_rate_card(
        db, seed, clock, app_factory, minio, clamd
    )

    assert accepted["status"] == "pending_scan"
    assert doc["status"] == "ready"
    assert doc["tainted"] is True
    assert doc["path"] == f"uploads/{RATE_CARD}"
    table = _table_chunk(db, doc["id"])
    assert (table["page_from"], table["page_to"]) == (TABLE_PAGE, TABLE_PAGE)
    assert table["heading_path"][-1] == "Rates"


@pytest.mark.wp("P1-17")
@pytest.mark.xfail(strict=True, reason="spec:P1-17")
async def test_table_chunk_is_searchable_with_page(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    seed: SeedResult,
    clock: FixedClock,
    app_factory: Any,
    minio: S3Endpoint,
    clamd: ClamdEndpoint,
) -> None:
    """A1.5 step 3
    Given the extracted rate card, when `GET /v1/knowledge/search?q=senior designer` runs
    for the project, then the table chunk comes first, citing the document title and
    page 2.
    """
    http, acme, _accepted, doc = await _uploaded_rate_card(
        db, seed, clock, app_factory, minio, clamd
    )
    table = _table_chunk(db, doc["id"])

    found = await get_json(http, "/v1/knowledge/search", q="senior designer", project_id=acme)
    hits = found["items"] if isinstance(found, dict) else found

    assert hits, "no search results"
    first = hits[0]
    assert first["chunk_id"] == table["id"]
    assert first["document_id"] == doc["id"]
    assert first["document_title"] == doc["title"]
    assert first["page"] == TABLE_PAGE


@pytest.mark.wp("P1-17")
@pytest.mark.xfail(strict=True, reason="spec:P1-17")
async def test_task_packet_carries_brief_and_table_passage(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    seed: SeedResult,
    clock: FixedClock,
    app_factory: Any,
    minio: S3Endpoint,
    clamd: ClamdEndpoint,
) -> None:
    """A1.5 step 4
    Given the extracted rate card, when task `Quote Acme for the redesign` is created in
    the project, then `GET /v1/tasks/{id}/packet` returns the TaskPacket (`kind = "enrich"`,
    `schema_version = 1`) whose `body.brief` is the project brief and whose `body.passages`
    include the table chunk with `page: 2`.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    http, acme, _accepted, doc = await _uploaded_rate_card(
        db, seed, clock, app_factory, minio, clamd
    )
    table = _table_chunk(db, doc["id"])
    task = await create_task(http, acme, "Quote Acme for the redesign")
    async with tenant_session(seed_ctx(seed)) as s:
        brief = await knowledge.get_brief(uuid.UUID(acme), session=s)

    packet = await get_json(http, f"/v1/tasks/{task['id']}/packet")

    assert packet["kind"] == "enrich"
    assert packet["schema_version"] == 1
    assert packet["body"]["brief"] == brief.body_md
    passages = [p for p in packet["body"]["passages"] if p["chunk_id"] == table["id"]]
    assert len(passages) == 1
    assert passages[0]["page"] == TABLE_PAGE


@pytest.mark.wp("P1-16")
@pytest.mark.xfail(strict=True, reason="spec:P1-16")
async def test_eicar_upload_is_quarantined(  # noqa: PLR0917
    db: DbUrls,
    dbos: Any,
    seed: SeedResult,
    clock: FixedClock,
    app_factory: Any,
    minio: S3Endpoint,
    clamd: ClamdEndpoint,
) -> None:
    """A1.5 step 5
    Given the EICAR test file, when it is uploaded, then the document ends `quarantined`,
    has no chunks, and an `upload.quarantined` audit row exists for it.
    """
    clock.set(MONDAY_PLAN_TIME)
    http = await seed_client(knowledge_app(app_factory, minio, clamd), clock)
    accepted = await upload(http, await project_id(http, ACME), "eicar.txt")

    doc = await until_extracted(http, accepted["id"])

    assert doc["status"] == "quarantined"
    assert chunks_of(db, doc["id"]) == []
    audit = rows(
        db,
        "SELECT action FROM audit_log WHERE action = 'upload.quarantined' AND target_id = %s",
        doc["id"],
    )
    assert len(audit) == 1
