"""integrations DBOS workflows and steps.

P2-18: importing `archive` registers the excerpt steps of the project archive workflows.

P3-02, the sync framework:

- `connector_sync(workspace_id, connection_id)` (`integrations_connector_sync`, on the
  `sync` queue): a begin step (`api.begin_sync`: the connection goes `syncing`, each
  scope's cursor is ready), then one step per page that reads the stored cursor, fetches
  the page within the provider's request limit and persists it (records, the next cursor
  and one `items.ingested`) in one transaction; kill point
  `integrations.sync.page_<n>.persisting` fires inside that transaction, before its
  commit. A raw page is never a step's output: a resumed sync fetches the page again.
  A provider failure (`AdapterUnavailable`, `ReauthRequired`) is an outcome of the page
  step, not an error DBOS retries; the finish step (`api.finish_sync`) records it.
- `connect_oauth(workspace_id, connection_id, redirect_uri, actor)`
  (`integrations_connect_oauth`): prepare (discovery, registration, a consent in flight),
  publish the authorize URL as event `authorize_url`, wait on topic `oauth_callback` for
  the browser's callback (`OAUTH_WAIT_S`), then exchange the code, or leave the
  connection as it was when nobody came back.
- `connector_sync_tick` (`integrations_connector_sync_tick`, every minute on `sync`):
  one `connector_sync` per due connection of every workspace, deduplicated by
  `sync:<connection id>` (return-existing), skipping a connection whose sync is already
  queued or running.

The OAuth port, the connectors, the clock and the limiter's sleep come from `use()`
(tests) or the adapter registry, the system clock and `asyncio.sleep`.
"""

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from dbos import DBOS, SetEnqueueOptions

from tumnis.core import audit, db, faults
from tumnis.core.adapters.errors import AdapterUnavailable
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.integrations import api
from tumnis.modules.integrations import archive as _archive  # noqa: F401
from tumnis.modules.integrations.oauth_port import ADAPTER as OAUTH_ADAPTER
from tumnis.modules.integrations.oauth_port import OAuthPort
from tumnis.modules.integrations.rules import SyncOutcome

TICK_SCHEDULE_NAME: Final = "connector-sync-tick"
TICK_SCHEDULE: Final = "* * * * *"  # plan: the tick runs every minute
OAUTH_WAIT_S: Final = 900  # plan default: past the consent's 10-minute life
MAX_PAGES: Final = 1000  # one sync never pages further (a connector that never ends)
JITTER_S: Final = 30  # plan default: up to 30 s added to a backoff
_ACTIVE: Final = ["ENQUEUED", "PENDING"]
STEP_RETRY: Final[dict[str, Any]] = {
    "retries_allowed": True,
    "max_attempts": 3,
    "interval_seconds": 0.1,
}

Sleep = Callable[[float], Awaitable[None]]

_oauth: Any = None  # None: resolved from the registry on first use
_sources: Mapping[str, Any] | None = None  # provider -> connector instance (tests)
_clock: Clock | None = None
_sleep: Sleep | None = None
_resolved_oauth: OAuthPort | None = None
_net_policy: NetPolicy | None = None  # None: the real OAuth client's default (hosted)


def use(
    *,
    oauth: Any = None,
    sources: Mapping[str, Any] | None = None,
    clock: Clock | None = None,
    sleep: Sleep | None = None,
) -> dict[str, Any]:
    """Swap the OAuth port, the connectors, the clock and the sleep the workflows use
    (tests); returns the previous values, as keywords for the next `use`."""
    global _oauth, _sources, _clock, _sleep  # the workflows' one seam for tests
    previous = {"oauth": _oauth, "sources": _sources, "clock": _clock, "sleep": _sleep}
    _oauth, _sources, _clock, _sleep = oauth, sources, clock, sleep
    return previous


def configure_net_policy(policy: NetPolicy | None) -> None:
    """The worker sets its deployment's SSRF policy, so the real OAuth client reaches a
    self-hosted MCP server on the LAN when the deployment allows it; None restores the
    default."""
    global _net_policy, _resolved_oauth  # set once at worker start
    _net_policy, _resolved_oauth = policy, None


def _oauth_port() -> OAuthPort:
    global _resolved_oauth  # noqa: PLW0603  # built once per process
    if _oauth is not None:
        port: OAuthPort = _oauth
        return port
    if _resolved_oauth is None:
        mode = current_mode()
        deps = {"policy": _net_policy} if mode == "real" and _net_policy is not None else {}
        _resolved_oauth = resolve(OAUTH_ADAPTER, mode, **deps)
    return _resolved_oauth


def _connector(provider: str) -> api.Connector:
    if _sources is not None and provider in _sources:
        connector: api.Connector = _sources[provider]
        return connector
    return api.connector_for(provider)


def _now_clock() -> Clock:
    return _clock or SystemClock()


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


# --- Sync ---------------------------------------------------------------------------------------


@DBOS.step(name="integrations_sync_begin", **STEP_RETRY)
async def sync_begin(workspace_id: str, connection_id: str) -> dict[str, Any]:
    started = await api.begin_sync(
        _ctx(workspace_id),
        UUID(connection_id),
        [api.DEFAULT_SCOPE],
        now=_now_clock().now(),
    )
    return started.model_dump(mode="json")


@DBOS.step(name="integrations_sync_page", **STEP_RETRY)
async def sync_page(
    workspace_id: str, connection_id: str, provider: str, scope: str, page_no: int
) -> dict[str, Any]:
    """Page `page_no` (1-based) of `scope`: from the stored cursor, fetched and persisted
    in one step. Answers `more`, `done`, or the failure outcome."""
    ctx, conn = _ctx(workspace_id), UUID(connection_id)
    cursor, stored = await api.sync_position(ctx, conn, scope)
    if stored >= page_no:  # this page committed before a retry of the step
        return {"outcome": "more" if cursor is not None else "done"}
    connector = _connector(provider)
    clock = _now_clock()
    try:
        page = await api.fetch_page(
            ctx, conn, cursor, connector=connector, clock=clock, sleep=_sleep or asyncio.sleep
        )
    except api.ReauthRequired:
        return {"outcome": SyncOutcome.auth_error.value}
    except AdapterUnavailable:
        return {"outcome": SyncOutcome.transient_error.value}
    async with tenant_session(ctx) as s:
        persisted = await api.persist_page(
            ctx, conn, connector, page, scope=scope, page_no=page_no, at=clock.now(), session=s
        )
        faults.killpoint(f"integrations.sync.page_{page_no}.persisting")
    return {"outcome": "more" if persisted.more else "done", "items": len(persisted.item_ids)}


@DBOS.step(name="integrations_sync_finish", **STEP_RETRY)
async def sync_finish(workspace_id: str, connection_id: str, outcome: str) -> str:
    finished = await api.finish_sync(
        _ctx(workspace_id),
        UUID(connection_id),
        SyncOutcome(outcome),
        now=_now_clock().now(),
        jitter_s=secrets.randbelow(JITTER_S + 1),
    )
    return str(finished.status)


@DBOS.workflow(name=api.SYNC_WORKFLOW)
async def connector_sync(workspace_id: str, connection_id: str) -> dict[str, Any]:
    started = await sync_begin(workspace_id, connection_id)
    if started["status"] != "ready":
        return {"status": "skipped"}
    outcome = SyncOutcome.success.value
    try:
        for scope in started["scopes"]:
            for page_no in range(1, MAX_PAGES + 1):
                result = await sync_page(
                    workspace_id, connection_id, started["provider"], scope, page_no
                )
                if result["outcome"] != "more":
                    break
            if result["outcome"] in {SyncOutcome.transient_error, SyncOutcome.auth_error}:
                outcome = result["outcome"]
                break
    except Exception:  # a step out of retries: the connection must not stay `syncing`
        outcome = SyncOutcome.transient_error.value
    status = await sync_finish(workspace_id, connection_id, outcome)
    return {"status": status}


# --- Connecting through OAuth --------------------------------------------------------------------


@DBOS.step(name="integrations_oauth_prepare", **STEP_RETRY)
async def oauth_prepare(
    workspace_id: str, connection_id: str, redirect_uri: str, workflow_id: str
) -> dict[str, Any]:
    prepared = await api.prepare_oauth(
        _ctx(workspace_id),
        UUID(connection_id),
        oauth=_oauth_port(),
        redirect_uri=redirect_uri,
        workflow_id=workflow_id,
        now=_now_clock().now(),
    )
    return prepared.model_dump(mode="json")


@DBOS.step(name="integrations_oauth_complete", **STEP_RETRY)
async def oauth_complete(workspace_id: str, connection_id: str, pending_id: str, actor: str) -> str:
    status = await api.complete_oauth(
        _ctx(workspace_id),
        UUID(connection_id),
        UUID(pending_id),
        oauth=_oauth_port(),
        clock=_now_clock(),
        actor=actor,
    )
    return status.value


@DBOS.step(name="integrations_oauth_expire", **STEP_RETRY)
async def oauth_expire(workspace_id: str, connection_id: str) -> str:
    return str(await api.expire_oauth(_ctx(workspace_id), UUID(connection_id)))


@DBOS.workflow(name=api.CONNECT_WORKFLOW)
async def connect_oauth(
    workspace_id: str, connection_id: str, redirect_uri: str, actor: str
) -> dict[str, Any]:
    workflow_id = DBOS.workflow_id or ""
    prepared = await oauth_prepare(workspace_id, connection_id, redirect_uri, workflow_id)
    await DBOS.set_event_async(api.AUTHORIZE_URL_EVENT, prepared["authorize_url"])
    message = await DBOS.recv_async(api.OAUTH_TOPIC, timeout_seconds=OAUTH_WAIT_S)
    if (
        not isinstance(message, dict)
        or message.get("outcome") != "accepted"
        or message.get("pending_id") != prepared["pending_id"]
    ):
        return {"status": await oauth_expire(workspace_id, connection_id)}
    status = await oauth_complete(workspace_id, connection_id, prepared["pending_id"], actor)
    return {"status": status}


# --- Scheduled sync -----------------------------------------------------------------------------


@DBOS.step(name="integrations_due_syncs")
async def due_syncs() -> list[tuple[str, str]]:
    """(workspace, connection) for every due connection whose sync is not already queued
    or running."""
    now = _now_clock().now()
    async with db.app_sessionmaker()() as s, s.begin():
        workspaces = [str(w) for w in await audit.workspace_ids(s)]
    due: list[tuple[str, str]] = []
    for workspace_id in workspaces:
        found = await api.due_connections(_ctx(workspace_id), now=now)
        due += [(workspace_id, str(conn)) for conn in found]
    if not due:
        return []
    running = await DBOS.list_workflows_async(
        name=api.SYNC_WORKFLOW, status=_ACTIVE, load_output=False
    )
    busy = {str(f.input["args"][1]) for f in running if f.input and len(f.input["args"]) > 1}
    return [(ws, conn) for ws, conn in due if conn not in busy]


@DBOS.workflow(name=api.TICK_WORKFLOW)
async def connector_sync_tick(scheduled_at: datetime, context: Any) -> None:
    """Every minute on the sync queue: one `connector_sync` per due connection."""
    for workspace_id, connection_id in await due_syncs():
        with SetEnqueueOptions(
            deduplication_id=api.sync_dedup_id(connection_id),
            duplication_policy="return-existing",
        ):
            await DBOS.enqueue_workflow_async(
                api.SYNC_QUEUE, connector_sync, workspace_id, connection_id
            )


def schedules() -> list[Any]:
    """This module's DBOS schedules, applied by the worker after launch."""
    return [
        {
            "schedule_name": TICK_SCHEDULE_NAME,
            "workflow_fn": connector_sync_tick,
            "schedule": TICK_SCHEDULE,
            "queue_name": api.SYNC_QUEUE,
        }
    ]
