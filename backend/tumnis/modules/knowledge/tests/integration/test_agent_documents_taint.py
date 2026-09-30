"""Documents an agent adds carry its run's taint (P2-08, SAF-1, FR-15.5): `add_document`
from a tainted run writes a tainted document, and every agent-written document is
untrusted, whatever its run."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


def _written(db: DbUrls, document_id: object) -> tuple[bool, str, str] | None:
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(
            "SELECT tainted, trust, created_by FROM documents WHERE id = %s", (document_id,)
        ).fetchone()
    return None if row is None else (bool(row[0]), str(row[1]), str(row[2]))


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
@pytest.mark.xfail(strict=True, reason="spec:P2-08")
async def test_add_document_from_tainted_run_is_tainted(
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-08-06
    `knowledge.api.add_document` called with a tainted run's caller writes a document
    with `tainted=True` and `trust=untrusted`, written by the run's task token (agent
    written). The same call from a clean run's caller is untainted, and still untrusted.
    """
    from tests._mcp import make_world  # noqa: PLC0415
    from tests._taint import Runs, context_item  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    world = await make_world(workspace, clock)
    actor = ActorRef(f"user:{workspace.user_id}")
    ctx = WorkspaceContext(workspace.id, actor)
    runs = Runs(fake_runner, world)
    outside = await world.task(
        "A", title="Summarise the Acme email thread", label="ai", estimate_minutes=None
    )
    item = await context_item(ctx, world.projects["A"], tainted=True)
    async with tenant_session(ctx) as s:
        await tasks.link_context_item(s, actor, outside.id, item, now=clock.now())
    inside = await world.task(
        "A", title="Write up the Acme style notes", label="ai", estimate_minutes=None
    )

    for task_id, tainted in ((outside.id, True), (inside.id, False)):
        run_id = await runs.run_of(task_id)
        caller = await runs.caller(run_id)
        async with tenant_session(caller.principal.workspace_context()) as s:
            added = await knowledge.add_document(
                s,
                caller,
                project_id=world.projects["A"],
                title=f"Agent notes ({'outside' if tainted else 'house'})",
                body_markdown="# Notes\n\nWhat the run found.",
                now=clock.now(),
            )
        assert added.tainted is tainted
        assert added.trust == "untrusted"
        assert _written(db, added.id) == (tainted, "untrusted", str(caller.principal.actor))
        assert str(caller.principal.actor).startswith("task_token:")
