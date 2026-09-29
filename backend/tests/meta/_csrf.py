"""Route inventory for the CSRF sweep (P0-13) and the preauth check in test_login.py.

No assertions live here. Routes are read from a real `create_app()` (which does no I/O)
built with placeholder settings at collection time, so a route added later is swept
without anyone listing it; each route's policy is the one `route_policy` attached
(P0-10's `RoutePolicy`).
"""

from __future__ import annotations

import re
import uuid
from functools import cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SESSION_AUTH = frozenset({"session", "session_or_key"})
KEY_AUTH = frozenset({"session_or_key", "key_or_task_token"})
PLACEHOLDER_URL = "postgresql+psycopg://inventory:inventory@127.0.0.1:1/inventory"
_PARAM = re.compile(r"\{([^}:]+)(?::[^}]*)?\}")


def policy_of(route: Any) -> Any:
    """The RoutePolicy `route_policy` stored on the endpoint, or None."""
    from tumnis.core.routing import policy_of as routing_policy_of  # noqa: PLC0415

    return routing_policy_of(route)


def _walk(app: FastAPI) -> list[Any]:
    """Every route with included routers unpacked (P0-10's walker)."""
    from tumnis.core.routing import walk_routes  # noqa: PLC0415

    return walk_routes(app)


@cache
def inventory_app() -> FastAPI:
    """The app's routes as `create_app` mounts them, with placeholder settings."""
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
    return create_app(settings=settings)


def _routes(app: FastAPI, auth: frozenset[str]) -> list[tuple[str, str, Any]]:
    found = []
    for route in _walk(app):
        policy = policy_of(route)
        if policy is None or policy.auth not in auth:
            continue
        for method in sorted(getattr(route, "methods", ()) or ()):
            if method != "HEAD":
                found.append((method, route.path, route))
    return found


def session_routes(app: FastAPI) -> list[tuple[str, str, Any]]:
    """(method, path, route) for every route whose policy takes a session."""
    return _routes(app, SESSION_AUTH)


def session_write_routes(app: FastAPI) -> list[tuple[str, str, Any]]:
    """Session writes that require CSRF (policy.csrf)."""
    return [
        (method, path, route)
        for method, path, route in session_routes(app)
        if method in WRITE_METHODS and policy_of(route).csrf
    ]


def key_write_routes(app: FastAPI) -> list[tuple[str, str, Any]]:
    """Writes whose policy takes an API key (P0-14)."""
    return [(m, p, r) for m, p, r in _routes(app, KEY_AUTH) if m in WRITE_METHODS]


def csrf_exempt_routes(app: FastAPI) -> list[tuple[str, str, str | None]]:
    """(method, path, reason) for writes whose policy turns CSRF off."""
    found = []
    for route in _walk(app):
        policy = policy_of(route)
        if policy is None or policy.csrf:
            continue
        for method in sorted(set(getattr(route, "methods", ()) or ()) & WRITE_METHODS):
            found.append((method, route.path, policy.csrf_exempt_reason))
    return found


def case_ids(app: FastAPI, kind: str) -> list[str]:
    routes = session_write_routes(app) if kind == "session" else key_write_routes(app)
    return [f"{method} {path}" for method, path, _ in routes]


def find_route(app: FastAPI, method: str, path: str) -> Any:
    """The route context the request handler runs for (method, path): its `dependant` is
    the one the request handler calls. For an included router (FastAPI 0.141) that is the
    effective route context's dependant, not the one of the route the router built (P0-14
    found a spy on the latter never sees the endpoint run)."""
    for route in _walk(app):
        if route.path == path and method in (route.methods or ()):
            return route
    raise LookupError(f"{method} {path} is not a route of this app")


def fill_path(path: str) -> str:
    """Path parameters filled with fresh UUIDs."""
    return _PARAM.sub(lambda _m: str(uuid.uuid4()), path)
