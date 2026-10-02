"""Helpers for the P3-02 connection and sync tests. No assertions live here: spec-guard
locks the test bodies.

- `wired(clock, ...)`: the fake OAuth server, connector instances, the clock and the rate
  limiter's sleep swapped into the integrations workflows for one test.
- `ready(ctx, ...)`: a connection created through the api and marked `ok` (no OAuth).
- `connected(ctx, oauth, clock, ...)`: a connection holding a client registration and a
  token set from the fake OAuth server, stored through the api (no workflow).
- `connect_through_routes(...)`: the whole browser flow through the routes: create, start,
  poll for the authorize URL, approve at the fake server, call back.
- `rows(...)`, `text_of(row)`: owner reads and every column of a row as bytes.
- `advancing_sleep(clock)`: a sleep that moves the fixed clock instead of waiting.
- `metrics_app(...)`: an app whose /metrics answers a known bearer token.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx
    from dbos import DBOSClient

    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.modules.integrations.adapters.fake_oauth import FakeOAuthServer

METRICS_TOKEN = "connections-scrape-token"
FAKE_SERVER_URL = "https://mcp.fake.example/mcp"


def rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(query, params).fetchall()


def text_of(row: Mapping[str, Any]) -> bytes:
    """Every column of a row as bytes, for a plaintext search."""
    return b"\x00".join(
        bytes(value) if isinstance(value, (bytes, memoryview)) else str(value).encode()
        for value in row.values()
    )


def advancing_sleep(clock: FixedClock) -> Callable[[float], Awaitable[None]]:
    async def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))
        await asyncio.sleep(0)

    return sleep


@contextmanager
def wired(
    clock: FixedClock,
    *,
    oauth: Any = None,
    sources: Mapping[str, Any] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> Iterator[None]:
    from tumnis.modules.integrations import workflows  # noqa: PLC0415

    previous = workflows.use(oauth=oauth, sources=sources, clock=clock, sleep=sleep)
    try:
        yield
    finally:
        workflows.use(**previous)


async def ready(
    ctx: WorkspaceContext,
    *,
    provider: str = "fake",
    label: str = "Work",
    next_sync_at: datetime | None = None,
    status: str = "ok",
) -> uuid.UUID:
    """A connection made through `create_connection`, then marked `status` (no OAuth),
    next due at `next_sync_at` (None: due now)."""
    from tumnis.modules.integrations import api  # noqa: PLC0415

    made = await api.create_connection(ctx, provider, api.ConnectionSettings(), account_label=label)
    await api.set_connection_status(ctx, made.id, status)
    await api.set_next_sync_at(ctx, made.id, next_sync_at)
    return made.id


async def connected(
    ctx: WorkspaceContext,
    oauth: FakeOAuthServer,
    clock: FixedClock,
    *,
    label: str = "Work",
    expires_in: int = 3600,
) -> uuid.UUID:
    """A connection whose client registration and tokens come from the fake OAuth server,
    stored through the api as the connect workflow stores them."""
    from mcp.shared.auth import OAuthClientMetadata  # noqa: PLC0415
    from pydantic import AnyUrl  # noqa: PLC0415

    from tumnis.modules.integrations import api  # noqa: PLC0415

    made = await api.create_connection(ctx, "fake", api.ConnectionSettings(), account_label=label)
    server = await oauth.discover(FAKE_SERVER_URL)
    client = await oauth.register(
        server,
        OAuthClientMetadata(
            redirect_uris=[AnyUrl("https://tumnis.example.org/v1/connections/oauth/callback")]
        ),
    )
    storage = api.ConnectionTokenStorage(ctx, made.id, clock=clock)
    await api.store_oauth_server(ctx, made.id, server)
    await storage.set_client_info(client)
    await storage.set_tokens(oauth.issue(client.client_id or "", expires_in=expires_in))
    await api.set_connection_status(ctx, made.id, "ok")
    return made.id


async def connect_through_routes(
    client: httpx.AsyncClient,
    dbos_client: DBOSClient,
    oauth: FakeOAuthServer,
    *,
    label: str = "Work",
    code: str = "c1",
) -> tuple[str, httpx.Response]:
    """Create a `fake` connection, start its OAuth, poll for the authorize URL, approve it
    at the fake server and call back; returns (connection id, the callback's response)
    once the connect workflow has finished."""
    made = await client.post("/v1/connections", json={"provider": "fake", "account_label": label})
    made.raise_for_status()
    connection_id: str = made.json()["id"]
    started = await client.post(f"/v1/connections/{connection_id}/oauth/start")
    started.raise_for_status()
    workflow_id: str = started.json()["workflow_id"]
    url = await poll_authorize_url(client, connection_id)
    state = parse_qs(urlsplit(url).query)["state"][0]
    approved = oauth.approve(url, code=code)
    callback = await client.get(
        "/v1/connections/oauth/callback", params={"code": approved, "state": state}
    )
    await wait_for(dbos_client, workflow_id)
    return connection_id, callback


async def poll_authorize_url(
    client: httpx.AsyncClient, connection_id: str, *, timeout_s: float = 20
) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        answer = await client.get(f"/v1/connections/{connection_id}/oauth/url")
        answer.raise_for_status()
        url: str | None = answer.json().get("authorize_url")
        if url:
            return url
        if loop.time() > deadline:
            raise TimeoutError(f"no authorize URL for {connection_id} after {timeout_s} s")
        await asyncio.sleep(0.05)


async def wait_for(dbos_client: DBOSClient, workflow_id: str, *, timeout_s: float = 30) -> str:
    """The workflow's status once it left pending and enqueued."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        status = (
            await asyncio.to_thread(dbos_client.retrieve_workflow(workflow_id).get_status)
        ).status
        if status not in {"PENDING", "ENQUEUED"}:
            return str(status)
        if loop.time() > deadline:
            raise TimeoutError(f"{workflow_id} still {status} after {timeout_s} s")
        await asyncio.sleep(0.05)


async def run_sync(workspace_id: uuid.UUID, connection_id: uuid.UUID) -> dict[str, Any]:
    """One `connector_sync` run in this process (the `dbos` fixture's executor)."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.modules.integrations.workflows import connector_sync  # noqa: PLC0415

    with SetWorkflowID(f"test-sync-{uuid.uuid4()}"):
        result: dict[str, Any] = await connector_sync(str(workspace_id), str(connection_id))
    return result


@asynccontextmanager
async def metrics_app(
    db: DbUrls, dbos_sys_db: DbUrls, clock: FixedClock, token_dir: Any
) -> AsyncIterator[httpx.AsyncClient]:
    """The app with METRICS_TOKEN_FILE holding METRICS_TOKEN, and a client on it."""
    import httpx  # noqa: PLC0415

    from tests.fixtures import build_app, settings_for  # noqa: PLC0415

    token_file = token_dir / "metrics_token"
    token_file.write_text(METRICS_TOKEN + "\n")
    app = build_app(settings_for(db, dbos_sys_db, metrics_token_file=str(token_file)), clock)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as http:
        yield http


async def scrape(
    client: httpx.AsyncClient, family: str
) -> dict[tuple[tuple[str, str], ...], float]:
    """One metric family's samples, keyed by their sorted labels."""
    from prometheus_client.parser import text_string_to_metric_families  # noqa: PLC0415

    response = await client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
    response.raise_for_status()
    for found in text_string_to_metric_families(response.text):
        if found.name == family:
            return {tuple(sorted(s.labels.items())): s.value for s in found.samples}
    return {}
