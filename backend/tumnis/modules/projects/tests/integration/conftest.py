"""Fixtures for the projects integration tests. P2-18: `archive_world`, an `ArchiveWorld`
(`_archive.py`) on the test's workspace, signed-in client and a location under
`tmp_path`, with DBOS running the workflows in this process."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.projects.tests.integration._archive import ArchiveWorld


@pytest.fixture
async def archive_world(  # noqa: PLR0917
    db: DbUrls,
    dbos: type[DBOS],
    workspace: WorkspaceHandle,
    clock: FixedClock,
    session_client: SessionClient,
    tmp_path: Path,
) -> AsyncIterator[ArchiveWorld]:
    from tumnis.modules.projects.tests.integration._archive import ArchiveWorld  # noqa: PLC0415

    world = ArchiveWorld(
        db=db, ws=workspace, clock=clock, client=session_client, root=tmp_path / "location"
    )
    await world.start()
    try:
        yield world
    finally:
        world.close()
