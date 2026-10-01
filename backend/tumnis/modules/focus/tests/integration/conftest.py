"""The focus integration tests' world (P2-15): `focus`, the workspace's user signed in on the
in-process app with the server clock at its start. With `dbos` the in-process worker runs
the focus workflows and `Focus.settle` waits for them; without it (the kill test) a
subprocess worker does. The modules' apis reach the per-test database either way."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.focus.tests.integration._focus import Focus

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock


@pytest.fixture
def focus(  # noqa: PLR0917  # the fixtures the world stands on
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
    request: pytest.FixtureRequest,
) -> Iterator[Focus]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    world = Focus(
        workspace,
        clock,
        db,
        dbos_sys_db,
        session_client,
        in_process="dbos" in request.fixturenames,
    )
    yield world
    world.use_decisions(None)
    from tumnis.modules.focus import workflows  # noqa: PLC0415

    workflows.use()  # the workflow seams back to production defaults for the next test
