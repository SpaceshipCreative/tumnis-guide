"""Canonical upserts: re-sending never duplicates, edits and deletions show on the next
sync, and records another module owns are refused (P0-12, FR-14.1, FR-14.3)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tumnis.modules.integrations.tests.integration._integrations import (
    LATER,
    T0,
    item,
    new_connection,
    page,
    rows,
)

if TYPE_CHECKING:
    import uuid

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

EXTERNAL_IDS = st.sampled_from(["m1", "m2", "m3", "m4", "m5", "m6"])
ITEMS = st.tuples(EXTERNAL_IDS, st.sampled_from(["a", "b"]))
PAGES = st.lists(st.lists(ITEMS, max_size=5), max_size=6)


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P0-12")
@pytest.mark.xfail(strict=True, reason="spec:P0-12")
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(pages=PAGES)
async def test_repeated_ingest_never_duplicates(
    app_db: DbUrls, workspace: WorkspaceHandle, pages: list[list[tuple[str, str]]]
) -> None:
    """T-P0-12-01
    Hypothesis: any sequence of pages with repeated and reordered items (MessageRecords
    whose subject is "a" or "b") leaves one row per (connection, external_id), and each
    row's subject is the last value sent for that id. Each example gets a fresh connection.
    """
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import ingest_page  # noqa: PLC0415

    connection = new_connection(app_db, workspace.id)
    connector = ScriptedConnector()
    last_sent: dict[str, str] = {}
    for items in pages:
        sync_page = page(*(item(external_id, subject=subject) for external_id, subject in items))
        await ingest_page(workspace.ctx, connection, connector, sync_page)
        for external_id, subject in items:
            last_sent[external_id] = subject

    stored = rows(app_db, "messages", connection)
    assert len(stored) == len(last_sent)
    assert {row["external_id"]: row["subject"] for row in stored} == last_sent
    assert all(row["deleted_at"] is None for row in stored)


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P0-12")
@pytest.mark.xfail(strict=True, reason="spec:P0-12")
async def test_provider_edit_shows_on_next_sync_and_bumps_version_once(
    app_db: DbUrls, workspace: WorkspaceHandle, connection: uuid.UUID
) -> None:
    """T-P0-12-02
    An edited item updates its row and version once; an unchanged re-send (even fetched
    later) changes nothing: same version, updated_at and fetched_at.
    """
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import ingest_page  # noqa: PLC0415

    connector = ScriptedConnector()
    first = await ingest_page(
        workspace.ctx, connection, connector, page(item("m1", subject="Invoice"))
    )
    assert (first.inserted, first.updated, first.unchanged) == (1, 0, 0)
    [created] = rows(app_db, "messages", connection)
    assert created["subject"] == "Invoice"
    assert created["version"] == 1

    resent = await ingest_page(
        workspace.ctx, connection, connector, page(item("m1", fetched_at=LATER, subject="Invoice"))
    )
    assert (resent.inserted, resent.updated, resent.unchanged) == (0, 0, 1)
    assert rows(app_db, "messages", connection) == [created]

    edited_item = item("m1", fetched_at=LATER + timedelta(minutes=5), subject="Invoice v2")
    edited = await ingest_page(workspace.ctx, connection, connector, page(edited_item))
    assert (edited.inserted, edited.updated, edited.unchanged) == (0, 1, 0)
    assert edited.changed_ids == [created["id"]]
    [changed] = rows(app_db, "messages", connection)
    assert changed["id"] == created["id"]
    assert changed["subject"] == "Invoice v2"
    assert changed["version"] == 2
    assert changed["fetched_at"] == LATER + timedelta(minutes=5)
    assert changed["content_hash"] != created["content_hash"]

    again = await ingest_page(workspace.ctx, connection, connector, page(edited_item))
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 1)
    assert rows(app_db, "messages", connection) == [changed]


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P0-12")
@pytest.mark.xfail(strict=True, reason="spec:P0-12")
async def test_provider_deletion_soft_deletes_and_reappearance_restores(
    app_db: DbUrls, workspace: WorkspaceHandle, connection: uuid.UUID
) -> None:
    """T-P0-12-03
    `deleted` sets `deleted_at` on that item's row only (once); the item coming back clears
    it on the same row.
    """
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import ingest_page  # noqa: PLC0415

    connector = ScriptedConnector()
    await ingest_page(
        workspace.ctx,
        connection,
        connector,
        page(item("m1", subject="Invoice"), item("m2", subject="Receipt")),
    )
    before = {row["external_id"]: row for row in rows(app_db, "messages", connection)}

    gone = await ingest_page(workspace.ctx, connection, connector, page(deleted=["m1"]))
    assert gone.deleted == 1
    after = {row["external_id"]: row for row in rows(app_db, "messages", connection)}
    assert after["m1"]["deleted_at"] is not None
    assert after["m2"] == before["m2"]

    repeat = await ingest_page(workspace.ctx, connection, connector, page(deleted=["m1"]))
    assert repeat.deleted == 0

    back = await ingest_page(
        workspace.ctx, connection, connector, page(item("m1", fetched_at=LATER, subject="Invoice"))
    )
    assert back.updated == 1
    restored = {row["external_id"]: row for row in rows(app_db, "messages", connection)}
    assert len(restored) == 2
    assert restored["m1"]["id"] == before["m1"]["id"]
    assert restored["m1"]["deleted_at"] is None


@pytest.mark.req("FR-14.1")
@pytest.mark.wp("P0-12")
@pytest.mark.xfail(strict=True, reason="spec:P0-12")
async def test_records_owned_elsewhere_are_refused(
    app_db: DbUrls, workspace: WorkspaceHandle, connection: uuid.UUID
) -> None:
    """T-P0-12-11
    `ingest_page` with an `event` record raises RecordTypeNotOwned (owner calendar) and
    writes nothing; `calendar.api.upsert_events` accepts it. The same holds for a
    `document` record and `knowledge.api.upsert_synced_documents`.
    """
    from tumnis.modules.calendar.api import EventRecord, upsert_events  # noqa: PLC0415
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import (  # noqa: PLC0415
        RawItem,
        RecordTypeNotOwned,
        ingest_page,
    )
    from tumnis.modules.knowledge.api import (  # noqa: PLC0415
        DocumentRecord,
        upsert_synced_documents,
    )

    def to_event(raw: RawItem) -> list[EventRecord]:
        return [
            EventRecord(
                external_id=raw.external_id,
                fetched_at=raw.fetched_at,
                title=raw.payload["title"],
                start_at=T0,
                end_at=T0 + timedelta(minutes=30),
            )
        ]

    def to_document(raw: RawItem) -> list[DocumentRecord]:
        return [
            DocumentRecord(
                external_id=raw.external_id, fetched_at=raw.fetched_at, title=raw.payload["title"]
            )
        ]

    calendar = new_connection(app_db, workspace.id, provider="scripted_calendar", kind="calendar")
    events = ScriptedConnector(kind="calendar", provider="scripted_calendar", mapper=to_event)
    standup = item("evt-1", "event", title="Standup")
    with pytest.raises(RecordTypeNotOwned) as refused:
        await ingest_page(workspace.ctx, calendar, events, page(standup))
    assert (refused.value.record_type, refused.value.owner) == ("event", "calendar")
    assert rows(app_db, "events", calendar) == []
    assert rows(app_db, "raw_payloads", calendar) == []

    stats = await upsert_events(workspace.ctx, calendar, to_event(standup))
    assert stats.inserted == 1
    [event] = rows(app_db, "events", calendar)
    assert (event["title"], event["tainted"]) == ("Standup", False)
    assert event["source"] == "calendar:scripted_calendar"
    again = await upsert_events(workspace.ctx, calendar, to_event(standup))
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 1)

    files = ScriptedConnector(kind="knowledge", provider="scripted", mapper=to_document)
    brief = item("doc-1", "document", title="Brief")
    with pytest.raises(RecordTypeNotOwned) as refused_doc:
        await ingest_page(workspace.ctx, connection, files, page(brief))
    assert (refused_doc.value.record_type, refused_doc.value.owner) == ("document", "knowledge")
    assert rows(app_db, "documents", connection) == []

    docs = await upsert_synced_documents(workspace.ctx, connection, to_document(brief))
    assert docs.inserted == 1
    [document] = rows(app_db, "documents", connection)
    assert (document["title"], document["trust"], document["tainted"]) == (
        "Brief",
        "untrusted",
        True,
    )
