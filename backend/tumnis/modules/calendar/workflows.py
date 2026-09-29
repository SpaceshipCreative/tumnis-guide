"""calendar DBOS workflows and steps (P1-09).

- `calendar_connector_sync(workspace_id, connection_id)` on the `sync` queue: refresh the
  access token when it is about to expire (a revoked grant marks the account
  `needs_reauth` and ends the sync), store the window and first cursor, then one step per
  events.list page, each committing the page's events with the next cursor; after page n
  commits, kill point `calendar.sync.page_<n>.committed`. The last step clears the cursor,
  sets `last_sync_at` and emits one `calendar.synced`. A failed sync emits nothing.
- `calendar_oauth_exchange(workspace_id, pending_id)`: the worker's half of the OAuth
  callback (the api makes no outbound call, architecture principle 3): exchange the code,
  list the calendars, store the account and consume the grant in one transaction.
- `calendar_sync_tick`: every 10 minutes (plan default), a sync per connected account of
  every workspace.

Google and the clock come from `use()` (tests) or the adapter registry and the system
clock.
"""

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from tumnis.core import audit, db, faults
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.calendar import api
from tumnis.modules.calendar.adapters.port import GoogleCalendarPort, GrantRevoked
from tumnis.modules.calendar.rules import SYNC_EVERY_MINUTES, needs_refresh
from tumnis.modules.integrations import api as integrations

SYNC_QUEUE: Final = api.SYNC_QUEUE
SYNC_WORKFLOW: Final = api.SYNC_WORKFLOW
EXCHANGE_WORKFLOW: Final = api.EXCHANGE_WORKFLOW
TICK_SCHEDULE_NAME: Final = "calendar-sync-tick"
TICK_SCHEDULE: Final = f"*/{SYNC_EVERY_MINUTES} * * * *"
STEP_RETRY: Final[dict[str, Any]] = {
    "retries_allowed": True,
    "max_attempts": 3,
    "interval_seconds": 0.1,
}

_api: GoogleCalendarPort | None = None  # None: resolved from the registry on first use
_clock: Clock | None = None  # None: the system clock


def use(
    api: GoogleCalendarPort | None = None, clock: Clock | None = None
) -> tuple[GoogleCalendarPort | None, Clock | None]:
    """Swap the Google API and the clock the workflows use (tests); returns the previous."""
    global _api, _clock  # the workflows' one seam for tests
    previous = (_api, _clock)
    _api, _clock = api, clock
    return previous


def _google() -> GoogleCalendarPort:
    global _api  # noqa: PLW0603  # built once per process
    if _api is None:
        _api = resolve("calendar.google", current_mode())
    return _api


def _now_clock() -> Clock:
    return _clock or SystemClock()


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


# --- Sync --------------------------------------------------------------------------------------


@DBOS.step(**STEP_RETRY)
async def begin_sync(workspace_id: str, connection_id: str) -> dict[str, Any]:
    """Token check and the sync's window: {"status": "ready", "window": [start, end]}, or
    {"status": "needs_reauth"} (revoked grant) / {"status": "not_configured"} (no OAuth
    client to refresh with)."""
    ctx, conn = _ctx(workspace_id), UUID(connection_id)
    clock = _now_clock()
    if await api.account_status(ctx, conn) == "needs_reauth":
        return {"status": "needs_reauth"}
    tokens = await api.account_tokens(ctx, conn)
    expires_at = datetime.fromisoformat(tokens.get("expires_at") or "1970-01-01T00:00:00+00:00")
    if needs_refresh(expires_at, clock.now()):
        client = await api.oauth_client(ctx)
        if client is None or not tokens.get("refresh_token"):
            return {"status": "not_configured"}
        try:
            fresh = await _google().refresh(client, refresh_token=tokens["refresh_token"])
        except GrantRevoked:
            await api.mark_needs_reauth(ctx, conn)
            return {"status": "needs_reauth"}
        await api.store_tokens(ctx, conn, fresh)
    start, end = await api.start_sync(ctx, conn, now=clock.now())
    return {"status": "ready", "window": [start.isoformat(), end.isoformat()]}


@DBOS.step(**STEP_RETRY)
async def sync_page(workspace_id: str, connection_id: str, page_no: int) -> bool:
    """Page `page_no` of the sync (the cursor in `sync_state` says which); True when more
    follow."""
    return await api.sync_next_page(
        _ctx(workspace_id), UUID(connection_id), api=_google(), clock=_now_clock()
    )


@DBOS.step(**STEP_RETRY)
async def finish_sync(workspace_id: str, connection_id: str, window: list[str]) -> None:
    start, end = (datetime.fromisoformat(value) for value in window)
    await api.complete_sync(
        _ctx(workspace_id), UUID(connection_id), at=_now_clock().now(), window=(start, end)
    )


@DBOS.workflow(name=SYNC_WORKFLOW)
async def connector_sync(workspace_id: str, connection_id: str) -> dict[str, Any]:
    started = await begin_sync(workspace_id, connection_id)
    if started["status"] != "ready":
        return {"status": started["status"]}
    pages = 0
    more = True
    while more:
        pages += 1
        more = await sync_page(workspace_id, connection_id, pages)
        faults.killpoint(f"calendar.sync.page_{pages}.committed")
    await finish_sync(workspace_id, connection_id, started["window"])
    return {"status": "synced", "pages": pages}


# --- OAuth exchange -----------------------------------------------------------------------------


@DBOS.step(**STEP_RETRY)
async def exchange_grant(workspace_id: str, pending_id: str) -> dict[str, Any]:
    """Exchange the stored code, list the calendars, store the account and consume the
    grant in one transaction. A grant already consumed (a retried step) is a no-op."""
    ctx, pending = _ctx(workspace_id), UUID(pending_id)
    grant = await integrations.read_oauth_grant(ctx, pending)
    if grant is None:
        return {"status": "gone"}
    client = await api.oauth_client(ctx)
    if client is None:
        return {"status": "not_configured"}
    google = _google()
    tokens = await google.exchange_code(
        client,
        code=grant.code,
        code_verifier=grant.code_verifier,
        redirect_uri=grant.redirect_uri,
    )
    calendars = await google.list_calendars(tokens.access_token)
    async with tenant_session(ctx) as s:
        account = await api.connect_account(ctx, tokens, calendars, session=s)
        await integrations.consume_oauth_grant(ctx, pending, session=s)
    return {"status": "connected", "connection_id": str(account.connection_id)}


@DBOS.workflow(name=EXCHANGE_WORKFLOW)
async def oauth_exchange(workspace_id: str, pending_id: str) -> dict[str, Any]:
    return await exchange_grant(workspace_id, pending_id)


# --- Scheduled sync -----------------------------------------------------------------------------


@DBOS.step()
async def connected_accounts() -> list[tuple[str, str]]:
    """(workspace, connection) for every connected Google account."""
    async with db.app_sessionmaker()() as s, s.begin():
        workspaces = [str(w) for w in await audit.workspace_ids(s)]
    found: list[tuple[str, str]] = []
    for workspace_id in workspaces:
        connections = await api.connected_accounts(_ctx(workspace_id))
        found += [(workspace_id, str(conn)) for conn in connections]
    return found


def schedules() -> list[Any]:
    """This module's DBOS schedules, applied by the worker after launch."""
    return [
        {
            "schedule_name": TICK_SCHEDULE_NAME,
            "workflow_fn": sync_tick,
            "schedule": TICK_SCHEDULE,
            "queue_name": SYNC_QUEUE,
        }
    ]


@DBOS.workflow(name="calendar_sync_tick")
async def sync_tick(scheduled_at: datetime, context: Any) -> None:
    """Scheduled `*/10 * * * *` on the sync queue: one sync per connected account, with a
    workflow id per (connection, tick) so a replayed tick starts none twice."""
    for workspace_id, connection_id in await connected_accounts():
        with SetWorkflowID(f"calendar-sync:{connection_id}:{scheduled_at.isoformat()}"):
            await DBOS.enqueue_workflow_async(
                SYNC_QUEUE, connector_sync, workspace_id, connection_id
            )
