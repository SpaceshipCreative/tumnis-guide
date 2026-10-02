"""Helpers for A4.3 (unattended windows, P4-04). No assertions: they arrange the night's
rows in the `workspace` fixture's workspace through the apis and routes P4-04 names, move
the server clock and fire the test ticks, so the WP may adjust them without touching the
locked test bodies.

- Times: Monday 2026-03-09 in New York (EDT): `EVENING` 21:50, `NIGHT` 22:05, `LATER`
  22:15, `RELEASE` Tuesday 08:45 (the first working hour, 09:00, minus 15 minutes).
- `arrange_night(world, http)`: the window (weekdays 22:00 to 06:00), project "Beta app"
  (paused), and the plan's tasks: U1 (green), U2 (tainted: linked to an outside email), U3
  (in the paused project) queued, H1 (Human) refused at queue time; an `Night` with their
  ids and H1's answer.
- `at(http, when)`: `POST /v1/test/clock`; `fire(http, name)`: `POST /v1/test/tick/{name}`.
- `notify_runs(db, workspace)`, `notifications(db, workspace)`, `attempts(db, workspace)`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from tests.acceptance._phase2 import rows, user_ctx

if TYPE_CHECKING:
    import httpx

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.acceptance._phase2 import World
    from tests.fixtures import WorkspaceHandle

EVENING: Final = datetime(2026, 3, 10, 1, 50, tzinfo=UTC)  # Monday 21:50 EDT
NIGHT: Final = datetime(2026, 3, 10, 2, 5, tzinfo=UTC)  # 22:05 EDT
LATER: Final = datetime(2026, 3, 10, 2, 15, tzinfo=UTC)  # 22:15 EDT
RELEASE: Final = datetime(2026, 3, 10, 12, 45, tzinfo=UTC)  # Tuesday 08:45 EDT
DAY: Final = "2026-03-09"
BETA: Final = "Beta app"
UNATTENDED_TICK: Final = "unattended-tick"
RELEASE_TICK: Final = "overnight-release"


@dataclass
class Night:
    u1: uuid.UUID
    u2: uuid.UUID
    u3: uuid.UUID
    h1: uuid.UUID
    h1_queued: httpx.Response
    beta: uuid.UUID


async def at(http: SessionClient, when: datetime) -> None:
    answer = await http.post("/v1/test/clock", json={"time": when.isoformat()})
    answer.raise_for_status()


async def fire(http: SessionClient, name: str) -> httpx.Response:
    return await http.post(f"/v1/test/tick/{name}")


async def _project(world: World, name: str) -> uuid.UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ctx = user_ctx(world.workspace)
    async with tenant_session(ctx) as s:
        made = await projects.create_project(
            s, ctx.actor, projects.ProjectCreate(name=name), now=world.clock.now()
        )
    world.projects[name] = made.id
    return made.id


async def _human_task(world: World, title: str) -> uuid.UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    ctx = user_ctx(world.workspace)
    async with tenant_session(ctx) as s:
        made = await tasks.create_task(
            s,
            ctx.actor,
            tasks.TaskCreate(
                project_id=world.project_id,
                title=title,
                label="human",
                estimate_minutes=30,
                status="today",
            ),
            now=world.clock.now(),
        )
    return uuid.UUID(str(made.id))


async def _queue(http: SessionClient, task_id: uuid.UUID) -> httpx.Response:
    return await http.put(f"/v1/tasks/{task_id}/unattended", json={"queued": True})


async def arrange_night(world: World, http: SessionClient) -> Night:
    window = await http.put(
        "/v1/unattended/window",
        json={
            "project_id": None,
            "window": {"weekdays": [0, 1, 2, 3, 4], "start_local": "22:00", "end_local": "06:00"},
            "version": None,
        },
    )
    window.raise_for_status()
    beta = await _project(world, BETA)
    u1 = await world.ai_task("Fix footer link")
    u2 = await world.ai_task("Answer the footer colours email", tainted=True)
    u3 = await world.ai_task("Update the Beta sitemap", project_id=beta)
    h1 = await _human_task(world, "Call the client")
    for task in (u1, u2, u3):
        (await _queue(http, task)).raise_for_status()
    h1_queued = await _queue(http, h1)
    paused = await http.post(
        "/v1/agents/pause", json={"scope": "project", "project_id": str(beta), "reason": "test"}
    )
    paused.raise_for_status()
    return Night(u1=u1, u2=u2, u3=u3, h1=h1, h1_queued=h1_queued, beta=beta)


def runs_of(db: DbUrls, task_id: uuid.UUID) -> list[dict[str, Any]]:
    return rows(db, "SELECT * FROM runs WHERE task_id = %s ORDER BY created_at, id", task_id)


def notify_runs(db: DbUrls, workspace: WorkspaceHandle) -> list[dict[str, Any]]:
    return rows(
        db,
        "SELECT * FROM runs WHERE workspace_id = %s AND kind = 'notify' ORDER BY created_at, id",
        workspace.id,
    )


def notifications(db: DbUrls, workspace: WorkspaceHandle) -> list[dict[str, Any]]:
    return rows(
        db,
        "SELECT * FROM notifications WHERE workspace_id = %s ORDER BY created_at, id",
        workspace.id,
    )


def attempts(db: DbUrls, workspace: WorkspaceHandle) -> list[dict[str, Any]]:
    return rows(
        db,
        "SELECT * FROM delivery_attempts WHERE workspace_id = %s ORDER BY created_at, id",
        workspace.id,
    )
