"""The undo rules (P0-24, R-09, UX 9): which fields a write changed, as JSON, and the row
values that put them back. The integration suite (`test_undo.py`) drives them through the
api; these pin each branch without a database."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from tumnis.modules.tasks.rules import (
    UNDO_FIELDS,
    Label,
    Status,
    change_between,
    restore_values,
    undo_snapshot,
)

NOW = datetime(2026, 3, 9, 12, tzinfo=UTC)
COLUMN = uuid.UUID("01900000-0000-7000-8000-000000000001")
PARENT = uuid.UUID("01900000-0000-7000-8000-000000000002")


def _row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": Status.TODAY,
        "column_id": COLUMN,
        "board_rank": "a0",
        "title": "Write the brief",
        "label": Label.HUMAN,
        "priority": "normal",
        "due_on": date(2026, 3, 10),
        "estimate_minutes": 30,
        "first_action": None,
        "acceptance_criteria": None,
        "parent_id": None,
        "deleted_at": None,
        "rollover_count": 0,
        "completed_at": None,
    }
    row.update(kw)
    return row


@pytest.mark.req("UX 9")
@pytest.mark.wp("P0-24")
def test_snapshot_holds_undoable_fields_as_json() -> None:
    """Ids, dates and enums become strings; `deleted` says whether the row is trashed;
    history fields are left out."""
    snap = undo_snapshot(_row(parent_id=PARENT))
    assert set(snap) == UNDO_FIELDS
    assert snap["column_id"] == str(COLUMN)
    assert snap["parent_id"] == str(PARENT)
    assert snap["due_on"] == "2026-03-10"
    assert snap["status"] == "today"
    assert snap["label"] == Label.HUMAN.value
    assert snap["estimate_minutes"] == 30
    assert snap["deleted"] is False
    assert "rollover_count" not in snap
    assert undo_snapshot(_row(deleted_at=NOW))["deleted"] is True


@pytest.mark.req("UX 9")
@pytest.mark.wp("P0-24")
def test_change_between_keeps_only_changed_fields() -> None:
    before = _row()
    after = _row(status=Status.DONE, title="Write the brief today", rollover_count=3)
    old, new = change_between(before, after)
    assert old == {"status": "today", "title": "Write the brief"}
    assert new == {"status": "done", "title": "Write the brief today"}
    assert change_between(before, _row(rollover_count=5)) == ({}, {})


@pytest.mark.req("UX 9")
@pytest.mark.wp("P0-24")
def test_restore_values_parse_ids_dates_and_trash() -> None:
    """Ids and dates come back typed, `deleted` becomes `deleted_at`, fields outside the
    undo set are ignored, and nothing about completion changes without a status."""
    values = restore_values(
        {
            "column_id": str(COLUMN),
            "parent_id": None,
            "due_on": "2026-03-10",
            "title": "Old title",
            "deleted": True,
            "rollover_count": 9,
        },
        None,
        NOW,
    )
    assert values == {
        "column_id": COLUMN,
        "parent_id": None,
        "due_on": date(2026, 3, 10),
        "title": "Old title",
        "deleted_at": NOW,
    }
    assert restore_values({"deleted": False}, None, NOW) == {"deleted_at": None}


@pytest.mark.req("UX 9")
@pytest.mark.wp("P0-24")
def test_restore_values_recompute_completion_with_status() -> None:
    """Done keeps the completion time (or takes now when there was none); any other status
    clears it."""
    earlier = NOW - timedelta(hours=2)
    assert restore_values({"status": "done"}, earlier, NOW)["completed_at"] == earlier
    assert restore_values({"status": "done"}, None, NOW)["completed_at"] == NOW
    reopened = restore_values({"status": "today"}, earlier, NOW)
    assert reopened == {"status": "today", "completed_at": None}
