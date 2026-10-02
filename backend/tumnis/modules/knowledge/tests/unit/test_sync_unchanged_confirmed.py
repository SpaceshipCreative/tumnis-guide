"""A plan made before the user confirmed deleting an outside file at its source is stale
once the confirmation lands (P3-14 review follow-up, #154, FR-15.12): the apply step leaves
it to the next sync, which deletes the file, instead of only unindexing it."""

import uuid
from typing import Any

import pytest

from tumnis.modules.knowledge.sync import _unchanged

DOC = uuid.uuid4()


def _item(*, delete_confirmed: bool) -> dict[str, Any]:
    prev = {"etag": "e1", "content_hash": "h1", "synced_version": 2, "document_id": str(DOC)}
    local = {"document_id": str(DOC), "version": 2, "trashed": True}
    return {"prev": prev, "local": local | {"delete_confirmed": delete_confirmed}}


def _record(*, delete_confirmed: bool) -> dict[str, Any]:
    return {
        "etag": "e1",
        "content_hash": "h1",
        "synced_version": 2,
        "document_id": DOC,
        "delete_confirmed": delete_confirmed,
    }


DOC_ROW = {"version": 2, "deleted_at": "2026-03-09T12:00:00+00:00"}


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
def test_a_confirmation_since_the_plan_makes_it_stale() -> None:
    planned = _item(delete_confirmed=False)
    assert _unchanged(planned, _record(delete_confirmed=False), DOC_ROW)  # type: ignore[arg-type]
    assert not _unchanged(planned, _record(delete_confirmed=True), DOC_ROW)  # type: ignore[arg-type]
    confirmed = _item(delete_confirmed=True)
    assert _unchanged(confirmed, _record(delete_confirmed=True), DOC_ROW)  # type: ignore[arg-type]
