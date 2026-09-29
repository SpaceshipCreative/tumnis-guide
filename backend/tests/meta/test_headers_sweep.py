"""Every response carries the security headers: every route, every error, static files
(P0-16, SEC-4).

A sweep over the real app's routes (the P0-10 walker, nested routers unpacked) plus the
built shell, so a route a later work package adds is covered with no edit here. Requests
fill path parameters with random UUIDs and send no credentials: the headers must not
depend on auth or on the handler, so whatever status comes back is checked.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import Request  # runtime: the probe route's annotation resolves here

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence
    from pathlib import Path

    import httpx
    from fastapi import APIRouter, FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, MasterKeyFile
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

DSN = "postgresql+psycopg://sweep:sweep@127.0.0.1:1/sweep"
PARAM = re.compile(r"\{([^}:]+)(?::[^}]*)?\}")
# The built frontend: the shell and a hashed asset (P0-22 replaces the placeholder).
STATIC_CASES = (("GET", "/"), ("GET", "/assets/index-abc123.js"), ("GET", "/some/spa/route"))
MIN_HSTS_S = 31_536_000  # one year


def _route_cases() -> list[tuple[str, str]]:
    """(method, path) for every HTTP route of create_app() with fakes (the test routes
    included): GET where the route reads, its first declared method otherwise."""
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core.routing import walk_routes  # noqa: PLC0415
    from tumnis.settings import Settings  # noqa: PLC0415

    app = create_app(Settings(database_url=DSN, database_direct_url=DSN, tumnis_adapters="fake"))
    cases = set()
    for ctx in walk_routes(app):
        methods = set(ctx.methods or ()) - {"HEAD", "OPTIONS"}
        if not methods:
            continue  # websocket routes answer no HTTP response of their own
        cases.add(("GET" if "GET" in methods else sorted(methods)[0], ctx.path))
    return sorted(cases)


ROUTE_CASES = [*_route_cases(), *STATIC_CASES]


def _fill(path: str) -> str:
    return PARAM.sub(lambda _: str(uuid.uuid4()), path)


def _directives(csp: str) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            parsed[words[0].lower()] = words[1:]
    return parsed


def security_header_problems(response: httpx.Response) -> list[str]:
    """What is missing or weak in a response's security headers; empty when all is well."""
    problems: list[str] = []
    headers = response.headers
    csp = _directives(headers.get("content-security-policy", ""))
    script_src = csp.get("script-src", csp.get("default-src"))
    if script_src is None:
        problems.append("no CSP script-src or default-src")
    elif {"'unsafe-inline'", "'unsafe-eval'", "*", "data:"} & set(script_src):
        problems.append(f"weak script-src {script_src}")
    if csp.get("object-src") != ["'none'"]:
        problems.append("object-src is not 'none'")
    if csp.get("frame-ancestors") != ["'none'"]:
        problems.append("frame-ancestors is not 'none'")
    if headers.get("x-frame-options", "").upper() != "DENY":
        problems.append("X-Frame-Options is not DENY")
    if headers.get("referrer-policy") not in {"no-referrer", "same-origin", "strict-origin"}:
        problems.append(f"Referrer-Policy {headers.get('referrer-policy')!r}")
    if headers.get("x-content-type-options", "").lower() != "nosniff":
        problems.append("X-Content-Type-Options is not nosniff")
    hsts = re.search(r"max-age=(\d+)", headers.get("strict-transport-security", ""))
    if not hsts or int(hsts.group(1)) < MIN_HSTS_S:
        problems.append("HSTS missing or shorter than a year")
    for name in ("content-security-policy", "x-frame-options", "strict-transport-security"):
        if len(headers.get_list(name)) != 1:
            problems.append(f"{name} sent {len(headers.get_list(name))} times")
    return problems


def _shell(tmp_path: Path) -> Path:
    shell = tmp_path / "dist"
    (shell / "assets").mkdir(parents=True)
    (shell / "index.html").write_text(
        '<!doctype html><html><head><script type="module" src="/assets/index-abc123.js">'
        '</script></head><body><div id="root"></div></body></html>'
    )
    (shell / "assets" / "index-abc123.js").write_text("export {};\n")
    return shell


@pytest.fixture
async def swept_app(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    """create_app on the per-test database with fakes and a built shell mounted."""
    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis import app as app_module  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    monkeypatch.setattr(app_module, "SHELL_DIR", _shell(tmp_path))
    try:
        yield app_module.create_app(settings=settings_for(db, dbos_sys_db), clock=clock)
    finally:
        await core_db.dispose()


def _probe_router(max_body_bytes: int) -> APIRouter:
    """POST /v1/probe (small body limit) and GET /v1/probe/boom (always raises)."""
    from tumnis.core.routing import RoutePolicy, route_policy, v1_router  # noqa: PLC0415

    router = v1_router("probe", prefix="/probe")

    @router.post("")
    @route_policy(
        RoutePolicy(
            auth="none",
            idempotent=False,
            not_idempotent_reason="test probe, stores nothing",
            csrf=False,
            max_body_bytes=max_body_bytes,
        )
    )
    async def accept(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @router.get("/boom")
    @route_policy(RoutePolicy(auth="none"))
    async def boom() -> dict[str, str]:
        raise RuntimeError("forced failure for the headers sweep")

    return router


@pytest.fixture
async def error_app(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[FastAPI]:
    """create_app with the probe router and no shell (so a wrong method is a 405)."""
    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis import app as app_module  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    monkeypatch.setattr(app_module, "SHELL_DIR", tmp_path / "no-shell")
    try:
        yield app_module.create_app(
            settings=settings_for(db, dbos_sys_db),
            clock=clock,
            extra_routers=[_probe_router(max_body_bytes=64)],
        )
    finally:
        await core_db.dispose()


def _ids(cases: Sequence[tuple[str, str]]) -> Iterator[str]:
    for method, path in cases:
        yield f"{method} {path}"


def test_sweep_covers_ops_v1_and_static_routes() -> None:
    """The inventory the sweep runs on (not a spec test itself)."""
    paths = {path for _, path in ROUTE_CASES}
    assert {"/health/live", "/health/ready", "/metrics", "/"} <= paths
    assert any(path.startswith("/v1/") for path in paths)


@pytest.mark.req("SEC-4")
@pytest.mark.wp("P0-16")
@pytest.mark.parametrize(("method", "path"), ROUTE_CASES, ids=list(_ids(ROUTE_CASES)))
async def test_every_route_sends_security_headers(
    swept_app: FastAPI, method: str, path: str
) -> None:
    """T-P0-16-01
    Every route in the app (including /health/*, /metrics, the static shell and /v1/*)
    answers with a CSP whose script-src has no 'unsafe-inline', `X-Frame-Options: DENY`,
    a Referrer-Policy, HSTS and `nosniff`, whatever the status.
    """
    from tumnis.core.tests.integration._demo import client_at  # noqa: PLC0415

    kwargs: dict[str, Any] = {} if method == "GET" else {"json": {}}
    async with client_at(swept_app) as http:
        response = await http.request(method, _fill(path), **kwargs)
    assert security_header_problems(response) == [], (response.status_code, response.headers)


@pytest.mark.req("SEC-4")
@pytest.mark.wp("P0-16")
async def test_error_responses_send_security_headers(error_app: FastAPI) -> None:
    """T-P0-16-02
    A 404 on an unknown path, a 405, a 413 and a forced 500 (an unhandled exception, answered
    by the server error handler) carry the same headers as a 200.
    """
    from tumnis.core.tests.integration._demo import client_at  # noqa: PLC0415

    async with client_at(error_app) as http:
        responses = {
            200: await http.post("/v1/probe", content=b"{}"),
            404: await http.get("/no/such/path"),
            405: await http.delete("/health/live"),
            413: await http.post("/v1/probe", content=b"x" * 65),
            500: await http.get("/v1/probe/boom"),
        }
    for status, response in responses.items():
        assert response.status_code == status, (status, response.text)
        assert security_header_problems(response) == [], (status, response.headers)
    reference = responses[200].headers
    for response in responses.values():
        for name in ("content-security-policy", "strict-transport-security", "permissions-policy"):
            assert response.headers.get(name) == reference.get(name)
