"""Fixtures for the search tests (P0-20); the shared ones (clock, db, dbos, workspace,
two_workspaces, seed, load_fixture, query_counter, app, session_client) come from
backend/tests/fixtures.

- `search_db`: tumnis.core.db pointed at the per-test database (no pooling), for tests
  that call `search.api` or its subscribers without the app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls


@pytest.fixture
def search_db(db: DbUrls) -> DbUrls:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    return db
