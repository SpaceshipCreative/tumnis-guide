"""Module flags: per deployment and per workspace (P0-08, Hosted readiness)."""

from __future__ import annotations

import importlib
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

import httpx
import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PROBE_PATH = "/v1/test/calendar-probe"


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@pytest.fixture
def deployment_flags() -> Iterator[None]:
    """Every create_app sets the deployment kill list; forget it after the test."""
    try:
        yield
    finally:
        from tumnis.core.modules import set_deployment_disabled  # noqa: PLC0415

        set_deployment_disabled(frozenset())


def _ctx(workspace_id: uuid.UUID) -> Any:
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    return WorkspaceContext(workspace_id, SYSTEM_ACTOR)


async def _app_with_probe(db: DbUrls, clock: FixedClock, **overrides: Any) -> FastAPI:
    """create_app plus a router guarded by require_module("calendar"), placed first. The
    engines of the previous app are disposed first: create_app points tumnis.core.db at new
    ones, and a pooled connection left behind would be closed by the garbage collector."""
    from fastapi import APIRouter, Depends  # noqa: PLC0415

    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.core.modules import require_module  # noqa: PLC0415

    await core_db.dispose()
    app = create_app(settings=settings_for(db, **overrides), clock=clock)
    router = APIRouter(dependencies=[Depends(require_module("calendar"))])

    @router.get(PROBE_PATH)
    async def calendar_probe() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(router)
    app.router.routes.insert(0, app.router.routes.pop())  # ahead of the shell mount
    return app


async def _get_as(app: FastAPI, workspace_id: uuid.UUID) -> httpx.Response:
    from tumnis.core.tenancy import use_workspace  # noqa: PLC0415

    transport = httpx.ASGITransport(app=app)
    with use_workspace(_ctx(workspace_id)):
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            return await client.get(PROBE_PATH)


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-08")
@pytest.mark.usefixtures("core_db", "deployment_flags")
async def test_disabled_module_hides_its_routes(
    db: DbUrls, clock: FixedClock, master_key_file: MasterKeyFile
) -> None:
    """T-P0-08-13
    Given a route guarded by require_module("calendar") and workspaces A (calendar off)
    and B (no row), A gets 404 problem `not_found` and B gets 200; with
    TUMNIS_DISABLED_MODULES=calendar, B gets 404 too, even with calendar enabled for B.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.modules import set_module_enabled  # noqa: PLC0415

    a, b = make_workspace(db, "A"), make_workspace(db, "B")
    await set_module_enabled(_ctx(a), "calendar", False)

    app = await _app_with_probe(db, clock)
    off = await _get_as(app, a)
    assert off.status_code == 404, off.text
    assert off.headers["content-type"].startswith("application/problem+json")
    assert off.json()["code"] == "not_found"
    on = await _get_as(app, b)
    assert on.status_code == 200, on.text
    assert on.json() == {"ok": True}

    await set_module_enabled(_ctx(b), "calendar", True)
    killed = await _app_with_probe(db, clock, tumnis_disabled_modules="calendar")
    for workspace_id in (a, b):
        response = await _get_as(killed, workspace_id)
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "not_found"


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-08")
async def test_disabled_module_subscribers_are_skipped(
    db: DbUrls, dbos: Any, clock: FixedClock
) -> None:
    """T-P0-08-14
    An event for a workspace with calendar off is not enqueued to calendar.* subscribers;
    another module's subscriber still gets it, and so does calendar's for a workspace that
    has calendar on.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.modules import set_module_enabled  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    # P0-07's outbox and relay, imported by name so this file type-checks before they merge.
    events = importlib.import_module("tumnis.core.events")
    outbox = importlib.import_module("tumnis.core.outbox")

    async def handler(envelope: Any) -> None:
        return None

    events.subscribe("test.ping", name="calendar.p0_08_flag_probe")(handler)
    events.subscribe("test.ping", name="tasks.p0_08_flag_probe")(handler)

    off, on = make_workspace(db, "Off"), make_workspace(db, "On")
    await set_module_enabled(_ctx(off), "calendar", False)
    event_ids = {}
    for workspace_id in (off, on):
        async with tenant_session(_ctx(workspace_id)) as session:
            event_ids[workspace_id] = await outbox.emit(
                session, events.TestPingV1(note="flags"), occurred_at=clock.now()
            )
    while await events.relay_once():
        pass

    async def status(workspace_id: uuid.UUID, subscriber: str) -> Any:
        return await dbos.get_workflow_status_async(f"{event_ids[workspace_id]}:{subscriber}")

    assert await status(off, "calendar.p0_08_flag_probe") is None
    assert await status(off, "tasks.p0_08_flag_probe") is not None
    assert await status(on, "calendar.p0_08_flag_probe") is not None
    assert await status(on, "tasks.p0_08_flag_probe") is not None


@pytest.mark.req("Hosted readiness")
@pytest.mark.wp("P0-08")
@pytest.mark.usefixtures("core_db")
async def test_required_modules_cannot_be_disabled(db: DbUrls) -> None:
    """T-P0-08-15
    Turning off tasks (or auth, or projects) for a workspace raises ModuleRequired and
    leaves it on; a deployment kill list naming tasks is refused the same way.
    """
    from tests.fixtures import make_workspace, settings_for  # noqa: PLC0415
    from tumnis.core.modules import (  # noqa: PLC0415
        REQUIRED_MODULES,
        ModuleRequired,
        deployment_disabled,
        enabled,
        set_module_enabled,
    )

    ws = make_workspace(db)
    assert {"auth", "projects", "tasks"} <= REQUIRED_MODULES
    for module in sorted(REQUIRED_MODULES):
        with pytest.raises(ModuleRequired):
            await set_module_enabled(_ctx(ws), module, False)
        assert await enabled(module, ws)
    with pytest.raises(ModuleRequired):
        deployment_disabled(settings_for(db, tumnis_disabled_modules="calendar, tasks"))
    assert deployment_disabled(settings_for(db, tumnis_disabled_modules="calendar, github")) == {
        "calendar",
        "github",
    }


@pytest.mark.req("SEC-3")
@pytest.mark.wp("P0-08")
@pytest.mark.usefixtures("core_db")
async def test_module_toggle_is_audited(db: DbUrls, clock: FixedClock) -> None:
    """Switching a module off and on writes one `module.toggled` row each, in the
    workspace, with the module, the new state and the clock's time."""
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.modules import set_module_enabled  # noqa: PLC0415
    from tumnis.core.tests.integration._audit import owner_rows  # noqa: PLC0415

    ws = make_workspace(db)
    await set_module_enabled(_ctx(ws), "calendar", False, clock=clock)
    clock.advance(minutes=1)
    await set_module_enabled(_ctx(ws), "calendar", True, clock=clock)

    rows = owner_rows(
        db,
        "SELECT workspace_id, action, details, occurred_at FROM audit_log ORDER BY seq",
    )
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (ws, "module.toggled", {"module": "calendar", "enabled": False}),
        (ws, "module.toggled", {"module": "calendar", "enabled": True}),
    ]
    assert rows[1][3] == clock.now()
