"""The planning integration tests reach the database through the modules' apis
(`tumnis.core.db`) even when they take neither `dbos` nor the app (the kill test: its
worker runs in a subprocess), so every test here points the engines at its own database
first, as the `dbos` fixture does."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls


@pytest.fixture(autouse=True)
def _core_db_on_test_database(db: "DbUrls") -> None:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, pooled=False)
