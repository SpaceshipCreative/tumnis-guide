"""Raw provider payloads are kept, linked from their records and LZ4-compressed
(P0-12, FR-14.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.tests.integration._integrations import (
    by_id,
    item,
    page,
    rows,
    scalar,
)

if TYPE_CHECKING:
    import uuid

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-14.1")
@pytest.mark.wp("P0-12")
async def test_raw_payload_stored_and_linked(
    app_db: DbUrls, workspace: WorkspaceHandle, connection: uuid.UUID
) -> None:
    """T-P0-12-04
    Each ingested record points at its `raw_payloads` row holding the provider JSON; a raw
    item that maps to a thread and a message links both to the same row.
    """
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import ingest_page  # noqa: PLC0415

    basic = item("msg-1", subject="Invoice", to=["me@example.org"])
    reply = item(
        "msg-2",
        subject="Re: Invoice",
        thread={"id": "thr-1", "subject": "Invoice", "participants": ["client@example.com"]},
    )
    result = await ingest_page(workspace.ctx, connection, ScriptedConnector(), page(basic, reply))
    assert result.raw_stored == 2

    messages = {row["external_id"]: row for row in rows(app_db, "messages", connection)}
    [thread] = rows(app_db, "threads", connection)
    for raw, record in ((basic, messages["msg-1"]), (reply, messages["msg-2"])):
        assert record["raw_payload_id"] is not None
        stored = by_id(app_db, "raw_payloads", record["raw_payload_id"])
        assert stored is not None
        assert stored["payload"] == raw.payload
        assert (stored["record_type"], stored["external_id"]) == ("message", raw.external_id)
        assert stored["connection_id"] == connection
    assert thread["raw_payload_id"] == messages["msg-2"]["raw_payload_id"]
    assert messages["msg-2"]["thread_id"] == thread["id"]


@pytest.mark.req("FR-14.1")
@pytest.mark.wp("P0-12")
async def test_payload_column_uses_lz4(
    app_db: DbUrls, workspace: WorkspaceHandle, connection: uuid.UUID
) -> None:
    """T-P0-12-05
    `pg_attribute.attcompression = 'l'` on raw_payloads.payload; a 200 KB compressible
    payload stored through `store_raw_payloads` reports `pg_column_compression(payload) =
    'lz4'` (read as the owner).
    """
    from tumnis.modules.integrations.api import store_raw_payloads  # noqa: PLC0415

    big = item("big", body="x" * 200_000)
    ids = await store_raw_payloads(workspace.ctx, connection, [big])

    assert (
        scalar(
            app_db,
            "SELECT attcompression FROM pg_attribute "
            "WHERE attrelid = 'raw_payloads'::regclass AND attname = 'payload'",
        )
        == "l"
    )
    assert (
        scalar(
            app_db,
            "SELECT pg_column_compression(payload) FROM raw_payloads WHERE id = %s",
            ids[("message", "big")],
        )
        == "lz4"
    )
