"""Helpers for the P4-02 stuck tests (FR-10.5). No assertions live here: spec-guard locks
the test bodies, and these helpers adapt to the focus routes, the agents api and DBOS.

agents' tests never import focus (focus builds on agents; the module graph stays acyclic),
so the person's side goes through the app's routes with the signed-in `session_client`:

- `human_task(world, ...)`: a root Human task with a first action, made by the user.
- `tap_stuck(http, db, workspace_id, task_id, at)`: the workspace at Coach, a `check_in_due`
  message on the task (written as the owner, as the focus worker would), and "Stuck" tapped
  on it (`POST /v1/focus/respond`); the stuck focus event's id. `tap_again(http, check_in)`
  answers the same check-in once more.
- `next_step(http)`: `GET /v1/focus/current`'s `next_step` (None when absent).
- `stuck_runs(db, task_id)`, `stuck_workflows(sys_db)`: the task's stuck runs, the
  `handle_stuck` workflows (`stuck:<focus event>`).
- `live_messages(db)`: the `/ws` notifications (`tumnis_live`) sent while inside.
- `stuck_deadline(seconds)`: `handle_stuck`'s wait for the agent's answer, for one test.
- `quiet_stuck_workflows()`: on exit, the `handle_stuck` workflows still parked in `recv`
  are cancelled and woken, so the `dbos` fixture's teardown does not wait them out.
- `RequestRunSpy`: records every `agents.api.request_run` call and passes it on.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import psycopg

from tests._pg import APP, OWNER
from tumnis.modules.agents.tests.integration._runs import owner_rows

if TYPE_CHECKING:
    import httpx
    import pytest

    from tests._pg import DbUrls
    from tumnis.modules.agents.tests.integration._runs import RunWorld

STUCK_TOPIC: Final = "stuck_outcome"
FIRST_ACTION: Final = "Open the invoice template"


async def human_task(world: RunWorld, title: str = "Send the March invoice") -> Any:
    """A root Human task (25 minutes, a first action), made by the workspace's user."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    async with tenant_session(world.ctx) as s:
        return await tasks.create_task(
            s,
            world.ctx.actor,
            tasks.TaskCreate(
                project_id=world.project_id,
                title=title,
                label="human",
                estimate_minutes=25,
                first_action=FIRST_ACTION,
                acceptance_criteria="The client has the invoice",
            ),
            now=world.clock.now(),
        )


def _check_in(db: DbUrls, workspace_id: uuid.UUID, task_id: uuid.UUID, at: datetime) -> uuid.UUID:
    """A `check_in_due` focus event on the task, as the focus worker writes it."""
    event_id = uuid.uuid4()
    with psycopg.connect(db.libpq(OWNER)) as conn:
        conn.execute(
            b"INSERT INTO focus_events (id, workspace_id, task_id, kind, fired_at, level, rule,"
            b" message, dedupe_key, created_by) VALUES (%s, %s, %s, 'check_in_due', %s,"
            b" 'coach', 'Coach \xc2\xb7 check_in_due (25 min cadence)', 'Still on it?', %s,"
            b" 'system')",
            (event_id, workspace_id, task_id, at, f"check_in:{event_id}"),
        )
    return event_id


async def tap_stuck(
    http: httpx.AsyncClient,
    db: DbUrls,
    workspace_id: uuid.UUID,
    task_id: uuid.UUID,
    at: datetime,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Coach, a check-in on the task, and "Stuck" tapped on it; (check-in id, the stuck
    focus event's id)."""
    level = await http.put("/v1/focus/level", json={"level": "coach"})
    level.raise_for_status()
    check_in = _check_in(db, workspace_id, task_id, at)
    await tap_again(http, check_in)
    [(stuck_id,)] = owner_rows(
        db,
        "SELECT id FROM focus_events WHERE kind = 'stuck' AND task_id = %s"
        " ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    )
    return check_in, stuck_id


async def tap_again(http: httpx.AsyncClient, check_in: uuid.UUID) -> None:
    """ "Stuck" on the check-in `check_in` (a second tap answers it again)."""
    answer = await http.post(
        "/v1/focus/respond", json={"event_id": str(check_in), "response": "stuck"}
    )
    answer.raise_for_status()


async def next_step(http: httpx.AsyncClient) -> dict[str, Any] | None:
    answer = await http.get("/v1/focus/current")
    answer.raise_for_status()
    step: dict[str, Any] | None = answer.json().get("next_step")
    return step


def stuck_runs(db: DbUrls, task_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """(id, status, profile_id) of the task's stuck runs, oldest first."""
    return owner_rows(
        db,
        "SELECT id, status, profile_id FROM runs WHERE task_id = %s AND kind = 'stuck'"
        " ORDER BY created_at",
        (task_id,),
    )


def stuck_workflows(sys_db: DbUrls) -> list[str]:
    """The ids of the `handle_stuck` workflows (`stuck:<focus event id>`)."""
    with psycopg.connect(sys_db.libpq(APP)) as conn:
        rows = conn.execute(
            b"SELECT workflow_uuid FROM dbos.workflow_status WHERE workflow_uuid LIKE 'stuck:%%'"
        ).fetchall()
    return sorted(str(workflow_id) for (workflow_id,) in rows)


@contextlib.asynccontextmanager
async def live_messages(db: DbUrls) -> AsyncIterator[list[dict[str, Any]]]:
    """Collects every `tumnis_live` notification (what the api's LiveHub fans out to the
    workspace's `/ws` sockets) while inside."""
    seen: list[dict[str, Any]] = []
    conn = await psycopg.AsyncConnection.connect(db.libpq(OWNER), autocommit=True)
    await conn.execute("LISTEN tumnis_live")

    async def collect() -> None:
        async for note in conn.notifies():
            seen.append(json.loads(note.payload))

    reader = asyncio.create_task(collect())
    try:
        yield seen
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        await conn.close()


@contextlib.contextmanager
def stuck_deadline(seconds: float) -> Iterator[None]:
    """`handle_stuck` waits `seconds` for the agent's answer (R-30), for the test only."""
    from tumnis.modules.agents import api  # noqa: PLC0415

    api.configure_stuck(deadline_seconds=seconds)
    try:
        yield
    finally:
        api.configure_stuck()


@contextlib.asynccontextmanager
async def quiet_stuck_workflows(sys_db: DbUrls) -> AsyncIterator[None]:
    """On exit: every `handle_stuck` workflow still parked in `recv` is cancelled and
    woken (an empty message on its topic), so teardown does not wait out its deadline."""
    from dbos import DBOS  # noqa: PLC0415

    try:
        yield
    finally:
        for workflow_id in stuck_workflows(sys_db):
            with contextlib.suppress(Exception):
                await DBOS.cancel_workflow_async(workflow_id)
                await DBOS.send_async(workflow_id, {}, topic=STUCK_TOPIC)


@dataclass
class RequestRunSpy:
    """Every `agents.api.request_run(task_id, kind, priority=...)` call, passed on."""

    calls: list[tuple[uuid.UUID, str, int | None]] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tumnis.modules.agents import api  # noqa: PLC0415

        real = api.request_run

        async def spy(task_id: uuid.UUID, kind: Any, **kwargs: Any) -> uuid.UUID:
            self.calls.append((task_id, str(getattr(kind, "value", kind)), kwargs.get("priority")))
            return await real(task_id, kind, **kwargs)

        monkeypatch.setattr(api, "request_run", spy)
