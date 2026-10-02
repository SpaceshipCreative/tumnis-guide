"""Fixtures for the integrations integration tests (P0-12): the core database pointed at
the test database, a `connections` row for the scripted provider in `workspace`, and
(P3-02) the fake OAuth server wired into the integrations workflows."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.tests.integration import _integrations

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.integrations.adapters.fake_oauth import FakeOAuthServer


@pytest.fixture(autouse=True)
def _core_db_on_the_test_database(request: pytest.FixtureRequest) -> None:
    """A test that asks only for `workspace` (or `db`) still reaches tumnis.core.db on its
    own database, not on an earlier test's dropped one. `app_db`, `app` or `dbos` point it
    at the same database again after this runs (autouse goes first)."""
    if "db" not in request.fixturenames:
        return
    from tumnis.core import db as core_db  # noqa: PLC0415

    db: DbUrls = request.getfixturevalue("db")
    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)


@pytest.fixture
async def app_db(db: DbUrls) -> AsyncIterator[DbUrls]:
    async with _integrations.configured(db):
        yield db


@pytest.fixture
def connection(app_db: DbUrls, workspace: WorkspaceHandle) -> uuid.UUID:
    """A `connections` row for the scripted provider (kind email) in `workspace`."""
    return _integrations.new_connection(app_db, workspace.id)


@pytest.fixture
def oauth_server(fakes: Fakes, clock: FixedClock) -> Iterator[FakeOAuthServer]:
    """The fake OAuth server (`fakes.oauth_server`, P3-02), used by the integrations
    workflows for this test with the test clock."""
    from tumnis.modules.integrations.tests.integration._connections import (  # noqa: PLC0415
        wired,
    )

    server: FakeOAuthServer = fakes.oauth_server
    with wired(clock, oauth=server):
        yield server
