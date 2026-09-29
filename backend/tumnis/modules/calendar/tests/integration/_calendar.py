"""Helpers for the calendar spec tests (P1-09). No assertions live here: spec-guard locks
the test bodies, and this module is where their shapes are built.

- `configured(db)`: tumnis.core.db pointed at the per-test database (no pooling).
- `wired(clock)`: a fresh `FakeGoogleCalendar` and the clock in the calendar workflows.
- `set_oauth_client(ctx)`: the workspace's Google OAuth client settings.
- `connect(ctx, account, now=...)`: a connected Google account ("a" or "b" of the
  recordings) as the OAuth exchange leaves it, through the calendar api.
- `pages(account)`: the recorded events.list pages of an account's primary calendar.
- `rows(db, table, connection_id)`, `outbox(db, name)`, `scalar(db, sql)`: owner reads.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.modules.calendar.adapters.port import CalendarInfo
    from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar

Account = Literal["a", "b"]
T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "google_calendar"
ACCOUNTS: dict[Account, str] = {"a": "avery@example.com", "b": "blake@example.org"}
CLIENT_ID = "client-123.apps.example.com"
CLIENT_SECRET = "client-secret-not-real"


@asynccontextmanager
async def configured(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@contextmanager
def wired(clock: FixedClock) -> Iterator[FakeGoogleCalendar]:
    from tumnis.modules.calendar import workflows  # noqa: PLC0415
    from tumnis.modules.calendar.adapters.fake import FakeGoogleCalendar  # noqa: PLC0415

    fake = FakeGoogleCalendar()
    previous = workflows.use(api=fake, clock=clock)
    try:
        yield fake
    finally:
        workflows.use(*previous)


async def set_oauth_client(ctx: WorkspaceContext) -> None:
    from tumnis.core.settings_store import put_setting  # noqa: PLC0415
    from tumnis.modules.calendar.api import OAUTH_SECTION, GoogleOAuthClient  # noqa: PLC0415

    await put_setting(
        ctx,
        OAUTH_SECTION,
        GoogleOAuthClient(client_id=CLIENT_ID, client_secret=CLIENT_SECRET),
        expected_version=None,
    )


def refresh_token(account: Account) -> str:
    return f"fake-refresh-{account}"


async def connect(
    ctx: WorkspaceContext,
    account: Account,
    *,
    now: datetime = T0,
    expires_in_s: int = 3600,
    also_listed: Sequence[CalendarInfo] = (),
) -> uuid.UUID:
    """Connect (or reconnect) the account; Google lists its primary calendar and
    `also_listed`."""
    from tumnis.modules.calendar import api  # noqa: PLC0415
    from tumnis.modules.calendar.adapters.port import CalendarInfo, TokenSet  # noqa: PLC0415

    email = ACCOUNTS[account]
    tokens = TokenSet(
        access_token=f"fake-access-{account}",
        refresh_token=refresh_token(account),
        expires_at=now + timedelta(seconds=expires_in_s),
        scope="https://www.googleapis.com/auth/calendar.events.readonly",
    )
    calendars = [
        CalendarInfo(id=email, summary=email, primary=True, time_zone="America/New_York"),
        *also_listed,
    ]
    account_out = await api.connect_account(ctx, tokens, calendars)
    return account_out.connection_id


def pages(account: Account) -> list[dict[str, Any]]:
    """The recorded events.list pages of the account's primary calendar, in order."""
    found = sorted((RECORDINGS / "pages").glob(f"account_{account}_page*.json"))
    return [json.loads(path.read_text()) for path in found]


def recording(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((RECORDINGS / "pages" / f"{name}.json").read_text())
    return data


def rows(db: DbUrls, table: str, connection_id: uuid.UUID) -> list[dict[str, Any]]:
    query = sql.SQL("SELECT * FROM {} WHERE connection_id = %s ORDER BY external_id").format(
        sql.Identifier(table)
    )
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(query, (connection_id,)).fetchall()


def outbox(db: DbUrls, name: str) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT * FROM outbox WHERE name = %s ORDER BY occurred_at, id", (name,)
        ).fetchall()


def scalar(db: DbUrls, query: str, *params: Any) -> Any:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(query, params).fetchone()
    return None if row is None else row[0]


def all_rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(query, params).fetchall()


@contextmanager
def outbound_blocked() -> Iterator[None]:
    """No Python socket may connect anywhere but loopback while inside (the database is
    reached through libpq, which pytest-socket does not guard)."""
    import pytest_socket  # noqa: PLC0415

    pytest_socket.socket_allow_hosts(["127.0.0.1", "::1"], allow_unix_socket=True)
    try:
        yield
    finally:
        pytest_socket._remove_restrictions()  # its public enable leaves connect guarded


async def wait_for_workflows(
    client: Any, name: str, *, count: int = 1, timeout_s: float = 20
) -> list[Any]:
    """The workflows called `name` once `count` of them finished (any status but pending,
    enqueued or running); TimeoutError otherwise."""
    import asyncio  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        found = await asyncio.to_thread(client.list_workflows, name=name)
        done = [w for w in found if w.status not in {"PENDING", "ENQUEUED"}]
        if len(done) >= count:
            return done
        if loop.time() > deadline:
            raise TimeoutError(f"{name}: {[w.status for w in found]}")
        await asyncio.sleep(0.05)
