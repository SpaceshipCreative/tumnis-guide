"""Primary keys are PostgreSQL 18 `uuidv7()` values that sort by creation time (ADR-0005)."""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ROWS = 1_000


@pytest.mark.req("ADR-0005")
@pytest.mark.wp("P0-06")
def test_uuidv7_ids_sort_by_creation_time(db: DbUrls) -> None:
    """T-P0-06-09
    1,000 sequential inserts from one connection: `ORDER BY id` equals insertion order and
    `uuid_extract_timestamp(id)` is non-decreasing.
    """
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        inserted = []
        for n in range(ROWS):
            row = conn.execute(
                "INSERT INTO workspaces (name) VALUES (%s) RETURNING id", (f"ws-{n:04d}",)
            ).fetchone()
            assert row is not None
            inserted.append(row[0])

        by_id = [r[0] for r in conn.execute("SELECT id FROM workspaces ORDER BY id")]
        assert by_id == inserted

        versions = {r[0] for r in conn.execute("SELECT uuid_extract_version(id) FROM workspaces")}
        assert versions == {7}

        stamps = [
            r[0]
            for r in conn.execute("SELECT uuid_extract_timestamp(id) FROM workspaces ORDER BY name")
        ]
        assert len(stamps) == ROWS
        assert all(a <= b for a, b in pairwise(stamps))
