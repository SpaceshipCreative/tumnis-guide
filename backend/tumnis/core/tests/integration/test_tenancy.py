"""The workspace context reaches every transaction, and the touch trigger guards updates
(P0-06, ADR-0009). Uses the harness tenant table `tenant_probe` (test templates only)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
@pytest.mark.usefixtures("core_db")
async def test_every_transaction_applies_the_context(db: DbUrls) -> None:
    """T-P0-06-15
    Two `begin()` blocks in one session each see the workspace; after leaving
    `use_workspace` a new transaction sees none.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, current, use_workspace  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    ws = make_workspace(db)
    probe = text("SELECT app.current_workspace_id(), app.current_actor(), count(*) FROM workspaces")

    async with core_db.app_sessionmaker()() as session:
        with use_workspace(WorkspaceContext(ws, SYSTEM_ACTOR)):
            assert current() == WorkspaceContext(ws, SYSTEM_ACTOR)
            async with session.begin():
                assert tuple((await session.execute(probe)).one()) == (ws, "system", 1)
            async with session.begin():
                assert tuple((await session.execute(probe)).one()) == (ws, "system", 1)
        assert current() is None
        async with session.begin():
            assert tuple((await session.execute(probe)).one()) == (None, "system", 0)


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-06")
@pytest.mark.usefixtures("core_db")
async def test_update_bumps_version_and_keeps_tenant(db: DbUrls) -> None:
    """T-P0-06-16
    `UPDATE` through the app role increments `version`, sets `updated_at`, and cannot change
    `workspace_id` or `id`.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415

    ws_a, ws_b = make_workspace(db, "A"), make_workspace(db, "B")
    actor = ActorRef(f"user:{uuid.uuid4()}")
    ctx = WorkspaceContext(ws_a, actor)

    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                text(
                    "INSERT INTO tenant_probe (name) VALUES ('before') "
                    "RETURNING id, workspace_id, version, updated_at, created_by"
                )
            )
        ).one()
    row_id, workspace_id, version, updated_at, created_by = row
    assert (workspace_id, version, created_by) == (ws_a, 1, actor)

    async with tenant_session(ctx) as s:
        after = (
            await s.execute(
                text(
                    "UPDATE tenant_probe SET name = 'after', id = :new_id, workspace_id = :ws_b, "
                    "version = 41 WHERE id = :id "
                    "RETURNING id, workspace_id, version, updated_at, name"
                ),
                {"new_id": uuid.uuid4(), "ws_b": ws_b, "id": row_id},
            )
        ).one()
    assert after.id == row_id
    assert after.workspace_id == ws_a
    assert after.version == version + 1
    assert after.updated_at > updated_at
    assert after.name == "after"


@pytest.mark.req("ADR-0009")
@pytest.mark.wp("P0-06")
@pytest.mark.usefixtures("core_db")
async def test_workspace_fixture_enters_its_context(workspace: WorkspaceHandle) -> None:
    """The `workspace` fixture runs the test inside its workspace: app-role transactions
    see that workspace and its row."""
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.tenancy import current  # noqa: PLC0415

    assert current() == workspace.ctx
    async with core_db.app_sessionmaker()() as session, session.begin():
        seen = await session.execute(text("SELECT id, name, timezone FROM workspaces"))
        assert [tuple(r) for r in seen] == [(workspace.id, "Test", "America/New_York")]
