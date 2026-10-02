"""Helpers for the P2-06 delegation tests. No assertions live here: spec-guard locks the
test bodies, and these helpers adapt to the agents api, the fake runner and the outbox.

The world is the phase 2 acceptance world (`tests.acceptance._phase2.arrange_world` with
the master): "Acme site" with its agent on a protocol-2 fake runner, the key its runs
issue task tokens from, and `tumnis-master` with its key. The tests play both agents over
MCP: the master with its key, a child with its run's task token.

- `world(...)`: that world.
- `delegate(world, key, task_id, **extra)`, `wait(world, key, delegation_id, timeout)`: the
  master's tools.
- `child_task(world, token, title, parent_id=None)`: a task a child run creates with its
  task token (`create_task`), AI with a first action and acceptance criteria.
- `project_key_with_delegate(world)`: a key holding `delegate` that belongs to the
  project's agent profile (not the master's); `key_without_delegate(world)`.
- `stop(world, run_id)`: the person stops a run (`cancel_run`); its workflow ends it.
- `start_master_run(world, db)`: a running run of the master profile, supervised by
  `supervise_run` (no visible api starts one yet), so a loop has a master run to stop.
- `run_row(db, run_id)`, `runs_of_task(db, task_id)`, `open_items(db, kind, task_id)`,
  `delegation_rows(db)`: owner reads.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from tests.acceptance._phase2 import arrange_world, tool
from tumnis.modules.agents.tests.integration._runs import owner_rows

if TYPE_CHECKING:
    from tests._mcp import Outcome
    from tests._pg import DbUrls
    from tests.acceptance._phase2 import World
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock


async def world(
    fake_runner: FakeRunnerFactory, workspace: WorkspaceHandle, clock: FixedClock
) -> World:
    return await arrange_world(fake_runner, workspace, clock, master=True)


async def delegate(world: World, key: str, task_id: uuid.UUID, **extra: Any) -> Outcome:
    return await tool(world.runner, key, "delegate_task", {"task_id": str(task_id), **extra})


async def wait(world: World, key: str, delegation_id: uuid.UUID, seconds: int) -> Outcome:
    return await tool(
        world.runner,
        key,
        "wait_for_task",
        {"delegation_id": str(delegation_id), "timeout_seconds": seconds},
    )


async def child_task(
    world: World, token: str, title: str, *, parent_id: uuid.UUID | None = None
) -> Outcome:
    args: dict[str, Any] = {
        "project_id": str(world.project_id),
        "title": title,
        "label": "ai",
        "first_action": "Open the component",
        "acceptance_criteria": "The change works",
    }
    if parent_id is not None:
        args["parent_id"] = str(parent_id)
    return await tool(world.runner, token, "create_task", args)


async def project_key_with_delegate(world: World) -> str:
    """A key with `tasks:read`, `tasks:write` and `delegate`, linked to the project's agent
    profile: the profile's key, not the master's."""
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    key = await auth.create_key(
        world.workspace.ctx,
        auth.KeyIn(
            name="project key with delegate", scopes=["tasks:read", "tasks:write", "delegate"]
        ),
        now=world.clock.now(),
    )
    await agents.set_profile_key(
        world.workspace.ctx, world.profile_id, key.id, now=world.clock.now()
    )
    return key.key


async def key_without_delegate(world: World) -> str:
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    key = await auth.create_key(
        world.workspace.ctx,
        auth.KeyIn(name="key without delegate", scopes=["tasks:read", "tasks:write"]),
        now=world.clock.now(),
    )
    return key.key


async def stop(world: World, run_id: uuid.UUID) -> None:
    """The person stops the run (the run view's Stop)."""
    from tests.acceptance._phase2 import user_ctx  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    await agents.cancel_run(user_ctx(world.workspace), run_id, now=world.clock.now())


async def start_master_run(world: World, db: DbUrls) -> uuid.UUID:
    """A running run of the master profile (kind plan, no task), supervised by
    `supervise_run` under its workflow id, as a run of the master would be."""
    import psycopg  # noqa: PLC0415
    from dbos import DBOS, SetWorkflowID  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415
    from tumnis.modules.agents import workflows  # noqa: PLC0415

    run_id = uuid.uuid4()
    workflow_id = workflows.supervise_workflow_id(run_id, str(DBOS.application_version))
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(
            b"INSERT INTO runs (id, workspace_id, task_id, profile_id, kind, status, started_at,"
            b" workflow_id, correlation_id, created_by)"
            b" VALUES (%s, %s, NULL, %s, 'plan', 'running', now(), %s, %s, 'system')",
            (run_id, world.workspace.id, world.master_profile_id, workflow_id, f"run:{run_id}"),
        )
    with SetWorkflowID(workflow_id):
        await DBOS.start_workflow_async(
            workflows.supervise_run, str(world.workspace.id), str(run_id)
        )
    return run_id


def run_row(db: DbUrls, run_id: uuid.UUID) -> tuple[Any, ...] | None:
    """(status, stop_reason, workflow_id, delegation_id) of the run."""
    found = owner_rows(
        db,
        "SELECT status, stop_reason, workflow_id, delegation_id FROM runs WHERE id = %s",
        (run_id,),
    )
    return found[0] if found else None


def runs_of_task(db: DbUrls, task_id: uuid.UUID) -> list[uuid.UUID]:
    return [r for (r,) in owner_rows(db, "SELECT id FROM runs WHERE task_id = %s", (task_id,))]


def open_items(db: DbUrls, kind: str, task_id: uuid.UUID) -> list[dict[str, Any]]:
    """The open review items of a kind aimed at the task: their payloads."""
    return [
        payload
        for (payload,) in owner_rows(
            db,
            "SELECT payload FROM review_items WHERE kind = %s AND target_id = %s"
            " AND decided_at IS NULL AND deleted_at IS NULL",
            (kind, task_id),
        )
    ]


def delegation_rows(db: DbUrls) -> list[tuple[Any, ...]]:
    """(id, child_task_id, depth) of every delegation, oldest first."""
    return owner_rows(
        db, "SELECT id, child_task_id, depth FROM delegations ORDER BY delegated_at, id"
    )
