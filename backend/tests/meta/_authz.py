"""Route inventory for the authorization matrix (P0-14). No assertions live here.

The matrix reads every `/v1` route of `create_app()` (built with placeholder settings at
collection time, so it does no I/O) through P0-10's walker and sorts each into the checks
its policy calls for. A route added later joins the matrix without an edit. The synthetic
canary routes (`tests._keys.canary_router`) are mounted too, so every check has cases
before the modules with scoped and project routes land.

- `no_auth`: every route whose policy needs a principal (401 without credentials).
- `wrong_scope`: routes that accept an API key and require scopes (403
  `insufficient_scope` to a key holding every other scope).
- `project`: routes that accept an API key and name a project (`project_param`): 404
  `not_found` for a key limited to another project.
- `session_only`: `auth="session"` routes (403 `session_required` to a full-scope key).
- `skipped`: `auth="none"` and ops routes, with the reason printed by the matrix.

`lookup:<module>` routes need a row of that module in a given project: `LOOKUP_TARGETS`
maps the module to a function making (or finding) one; a module that adds such a route
adds its entry here.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

PRINCIPAL_AUTH: Final = frozenset({"session", "session_or_key", "key_or_task_token"})
KEY_AUTH: Final = frozenset({"session_or_key", "key_or_task_token"})
KINDS: Final = ("no_auth", "wrong_scope", "project", "session_only")
PLACEHOLDER_URL = "postgresql+psycopg://inventory:inventory@127.0.0.1:1/inventory"
_PARAM = re.compile(r"\{([^}:]+)(?::[^}]*)?\}")


@dataclass(frozen=True)
class Case:
    method: str
    path: str
    policy: Any = field(compare=False, repr=False)

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class Matrix:
    cases: dict[str, list[Case]] = field(default_factory=lambda: {k: [] for k in KINDS})
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (case id, reason)

    def ids(self, kind: str) -> list[str]:
        return [case.id for case in self.cases[kind]]


def build_matrix(app: FastAPI) -> Matrix:
    from fastapi.routing import APIRoute  # noqa: PLC0415

    from tumnis.core.routing import TumnisRoute, policy_of, walk_routes  # noqa: PLC0415

    matrix = Matrix()
    openapi = getattr(app, "openapi_url", None)
    for route in walk_routes(app):
        path = route.path or ""
        methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        if not isinstance(route.original_route, APIRoute):
            continue
        if not path.startswith("/v1/") or path == openapi:
            for method in methods:
                matrix.skipped.append((f"{method} {path}", "ops route outside /v1"))
            continue
        policy = policy_of(route)
        if not isinstance(route.original_route, TumnisRoute) or policy is None:
            for method in methods:
                matrix.skipped.append(
                    (f"{method} {path}", "no RoutePolicy (the registry fails it)")
                )
            continue
        for method in methods:
            case = Case(method, path, policy)
            if policy.auth not in PRINCIPAL_AUTH:
                reason = (
                    "auth=none: reachable without credentials"
                    if policy.auth == "none"
                    else f"auth={policy.auth}: its own bearer check"
                )
                matrix.skipped.append((case.id, reason))
                continue
            matrix.cases["no_auth"].append(case)
            if policy.auth == "session":
                matrix.cases["session_only"].append(case)
            if policy.auth in KEY_AUTH and policy.scopes:
                matrix.cases["wrong_scope"].append(case)
            if policy.auth in KEY_AUTH and policy.project_param:
                matrix.cases["project"].append(case)
    return matrix


def inventory_app(extra_routers: tuple[APIRouter, ...] = ()) -> FastAPI:
    """create_app with placeholder settings (no I/O) and the given extra routers."""
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.settings import Settings  # noqa: PLC0415

    settings = Settings(
        database_url=PLACEHOLDER_URL,
        database_direct_url=PLACEHOLDER_URL,
        deployment_env="dev",
        tumnis_adapters="fake",
        master_key_file="/nonexistent/master_key",
        api_key_pepper_file="/nonexistent/pepper",
    )
    return create_app(settings=settings, extra_routers=extra_routers)


@cache
def collection_matrix() -> Matrix:
    """The matrix of the app with the canary routes, at collection time."""
    from tests._keys import canary_router, register_canary_lookup  # noqa: PLC0415

    register_canary_lookup()
    return build_matrix(inventory_app((canary_router(),)))


def case_ids(kind: str) -> list[str]:
    try:
        return collection_matrix().ids(kind)
    except Exception:  # before P0-14 there is no matrix (a missing name, a policy field)
        return ["<no authorization matrix>"]


def find_case(app: FastAPI, kind: str, case_id: str) -> Case:
    for case in build_matrix(app).cases[kind]:
        if case.id == case_id:
            return case
    raise LookupError(f"{case_id} is not a {kind} case of this app")


def find_route(app: FastAPI, method: str, path: str) -> Any:
    """The route context the request handler runs for (method, path). For an included
    router (FastAPI 0.141) that is the effective route context, not the route the router
    built: its `dependant` (shared by every walk) is the one whose `call` runs, so a spy on
    it sees the endpoint run."""
    from tumnis.core.routing import walk_routes  # noqa: PLC0415

    for route in walk_routes(app):
        if route.path == path and method in (route.methods or ()):
            return route
    raise LookupError(f"{method} {path} is not a route of this app")


# --- Requests -------------------------------------------------------------------------------


async def canary_row(project_id: uuid.UUID) -> uuid.UUID:
    from tests._keys import CANARY_PROJECTS  # noqa: PLC0415

    row_id = uuid.uuid4()
    CANARY_PROJECTS[row_id] = project_id
    return row_id


async def _workspace_with_project(project_id: uuid.UUID) -> uuid.UUID:
    """The workspace of the newest API key (the one the case sends), with project
    `project_id` made there when there is none, written as the owner."""
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core import db  # noqa: PLC0415

    async with db.owner_sessionmaker()() as s, s.begin():
        workspace_id: uuid.UUID | None = await s.scalar(
            text("SELECT workspace_id FROM api_keys ORDER BY created_at DESC, id DESC LIMIT 1")
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, workspace_id, name, sort_key, created_by)"
                " VALUES (:p, :ws, :name, 'a0', 'system') ON CONFLICT (id) DO NOTHING"
            ),
            {"p": project_id, "ws": workspace_id, "name": f"Authz {project_id}"},
        )
    assert workspace_id is not None
    return workspace_id


async def task_row(project_id: uuid.UUID) -> uuid.UUID:
    """A task in project `project_id` (made with that id when there is none), in the
    workspace of the newest API key (the one the case sends), written as the owner
    (P0-18's `lookup:tasks` routes)."""
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core import db  # noqa: PLC0415

    workspace_id = await _workspace_with_project(project_id)
    async with db.owner_sessionmaker()() as s, s.begin():
        task_id: uuid.UUID | None = await s.scalar(
            text(
                "INSERT INTO tasks (workspace_id, project_id, title, board_rank, created_by)"
                " VALUES (:ws, :p, 'Authz probe', 'a0', 'system') RETURNING id"
            ),
            {"p": project_id, "ws": workspace_id},
        )
    assert task_id is not None
    return task_id


async def document_row(project_id: uuid.UUID) -> uuid.UUID:
    """A text entry in project `project_id` (made with that id when there is none), in the
    newest API key's workspace, written through knowledge's api as the system actor
    (P0-24's `lookup:knowledge` route)."""
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    workspace_id = await _workspace_with_project(project_id)
    async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
        return await knowledge.put_text_document(
            s, project_id, title="Authz probe", body_md="Probe", role=None
        )


async def run_row(project_id: uuid.UUID) -> uuid.UUID:
    """A queued run of a task in project `project_id`, on the project's agent profile (made
    when there is none), in the newest API key's workspace, written as the owner (P2-04's
    `lookup:runs` routes)."""
    from sqlalchemy import text  # noqa: PLC0415

    from tumnis.core import db  # noqa: PLC0415

    task_id = await task_row(project_id)
    async with db.owner_sessionmaker()() as s, s.begin():
        workspace_id = await s.scalar(
            text("SELECT workspace_id FROM tasks WHERE id = :t"), {"t": task_id}
        )
        profile_id = await s.scalar(
            text(
                "SELECT id FROM agent_profiles WHERE project_id = :p AND role = 'project'"
                " AND deleted_at IS NULL"
            ),
            {"p": project_id},
        )
        if profile_id is None:
            profile_id = await s.scalar(
                text(
                    "INSERT INTO agent_profiles"
                    " (workspace_id, name, role, project_id, transport, created_by)"
                    " VALUES (:ws, :name, 'project', :p, 'daemon', 'system') RETURNING id"
                ),
                {"ws": workspace_id, "name": f"authz-{project_id.hex[:12]}", "p": project_id},
            )
        run_id = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO runs (id, workspace_id, task_id, profile_id, kind, status,"
                " correlation_id, created_by)"
                " VALUES (:id, :ws, :t, :profile, 'task', 'queued', :corr, 'system')"
            ),
            {
                "id": run_id,
                "ws": workspace_id,
                "t": task_id,
                "profile": profile_id,
                "corr": f"run:{run_id}",
            },
        )
    return run_id


# module -> make a row of that module in the project; returns its id (lookup:<module>).
LOOKUP_TARGETS: dict[str, Callable[[uuid.UUID], Awaitable[uuid.UUID]]] = {
    "authz_canary": canary_row,
    "knowledge": document_row,
    "runs": run_row,
    "tasks": task_row,
}


def _body(route: Any) -> dict[str, Any] | None:
    body_field = getattr(route, "body_field", None)
    if body_field is None:
        return None
    from polyfactory.factories.pydantic_factory import ModelFactory  # noqa: PLC0415

    model = getattr(body_field, "type_", None) or body_field.field_info.annotation
    try:
        built: dict[str, Any] = ModelFactory.create_factory(model).build().model_dump(mode="json")
    except Exception:  # a body polyfactory cannot build: the checks fire before parsing
        return {}
    return built


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    params: dict[str, str]
    body: Any


async def request_for(case: Case, route: Any, project: uuid.UUID | None = None) -> Request:
    """A request for the case: path parameters filled with fresh ids, a body built with
    polyfactory from the route's model, and the project (when given) placed where the
    route's `project_param` names it. Required query parameters get a valid value."""
    param = case.policy.project_param or ""
    source, _, name = param.partition(":")
    body = _body(route)
    params: dict[str, str] = {}
    fill: dict[str, str] = {}
    if project is not None:
        if source == "path":
            fill[name] = str(project)
        elif source == "query":
            params[name] = str(project)
        elif source == "body":
            body = {**(body or {}), name: str(project)}
        elif source == "lookup":
            make = LOOKUP_TARGETS.get(name)
            if make is None:
                raise LookupError(f"tests/meta/_authz.py: no LOOKUP_TARGETS entry for {name}")
            row_id = await make(project)
            names = [p.name for p in route.dependant.path_params]
            target = "id" if "id" in names else names[0]
            fill[target] = str(row_id)
    for query in route.dependant.query_params:  # required ones, e.g. DELETE ?version= (P0-19)
        if query.field_info.is_required() and query.alias not in params:
            params[query.alias] = _query_value(query.field_info.annotation)
    url = _PARAM.sub(lambda m: fill.get(m.group(1), str(uuid.uuid4())), case.path)
    return Request(case.method, url, params, body)


def _query_value(annotation: Any) -> str:
    """A valid value for a required query parameter the case does not set."""
    if annotation is int:
        return "1"
    if annotation is uuid.UUID:
        return str(uuid.uuid4())
    return "x"
