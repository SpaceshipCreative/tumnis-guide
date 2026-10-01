"""A task version bump does not strand a run (FIX-prepare-run; P2-04, P2-05).

`prepare_run` (P2-04) and `close_human_wait` (P2-05) each lock the run's row, read its
task and move the task with `tasks.change_status` at the version they read. A write that
commits between that read and `change_status` (a user's edit, an enrichment status, ...)
used to make `change_status` raise 409 `stale_version`: the step failed, and the run
stayed queued (`prepare_run`) or waiting on a human (`close_human_wait`) for good. Each
test commits such a bump in that window, from another connection, and expects the step
to go through anyway.

The bump can only matter inside the step's transaction (a bump committed before it
starts is read fresh), so the tests make it at the moment `change_status` is called.
They wait for the bump at most `BUMP_WAIT_S`: a step that holds the task's row lock makes
the bump wait until it commits, and the bump then lands on top.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Final

import pytest

from tumnis.core.versioning import StaleVersion
from tumnis.modules.agents.tests.integration._human import ask, human_waits, run_status, started
from tumnis.modules.agents.tests.integration._runs import owner_rows, relay, run_world, wait_until

if TYPE_CHECKING:
    from uuid import UUID

    from dbos import DBOS
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

BUMP_WAIT_S: Final = 1.0

Bumps = list[asyncio.Task[list[tuple[Any, ...]]]]


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


def _bump_before_change_status(monkeypatch: pytest.MonkeyPatch, db: DbUrls, task_id: UUID) -> Bumps:
    """On the first `tasks.change_status` call, another connection bumps the task's version
    (waited for at most BUMP_WAIT_S) before the call goes on; the bump's task, once made."""
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    bumps: Bumps = []
    change_status = tasks.change_status

    async def bumped_change_status(*args: Any, **kwargs: Any) -> Any:
        if not bumps:
            bumps.append(asyncio.create_task(asyncio.to_thread(_bump, db, task_id)))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(bumps[0]), timeout=BUMP_WAIT_S)
        return await change_status(*args, **kwargs)

    monkeypatch.setattr(tasks, "change_status", bumped_change_status)
    return bumps


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

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    run_id = await world.request(task.id)  # no relay beside the test: the run stays queued
    _, before = _task_row(db, task.id)
    bumps = _bump_before_change_status(monkeypatch, db, task.id)

    prepared = await workflows.prepare_run(str(workspace.id), str(run_id))
    assert bumps, "prepare_run never called change_status"
    await bumps[0]

    assert prepared.refusal is None
    assert prepared.status == "running"
    assert owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,)) == [("running",)]
    assert _task_row(db, task.id) == ("in_progress", before + 2)


@pytest.mark.req("FR-5.7")
@pytest.mark.wp("P2-05")
@pytest.mark.xfail(strict=True, raises=StaleVersion, reason="spec:FIX-prepare-run")
async def test_close_human_wait_survives_a_task_version_bump(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write that bumps the task's version while `close_human_wait` runs (after its read
    of the task, before `change_status`) does not leave the run waiting on the human: the
    wait closes and resumes the run, the task is back In progress, and the other write is
    kept."""
    from tumnis.modules.agents import human_flows  # noqa: PLC0415
    from tumnis.modules.agents.review_kinds import QUESTION  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    with human_waits(poll_seconds=1):
        async with relay(db):
            run_id = await started(world, db, task.id)
            asked = await ask(app, world.token(run_id), run_id, "Which footer color?")
            assert asked.status_code == 200, asked.text
            assert await wait_until(lambda: run_status(db, run_id) == "waiting_on_human")
    # The relay has stopped: the step below runs alone, as the human flow would run it.
    status, before = _task_row(db, task.id)
    assert status == "waiting_on_human"
    bumps = _bump_before_change_status(monkeypatch, db, task.id)

    resumed = await human_flows.close_human_wait(str(workspace.id), QUESTION, asked.json()["id"])
    assert bumps, "close_human_wait never called change_status"
    await bumps[0]

    assert resumed is True
    assert _task_row(db, task.id) == ("in_progress", before + 2)
    assert owner_rows(
        db,
        "SELECT count(*) FROM outbox WHERE name = 'run.signal' AND payload->>'run_id' = %s"
        " AND payload->>'kind' = 'resumed'",
        (str(run_id),),
    ) == [(1,)]
