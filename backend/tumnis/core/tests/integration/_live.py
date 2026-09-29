"""Harness for the live-socket tests (P0-22). No assertions live here.

- `build_probe_router()`: `POST /v1/live-probe/tasks` inserts a `demo_items` row and marks
  it changed as a `task` (`tumnis.core.live.mark_changed`): the seam until the tasks
  module's writes mark their rows (P0-18).
- `live_app`: `create_app` on the per-test database with the demo and probe routers and
  the `X-Test-Principal` middleware (which also resolves WebSocket handshakes). A sync
  fixture: tests drive it through Starlette's `TestClient`, whose lifespan starts the
  LISTEN hub.
- `receive_within(ws, seconds)`: the next JSON frame, or `TimeoutError`.
- `wait_until_listening(app)`: blocks until the app's hub is listening.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tests.integration._demo import (
    DemoState,
    TestPrincipalMiddleware,
    build_router,
    create_demo_table,
    demo_items,
)

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI
    from starlette.testclient import WebSocketTestSession

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, MasterKeyFile
    from tumnis.core.clock import FixedClock

ORIGIN = "http://testserver"  # TestClient's Host is "testserver"


class ProbeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str


class ProbeOut(BaseModel):
    id: uuid.UUID


def build_probe_router() -> APIRouter:
    router = v1_router("live-probe", prefix="/live-probe", tags=["live-probe"])

    @router.post("/tasks", status_code=201)
    @route_policy(RoutePolicy(auth="session_or_key", idempotent=True))
    async def create_probe_task(body: ProbeIn, session: SessionDep) -> ProbeOut:
        from tumnis.core import live  # noqa: PLC0415

        row_id = (
            await session.execute(
                sa.insert(demo_items).values(title=body.title).returning(demo_items.c.id)
            )
        ).scalar_one()
        live.mark_changed(session, "task", row_id)
        return ProbeOut(id=row_id)

    return router


@pytest.fixture
def live_app(
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
) -> FastAPI:
    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis.app import create_app  # noqa: PLC0415

    create_demo_table(db)
    app = create_app(
        settings=settings_for(db, dbos_sys_db),
        clock=clock,
        extra_routers=[build_router(DemoState()), build_probe_router()],
    )
    app.add_middleware(TestPrincipalMiddleware)
    app.state.rate_limiter = None
    return app


def receive_within(ws: WebSocketTestSession, seconds: float) -> Any:
    """The next JSON frame from `ws`, or TimeoutError after `seconds`. The reader is a
    daemon thread: a frame that never comes cannot hold up the test run."""
    box: list[Any] = []
    done = threading.Event()

    def read() -> None:
        try:
            box.append(ws.receive_json())
        except Exception as exc:  # handed to the caller
            box.append(exc)
        done.set()

    threading.Thread(target=read, daemon=True).start()
    if not done.wait(seconds):
        raise TimeoutError(f"no frame within {seconds} s")
    if isinstance(box[0], Exception):
        raise box[0]
    return box[0]


def wait_until_listening(app: FastAPI, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        hub = getattr(app.state, "live_hub", None)
        if hub is not None and hub.listening:
            return
        time.sleep(0.02)
    raise TimeoutError("the live hub is not listening")
