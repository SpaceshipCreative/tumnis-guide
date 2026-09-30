"""Fixtures for the tasks integration tests: tumnis.core.db on the test's own database."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls


@pytest.fixture(autouse=True)
def _core_db_on_the_test_database(request: pytest.FixtureRequest) -> None:
    """A test that asks only for `workspace` (or `db`) still reaches tumnis.core.db on its
    own database, not on an earlier test's dropped one. Fixtures such as `app`, `dbos` or
    `actors` point it at the same database again after this runs (autouse goes first)."""
    if "db" not in request.fixturenames:
        return
    from tumnis.core import db as core_db  # noqa: PLC0415

    db: DbUrls = request.getfixturevalue("db")
    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
