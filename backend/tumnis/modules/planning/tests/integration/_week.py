"""Helpers for the project week view and manual block spec tests (P1-12). No assertions
live here: spec-guard locks the test bodies, and this module is where their shapes are
built.

- `seed_client(app, clock)`: an httpx client signed in (password, then TOTP at the clock's
  time) as the seed user of `fixtures/seed/workspace.yaml`, in the seed workspace.
- `local(day, "HH:MM")`: a New York wall time on `day` as an aware UTC datetime (the seed
  workspace's timezone).
- `iso(at)`: the `Z` form the API answers with.
- `schedule(client, day, task_id, start, end, *, version=None)`: the response to
  `PATCH /v1/plan/{day}/items/{task_id}`.
- `plan_rows(session)`: every `plan_items` row as `(task_id, block_start, block_end)`,
  read as the owner (no row-level security).
- `link_project(ctx, project_id, links)`: sets a project's links through the projects api.
- `add_meetings(ctx, account, meetings)`: busy events `(start, end, attendees)` on one
  calendar account, stored as a sync stores them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any
from uuid import UUID
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._auth import SessionClient
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext

NEW_YORK = ZoneInfo("America/New_York")


def local(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), NEW_YORK).astimezone(UTC)


def iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def seed_client(app: Any, clock: FixedClock) -> SessionClient:
    from tests._auth import (  # noqa: PLC0415
        password_step,
        seed_user,
        session_client_for,
        totp_code,
        totp_step,
    )

    email, password, secret = seed_user()
    client = session_client_for(app)
    first = await password_step(client, email, password)
    first.raise_for_status()
    second = await totp_step(client, first.json()["preauth"], totp_code(secret, clock.now()))
    second.raise_for_status()
    return client


async def schedule(
    client: httpx.AsyncClient,
    day: date,
    task_id: UUID,
    start: datetime,
    end: datetime,
    *,
    version: int | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"block_start": iso(start), "block_end": iso(end)}
    if version is not None:
        body["version"] = version
    return await client.patch(f"/v1/plan/{day.isoformat()}/items/{task_id}", json=body)


async def plan_rows(session: AsyncSession) -> list[tuple[UUID, datetime | None, datetime | None]]:
    from sqlalchemy import text  # noqa: PLC0415

    rows = await session.execute(
        text("SELECT task_id, block_start, block_end FROM plan_items ORDER BY created_at, id")
    )
    return [(row.task_id, row.block_start, row.block_end) for row in rows]


async def link_project(
    ctx: WorkspaceContext, project_id: UUID, links: Sequence[tuple[str, str]]
) -> None:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        project = await projects.get_project(s, project_id)
        patch = projects.ProjectPatch(
            links=[projects.ProjectLinkIn(kind=kind, value=value) for kind, value in links],
            version=project.version,
        )
        await projects.update_project(s, ctx.actor, project_id, patch, project.version)


async def add_meetings(
    ctx: WorkspaceContext,
    account: str,
    meetings: Sequence[tuple[datetime, datetime, Sequence[str]]],
) -> None:
    """Busy events with attendees on one calendar account's connection, stored as a sync
    stores them (`calendar.api.upsert_events`)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.calendar.api import EventRecord, upsert_events  # noqa: PLC0415
    from tumnis.modules.integrations.api import seed_connection  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        connection_id = await seed_connection(s, "calendar", "google_calendar", account)
        records = [
            EventRecord(
                external_id=f"{account}:{start.isoformat()}:{index}",
                fetched_at=start,
                calendar_id=account,
                title=f"Meeting {index} ({account})",
                start_at=start,
                end_at=end,
                attendees=list(attendees),
            )
            for index, (start, end, attendees) in enumerate(meetings)
        ]
        await upsert_events(ctx, connection_id, records, session=s)
