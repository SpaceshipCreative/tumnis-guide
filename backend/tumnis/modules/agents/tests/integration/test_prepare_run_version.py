"""`prepare_run` survives a task version bump (FIX-prepare-run, P2-04).

`prepare_run` moves the run's task to In progress with `tasks.change_status` at the
version it read. A write that commits between that read and `change_status` (a user's
edit, an enrichment status, ...) used to make `change_status` raise 409 `stale_version`:
the step failed and the run stayed queued for good. The test commits such a bump in that
window, from another connection, and expects the run to start anyway.

The bump can only matter inside `prepare_run`'s transaction (a bump committed before it
starts is read fresh), so the test makes it at the moment `change_status` is called. It
waits for the bump at most `BUMP_WAIT_S`: a `prepare_run` that holds the task's row lock
makes the bump wait until it commits, and the bump then lands on top.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Final

import pytest

from tumnis.modules.agents.tests.integration._runs import owner_rows, run_world

if TYPE_CHECKING:
    from uuid import UUID

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BUMP_WAIT_S: Final = 1.0


def _task_row(db: DbUrls, task_id: UUID) -> tuple[str, int]:
    [(status, version)] = owner_rows(
        db, "SELECT status::text, version FROM tasks WHERE id = %s", (task_id,)
    )
    return str(status), int(version)


def _bump(db: DbUrls, task_id: UUID) -> list[tuple[Any, ...]]:
    """Another writer's commit: the task's version + 1 (blocks while a lock holds the row)."""
    return owner_rows(
        db, "UPDATE tasks SET version = version + 1 WHERE id = %s RETURNING version", (task_id,)
    )


@pytest.mark.req("FR-5.4")
@pytest.mark.wp("P2-04")
async def test_prepare_run_survives_a_task_version_bump(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write that bumps the task's version while `prepare_run` runs (after its read of
    the task, before `change_status`) does not leave the run queued: the run starts, the
    task ends In progress, and the other write is kept."""
    from tumnis.modules.agents import workflows  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    run_id = await world.request(task.id)  # no relay beside the test: the run stays queued
    _, before = _task_row(db, task.id)

    bumps: list[asyncio.Task[list[tuple[Any, ...]]]] = []
    change_status = tasks.change_status

    async def bumped_change_status(*args: Any, **kwargs: Any) -> Any:
        if not bumps:
            bumps.append(asyncio.create_task(asyncio.to_thread(_bump, db, task.id)))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(bumps[0]), timeout=BUMP_WAIT_S)
        return await change_status(*args, **kwargs)

    monkeypatch.setattr(tasks, "change_status", bumped_change_status)

    prepared = await workflows.prepare_run(str(workspace.id), str(run_id))
    assert bumps, "prepare_run never called change_status"
    await bumps[0]

    assert prepared.refusal is None
    assert prepared.status == "running"
    assert owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,)) == [("running",)]
    assert _task_row(db, task.id) == ("in_progress", before + 2)
