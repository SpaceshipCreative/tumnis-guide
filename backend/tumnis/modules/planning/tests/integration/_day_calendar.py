"""Helpers for the day calendar spec tests (P1-10). No assertions live here: spec-guard
locks the test bodies, and this module is where their shapes are built.

- `add_events(ctx, account, spans)`: events on one calendar account's connection, through
  `calendar.api.upsert_events` (as a sync stores them). Each span is `(start, end)` or
  `(start, end, busy)` in UTC.
- `utc(value)`: an ISO instant as an aware UTC datetime.
- `day_calendar(client, day)`: the response to `GET /v1/plan/{day}/calendar`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from tumnis.core.tenancy import WorkspaceContext

Span = tuple[datetime, datetime] | tuple[datetime, datetime, bool]
PROVIDER = "google_calendar"


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


async def add_events(ctx: WorkspaceContext, account: str, spans: Sequence[Span]) -> None:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.calendar.api import EventRecord, upsert_events  # noqa: PLC0415
    from tumnis.modules.integrations.api import seed_connection  # noqa: PLC0415

    async with tenant_session(ctx) as s:
        connection_id = await seed_connection(s, "calendar", PROVIDER, account)
        records = [
            EventRecord(
                external_id=f"{account}:{span[0].isoformat()}:{index}",
                fetched_at=span[0],
                calendar_id=account,
                title=f"Meeting {index} ({account})",
                start_at=span[0],
                end_at=span[1],
                busy=span[2] if len(span) == 3 else True,  # (start, end, busy)
            )
            for index, span in enumerate(spans)
        ]
        await upsert_events(ctx, connection_id, records, session=s)


async def day_calendar(client: httpx.AsyncClient, day: date) -> httpx.Response:
    return await client.get(f"/v1/plan/{day.isoformat()}/calendar")
