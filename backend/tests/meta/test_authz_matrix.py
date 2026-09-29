"""The authorization matrix: every route that needs a principal refuses the wrong one
before its handler runs (P0-14, SEC-2, FR-9.3, FR-14.10, R-28).

Cases come from the live route inventory at collection time (tests/meta/_authz.py), so a
route added later is swept with no edit here. Each request's endpoint is wrapped in a spy:
the 401 and 403 answers, and the project 404, must come before the handler, which is how
the project 404 is told apart from a handler's own 404.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tests._keys import BASE_URL, canary_test_app
from tests.meta._authz import case_ids

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, KeyClientFactory, MasterKeyFile, PepperFile
    from tumnis.core.clock import FixedClock


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    for kind in ("no_auth", "wrong_scope", "project", "session_only"):
        name = f"{kind}_case"
        if name in metafunc.fixturenames:
            metafunc.parametrize(name, case_ids(kind))


@pytest.fixture
def app(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    pepper_file: PepperFile,
) -> FastAPI:
    """The shared app with the canary routes mounted (their project lookup registered)."""
    return canary_test_app(db, dbos_sys_db, clock, str(pepper_file.path))


@pytest.fixture(scope="module", autouse=True)
def print_skipped() -> None:
    """Routes the matrix leaves out, each with its reason (visible with -s or in CI)."""
    from tests.meta._authz import collection_matrix  # noqa: PLC0415

    try:
        skipped = collection_matrix().skipped
    except Exception:  # no matrix yet; the tests fail on their own
        return
    for case_id, reason in skipped:
        print(f"authz matrix skips {case_id}: {reason}")


class _Spy:
    def __init__(self) -> None:
        self.calls = 0

    def wrap(self, route: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        original = route.dependant.call

        async def spy(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            return await original(*args, **kwargs)

        monkeypatch.setattr(route.dependant, "call", spy)


async def _send(client: httpx.AsyncClient, request: Any) -> httpx.Response:
    return await client.request(
        request.method, request.url, params=request.params, json=request.body
    )


def _code(response: httpx.Response) -> str | None:
    if "json" not in response.headers.get("content-type", ""):
        return None
    code = response.json().get("code")
    return str(code) if code is not None else None


def _prepare(
    app: FastAPI, kind: str, case_id: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any, _Spy]:
    from tests.meta._authz import find_case, find_route  # noqa: PLC0415

    case = find_case(app, kind, case_id)
    route = find_route(app, case.method, case.path)
    spy = _Spy()
    spy.wrap(route, monkeypatch)
    return case, route, spy


@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("SEC-2")
@pytest.mark.wp("P0-14")
async def test_no_auth_is_401(
    no_auth_case: str, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-P0-14-09
    Every route whose policy is not `auth="none"` answers 401 `unauthenticated` without
    credentials, before its handler runs.
    """
    from tests.meta._authz import request_for  # noqa: PLC0415

    case, route, spy = _prepare(app, "no_auth", no_auth_case, monkeypatch)
    request = await request_for(case, route)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await _send(client, request)
    assert response.status_code == 401, (no_auth_case, response.text)
    assert _code(response) == "unauthenticated"
    assert spy.calls == 0


@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P0-14")
async def test_wrong_scope_is_403(
    wrong_scope_case: str,
    app: FastAPI,
    key_client: KeyClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P0-14-10
    Every route that takes an API key and requires scopes answers 403 `insufficient_scope`
    to a key holding every other scope, before its handler runs.
    """
    from tests.meta._authz import request_for  # noqa: PLC0415
    from tumnis.modules.auth.scopes import SCOPES  # noqa: PLC0415

    case, route, spy = _prepare(app, "wrong_scope", wrong_scope_case, monkeypatch)
    others = SCOPES - case.policy.scopes
    client = await key_client(others)
    async with client:
        response = await _send(client, await request_for(case, route))
    assert response.status_code == 403, (wrong_scope_case, response.text)
    assert _code(response) == "insufficient_scope"
    assert spy.calls == 0


@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P0-14")
async def test_project_limited_key_outside_project_is_404(
    project_case: str,
    app: FastAPI,
    key_client: KeyClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P0-14-11
    Every route that names a project (`project_param`) answers 404 `not_found` to a key
    limited to project P aiming at project Q, before its handler runs (existence does not
    leak, R-28); aiming at P, the authorization lets the request through.
    """
    from tests.meta._authz import request_for  # noqa: PLC0415
    from tumnis.modules.auth.scopes import SCOPES  # noqa: PLC0415

    case, route, spy = _prepare(app, "project", project_case, monkeypatch)
    mine, other = uuid.uuid4(), uuid.uuid4()
    client = await key_client(SCOPES, projects={mine})
    async with client:
        outside = await _send(client, await request_for(case, route, other))
        assert outside.status_code == 404, (project_case, outside.text)
        assert _code(outside) == "not_found"
        assert spy.calls == 0

        inside = await _send(client, await request_for(case, route, mine))
    assert spy.calls == 1, (project_case, inside.status_code, inside.text)
    assert inside.status_code not in {401, 403}, (project_case, inside.text)


@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("SEC-2", "FR-9.3")
@pytest.mark.wp("P0-14")
async def test_api_key_on_session_only_route_is_403(
    session_only_case: str,
    app: FastAPI,
    key_client: KeyClientFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-P0-14-12
    Every `auth="session"` route (admin actions: settings, keys, sessions, audit, dead
    letters) answers 403 `session_required` to a full-scope key, before its handler runs.
    """
    from tests.meta._authz import request_for  # noqa: PLC0415
    from tumnis.modules.auth.scopes import SCOPES  # noqa: PLC0415

    case, route, spy = _prepare(app, "session_only", session_only_case, monkeypatch)
    client = await key_client(SCOPES)
    async with client:
        response = await _send(client, await request_for(case, route))
    assert response.status_code == 403, (session_only_case, response.text)
    assert _code(response) == "session_required"
    assert spy.calls == 0


@pytest.mark.req("SEC-2")
@pytest.mark.wp("P0-14")
def test_matrix_extends_to_new_routes() -> None:
    """T-P0-14-13
    Adding a synthetic router to a test app adds its routes to the generated cases: a
    session-only route to `no_auth` and `session_only`; a scoped key route naming a project
    to `no_auth`, `wrong_scope` and `project`; an `auth="none"` route to none of them (it
    is listed as skipped).
    """
    from tests.meta._authz import KINDS, build_matrix, inventory_app  # noqa: PLC0415
    from tumnis.core.routing import RoutePolicy, route_policy, v1_router  # noqa: PLC0415

    router = v1_router("synthetic", prefix="/synthetic-matrix", tags=["synthetic"])

    @router.get("/admin")
    @route_policy(RoutePolicy(auth="session"))
    async def synthetic_admin() -> dict[str, str]:
        return {}

    @router.get("/projects/{project_id}/things")
    @route_policy(
        RoutePolicy(
            auth="session_or_key",
            scopes=frozenset({"context:read"}),
            project_param="path:project_id",
        )
    )
    async def synthetic_things(project_id: uuid.UUID) -> dict[str, str]:
        return {}

    @router.get("/public")
    @route_policy(RoutePolicy(auth="none"))
    async def synthetic_public() -> dict[str, str]:
        return {}

    before = build_matrix(inventory_app())
    after = build_matrix(inventory_app((router,)))
    admin = "GET /v1/synthetic-matrix/admin"
    things = "GET /v1/synthetic-matrix/projects/{project_id}/things"
    public = "GET /v1/synthetic-matrix/public"
    expected = {
        "no_auth": {admin, things},
        "session_only": {admin},
        "wrong_scope": {things},
        "project": {things},
    }
    for kind in KINDS:
        added = set(after.ids(kind)) - set(before.ids(kind))
        assert added == expected[kind], (kind, added)
        assert set(before.ids(kind)) <= set(after.ids(kind))
    assert public in {case_id for case_id, _ in after.skipped}
    assert "POST /v1/auth/login" in {case_id for case_id, _ in after.skipped}
    assert "GET /v1/keys" in after.ids("session_only")
