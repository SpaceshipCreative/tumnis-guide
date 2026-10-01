"""Helpers for the Guardrail integration tests (P4-01). No assertions: they arrange the day
through the product's routes and the modules' apis, and read rows as the owner, so the
code under test may change without touching a locked test body.

- `guardrail_day(focus, ...)`: tasks A, B (and C) in the world's project, today's plan
  with A, B, C blocked from 09:00, every item accepted, the level Guardrail, the clock at
  09:00 (A's `block_start` fired) and A In progress.
- `accept_all(focus, day)`: `POST /v1/plan/{day}/accept-all`.
- `detour(focus, title, project_id)`: "Switched" to something else on the latest
  `block_start`, through `POST /v1/focus/respond`.
- `answer_return(focus, decision, version)`: `POST /v1/focus/return`.
- `task_row(focus, task_id)`, `tasks_titled(focus, title)`: the task rows.
- `workflows_named(focus, name, prefix)`: DBOS workflow rows of `name` whose id starts
  with `prefix`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from tests._pg import APP
from tumnis.modules.focus.tests.integration._focus import TUESDAY, at, rows

if TYPE_CHECKING:
    import httpx

    from tumnis.modules.focus.tests.integration._focus import Focus


@dataclass(frozen=True)
class Day:
    a: UUID
    b: UUID
    c: UUID


async def accept_all(focus: Focus, day: date) -> httpx.Response:
    answer = await focus.http.post(f"/v1/plan/{day.isoformat()}/accept-all")
    await focus.settle()
    return answer


async def guardrail_day(
    focus: Focus,
    *,
    project: UUID | None = None,
    first_actions: tuple[str | None, str | None, str | None] = (
        "Open the invoice template",
        "Open the pull request",
        "Open the thread",
    ),
    level: str = "guardrail",
    start_a: bool = True,
) -> Day:
    """A Tuesday at `level` with three accepted plan items, A In progress at 09:00."""
    a = await focus.task("Write Acme invoice", first_action=first_actions[0], project=project)
    b = await focus.task("Review PR 42", first_action=first_actions[1], project=project)
    c = await focus.task("Reply to Bob", first_action=first_actions[2], project=project)
    await focus.advance(at(TUESDAY, "08:30"))
    await focus.publish(
        TUESDAY, [(a, "09:00", "09:30"), (b, "09:30", "09:50"), (c, "10:00", "10:10")]
    )
    await accept_all(focus, TUESDAY)
    await focus.level(level)
    await focus.advance(at(TUESDAY, "09:00"))
    if start_a:
        await focus.move(a, "in_progress")
    return Day(a, b, c)


async def detour(focus: Focus, title: str, project_id: UUID) -> httpx.Response:
    return await focus.respond(
        "block_start", "switched", detour={"title": title, "project_id": str(project_id)}
    )


async def answer_return(focus: Focus, decision: str, version: int) -> httpx.Response:
    answer = await focus.http.post(
        "/v1/focus/return", json={"decision": decision, "version": version}
    )
    await focus.settle()
    return answer


def task_row(focus: Focus, task_id: UUID) -> dict[str, Any]:
    [row] = rows(focus.db, "SELECT * FROM tasks WHERE id = %s", task_id)
    return row


def tasks_titled(focus: Focus, title: str) -> list[dict[str, Any]]:
    return rows(
        focus.db,
        "SELECT * FROM tasks WHERE workspace_id = %s AND title = %s ORDER BY created_at",
        focus.workspace.id,
        title,
    )


def workflows_named(focus: Focus, name: str, prefix: str) -> list[str]:
    with psycopg.connect(focus.sys_db.libpq(APP), row_factory=dict_row) as conn:
        found = conn.execute(
            b"SELECT workflow_uuid FROM dbos.workflow_status"
            b" WHERE name = %s AND workflow_uuid LIKE %s",
            (name, f"{prefix}%"),
        ).fetchall()
    return [str(r["workflow_uuid"]) for r in found]
