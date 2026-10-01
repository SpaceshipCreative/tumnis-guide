"""Route sweep helpers for A0.3 (tenant isolation over every route).

No assertions live here: spec-guard locks the test body in test_a0_3_tenant_isolation.py,
and this module is where the work packages that turn A0.3 green plug in their shapes:
P0-10 (route walker `tumnis.core.routing.walk_routes`, which unpacks nested included
routers, and `RoutePolicy` on each route), P0-06 (`two_workspaces`), P0-13
(`session_client`) and P0-14 (`key_client`). Until they land, the sweep cannot be built
and the test fails, as a spec test should.

Contract assumed here (adjust with the fixture, not in the test):
- `two_workspaces` returns `(a, b)`; each is a workspace UUID or has `.id`.
- `session_client` and `key_client` act for workspace B when `two_workspaces` is in use
  (B plays the fixture's own workspace; A is the other tenant).
- `key_client(scopes, projects=None)` returns (or resolves to) an `httpx.AsyncClient`.
"""

from __future__ import annotations

import datetime as dt
import enum
import importlib
import inspect
import json
import re
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from tumnis.core.modules import MODULES

CALLERS = ("session", "key")
NO_ROUTES = "<no /v1 route inventory>"
READS = frozenset({"GET", "HEAD"})
# Routes the sweep cannot aim at another tenant: test helpers (fakes only) and routes
# with no caller identity.
EXCLUDED_PREFIXES = ("/v1/test/",)
CALLER_AUTH = {
    "session": frozenset({"session", "session_or_key"}),
    "key": frozenset({"session_or_key", "key_or_task_token"}),
}
# `{id}` after a path segment whose table name is not the segment itself.
TABLE_ALIASES = {
    "review": "review_items",
    "keys": "api_keys",
    "columns": "board_columns",
    "profiles": "agent_profiles",
}


@dataclass(frozen=True)
class RouteCase:
    method: str
    path: str
    module: str
    route: Any = field(compare=False, repr=False)


@dataclass(frozen=True)
class SweepRequest:
    """One request aimed at workspace A. kind: "row" (names an A row: expect 404),
    "list" (a read without a row: no A rows back) or "write" (a write that names no A
    row: must not touch A)."""

    kind: str
    method: str
    url: str
    params: dict[str, str]
    body: Any


# --- inventory ---------------------------------------------------------------------------


def _module_of(route: Any) -> str:
    parts = getattr(route.endpoint, "__module__", "").split(".")
    return parts[2] if parts[:2] == ["tumnis", "modules"] and len(parts) > 2 else "core"


def _policy(route: Any) -> Any:
    """The route's P0-10 `RoutePolicy` (None for a route declared without one)."""
    from tumnis.core.routing import policy_of  # noqa: PLC0415

    return policy_of(route)


def _auth(route: Any) -> str | None:
    return getattr(_policy(route), "auth", None)


def accepts(route: Any, caller: str) -> bool:
    """Whether the route's policy lets this caller in at all (P0-10 `RoutePolicy.auth`)."""
    auth = _auth(route)
    return auth is None or auth in CALLER_AUTH[caller]


def route_inventory(app: Any) -> list[RouteCase]:
    """Every /v1 route of the app, one case per method, test routes excluded. Walks nested
    included routers with P0-10's route-registry walker (`walk_routes`): each case's
    `route` carries the full path, `endpoint`, `dependant` and `body_field`."""
    from fastapi.routing import APIRoute  # noqa: PLC0415

    from tumnis.core.routing import walk_routes  # noqa: PLC0415

    cases = [
        RouteCase(method, route.path, _module_of(route), route)
        for route in walk_routes(app)
        if isinstance(route.original_route, APIRoute)
        and route.path.startswith("/v1/")
        and not route.path.startswith(EXCLUDED_PREFIXES)
        and _auth(route) != "none"
        for method in sorted(route.methods - {"HEAD", "OPTIONS"})
    ]
    return sorted(cases, key=lambda c: (c.path, c.method))


def modules_with_routes() -> set[str]:
    """Modules whose router.py declares at least one route the sweep can aim at (a module
    whose only routes take no caller identity, like GitHub's signed webhook, has none)."""
    present = set()
    for name in MODULES:
        router = getattr(importlib.import_module(f"tumnis.modules.{name}.router"), "router", None)
        if router is not None and any(_auth(r) != "none" for r in getattr(router, "routes", ())):
            present.add(name)
    return present


def find_case(app: Any, method: str, path: str) -> RouteCase:
    for case in route_inventory(app):
        if (case.method, case.path) == (method, path):
            return case
    raise LookupError(f"{method} {path} is not in the app's /v1 route inventory")


def collection_cases() -> list[tuple[str, str, str]]:
    """(caller, method, path) for pytest_generate_tests, from an app built with no I/O.
    Before the app factory and the /v1 routes exist, one placeholder case per caller keeps
    the test visible (and failing) instead of silently empty."""
    try:
        from tumnis.app import create_app  # noqa: PLC0415
        from tumnis.settings import Settings  # noqa: PLC0415

        dsn = "postgresql+psycopg://inventory:inventory@127.0.0.1:1/inventory"
        settings = Settings(database_url=dsn, database_direct_url=dsn, tumnis_adapters="fake")
        inventory = route_inventory(create_app(settings))
    except Exception:  # any failure means "no inventory yet"
        inventory = []
    cases = [
        (caller, case.method, case.path)
        for case in inventory
        for caller in CALLERS
        if accepts(case.route, caller)
    ]
    return cases or [(caller, "-", NO_ROUTES) for caller in CALLERS]


# --- workspace A: ids, rows and snapshots -----------------------------------------------


def workspace_id(workspace: Any) -> str:
    return str(getattr(workspace, "id", workspace))


def _connect(libpq: str) -> Any:
    import psycopg  # noqa: PLC0415

    return psycopg.connect(libpq, autocommit=True)


def tenant_tables(owner_dsn: str) -> list[str]:
    """Tables in public with a workspace_id column, plus the tenant root `workspaces`."""
    with _connect(owner_dsn) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'workspace_id' "
            "ORDER BY table_name"
        ).fetchall()
    return [str(row[0]) for row in rows]


def row_ids(owner_dsn: str, workspace: Any) -> dict[str, str]:
    """One row id per tenant table in the workspace (the A side of every request)."""
    wid = workspace_id(workspace)
    ids = {"workspaces": wid}
    with _connect(owner_dsn) as conn:
        for table in tenant_tables(owner_dsn):
            row = conn.execute(
                f'SELECT id FROM "{table}" WHERE workspace_id = %s LIMIT 1',  # noqa: S608
                (wid,),
            ).fetchone()
            if row is not None:
                ids[table] = str(row[0])
    return ids


def all_ids(owner_dsn: str, workspace: Any) -> set[str]:
    """Every id of every row the workspace owns, and the workspace id itself."""
    wid = workspace_id(workspace)
    out = {wid}
    with _connect(owner_dsn) as conn:
        for table in tenant_tables(owner_dsn):
            rows = conn.execute(
                f'SELECT id::text FROM "{table}" WHERE workspace_id = %s',  # noqa: S608
                (wid,),
            ).fetchall()
            out.update(str(r[0]) for r in rows)
    return out


def snapshot(owner_dsn: str, workspace: Any) -> dict[str, list[str]]:
    """Every row the workspace owns, per table, as the owner role (RLS does not hide
    rows from the owner), serialized so versions and every column compare."""
    wid = workspace_id(workspace)
    out: dict[str, list[str]] = {}
    with _connect(owner_dsn) as conn:
        for table in ["workspaces", *tenant_tables(owner_dsn)]:
            column = "id" if table == "workspaces" else "workspace_id"
            rows = conn.execute(
                f'SELECT to_jsonb(t)::text FROM "{table}" t WHERE {column} = %s',  # noqa: S608
                (wid,),
            ).fetchall()
            out[table] = sorted(str(r[0]) for r in rows)
    return out


def leaked(payload: Any, a_ids: set[str]) -> set[str]:
    """A's ids found anywhere in a JSON response body."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value in a_ids:
            found.add(value)

    walk(payload)
    return found


def response_json(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


# --- requests aimed at A ------------------------------------------------------------------


def _table(path: str, param: str, tables: Mapping[str, str]) -> str | None:
    if param == "id":
        segments = [s for s in path.split("/") if s]
        before = segments[segments.index("{id}") - 1]
        candidates = [TABLE_ALIASES.get(before, before), f"{before}s", f"{before}_items"]
    elif param.endswith("_id"):
        stem = param.removesuffix("_id")
        candidates = [f"{stem}s", stem, f"{stem}es", f"{stem}_items"]
    else:
        return None
    return next((c for c in candidates if c in tables), None)


def _annotation(model_field: Any) -> Any:
    return getattr(model_field, "type_", None) or model_field.field_info.annotation


def _sample(annotation: Any) -> str:
    """A valid value for a path parameter that does not name a row."""
    if annotation is dt.date:
        return "2026-03-09"
    if annotation is dt.datetime:
        return "2026-03-09T14:00:00Z"
    if annotation is int:
        return "1"
    if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
        return str(next(iter(annotation)).value)
    return "x"


def _body(route: Any, path: str, tables: Mapping[str, str]) -> tuple[Any, bool]:
    """A valid body from the route's request model (polyfactory) with every id field
    that names a table pointed at A's row. Returns (body, names_an_a_row)."""
    body_field = getattr(route, "body_field", None)
    if body_field is None:
        return None, False
    from polyfactory.factories.pydantic_factory import ModelFactory  # noqa: PLC0415

    model = _annotation(body_field)
    body = ModelFactory.create_factory(model).build().model_dump(mode="json")
    names_a = False
    for key in list(body):
        table = _table(path, key, tables) if key != "id" else None
        if table is not None:
            body[key] = tables[table]
            names_a = True
    return body, names_a


def requests_for(case: RouteCase, tables: Mapping[str, str]) -> Iterator[SweepRequest]:
    """The requests the sweep sends for one route (steps 2 to 4 of A0.3)."""
    route, url, row_param = case.route, case.path, False
    for param in route.dependant.path_params:
        table = _table(case.path, param.name, tables)
        annotation = _annotation(param)
        if table is not None:
            value, row_param = tables[table], True
        elif annotation is uuid.UUID:
            raise LookupError(f"{case.method} {case.path}: no A row for {{{param.name}}}")
        else:
            value = _sample(annotation)
        # `{name}` or `{name:convertor}` (a Starlette path convertor such as `:uuid`)
        url = re.sub(rf"\{{{param.name}(?::[^}}]*)?\}}", value, url)
    id_filters = {
        q.name: tables[t]
        for q in route.dependant.query_params
        if (t := _table(case.path, q.name, tables)) is not None
    }
    # Required query parameters that name no row (a date range, say) get a valid sample,
    # so the request reaches the route instead of stopping at validation (P0-14). A
    # required id filter (`?project_id=` on the project week, P1-12) cannot be left out, so
    # it always names A's row and the read is a row read, like an id in the path.
    required: dict[str, str] = {}
    for q in route.dependant.query_params:
        if not q.field_info.is_required():
            continue
        if q.name in id_filters:
            required[q.name], row_param = id_filters[q.name], True
        else:
            required[q.alias or q.name] = _sample(_annotation(q))
    if case.method in READS:
        if row_param:
            yield SweepRequest("row", case.method, url, required, None)
            return
        yield SweepRequest("list", case.method, url, required, None)
        if id_filters:
            yield SweepRequest("list", case.method, url, {**required, **id_filters}, None)
        return
    body, body_names_a = _body(route, case.path, tables)
    kind = "row" if row_param or body_names_a or id_filters else "write"
    yield SweepRequest(kind, case.method, url, {**required, **id_filters}, body)


# --- B's clients ---------------------------------------------------------------------------


def every_scope(app: Any) -> list[str]:
    """Every scope any route asks for (P0-10 `RoutePolicy.scopes`), for B's key."""
    scopes: set[str] = set()
    for case in route_inventory(app):
        policy = _policy(case.route)
        scopes.update(getattr(policy, "scopes", ()) or ())
    return sorted(scopes)


async def client_for(caller: str, app: Any, session_client: Any, key_client: Any) -> Any:
    """B's client for this caller kind: the signed-in session, or a key with every scope."""
    if caller == "session":
        return session_client
    client = key_client(every_scope(app))
    return await client if inspect.isawaitable(client) else client
