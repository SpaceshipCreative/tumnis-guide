"""Fixtures for the integrations integration tests (P0-12): the core database pointed at
the test database, and a `connections` row for the scripted provider in `workspace`."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.integrations.tests.integration import _integrations

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle


@pytest.fixture
async def app_db(db: DbUrls) -> AsyncIterator[DbUrls]:
    async with _integrations.configured(db):
        yield db


@pytest.fixture
def connection(app_db: DbUrls, workspace: WorkspaceHandle) -> uuid.UUID:
    """A `connections` row for the scripted provider (kind email) in `workspace`."""
    return _integrations.new_connection(app_db, workspace.id)
