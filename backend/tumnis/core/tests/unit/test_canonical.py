"""content_hash and UpsertStats (P0-12): the pure parts of the canonical upsert."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from tumnis.core.canonical import CanonicalRecord


class NoteDemo(CanonicalRecord):
    schema_version: Literal[1] = 1
    record_type: Literal["note"] = "note"
    subject: str


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P0-12")
def test_content_hash_ignores_fetched_at_and_sees_content() -> None:
    """A re-fetch of the same content hashes the same; any content change does not."""
    from tumnis.core.canonical import content_hash  # noqa: PLC0415

    at = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    first = NoteDemo(external_id="m1", fetched_at=at, subject="Invoice")
    refetched = NoteDemo(external_id="m1", fetched_at=at + timedelta(days=1), subject="Invoice")
    edited = NoteDemo(external_id="m1", fetched_at=at, subject="Invoice v2")
    assert len(content_hash(first)) == 32
    assert content_hash(first) == content_hash(refetched)
    assert content_hash(first) != content_hash(edited)


@pytest.mark.req("FR-14.3")
@pytest.mark.wp("P0-12")
def test_upsert_stats_add_up() -> None:
    """Stats from several tables sum field by field."""
    import uuid  # noqa: PLC0415

    from tumnis.core.canonical import UpsertStats  # noqa: PLC0415

    a, b = uuid.uuid4(), uuid.uuid4()
    total = UpsertStats(1, 0, 2, (a,)) + UpsertStats(0, 1, 0, (b,))
    assert total == UpsertStats(inserted=1, updated=1, unchanged=2, changed_ids=(a, b))
