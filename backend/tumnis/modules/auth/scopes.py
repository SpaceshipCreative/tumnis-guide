"""API key scopes (P0-14, FR-14.10).

`SCOPES` is the closed list a key or task token may hold; a key asking for any other is
refused at creation (422 `unknown_scope`). Routes name what they need in
`RoutePolicy.scopes`; `tumnis.core.routing.authorize` checks it before the handler (core
cannot import a module, so the check itself lives there; `check_scope` and
`check_project` are the same rules for code that holds a principal, such as list
filters and, from P2-01, MCP tools).
"""

from collections.abc import Iterable
from typing import Final
from uuid import UUID

from tumnis.core.principal import Principal

SCOPES: Final = frozenset(
    {
        "tasks:read",
        "tasks:write",
        "context:read",
        "knowledge:write",
        "drafts:write",
        "delegate",
        "ingest",
    }
)
# R-31: profile and cron keys get these unless they ask for more.
DEFAULT_PROFILE_SCOPES: Final = frozenset({"tasks:read", "context:read"})


def unknown_scopes(requested: Iterable[str]) -> list[str]:
    """The requested scopes that are not in SCOPES, sorted."""
    return sorted(set(requested) - SCOPES)


def check_scope(principal: Principal, required: frozenset[str]) -> bool:
    """Whether the principal holds every required scope (a session holds all of them)."""
    return principal.kind == "session" or required <= principal.scopes


def check_project(principal: Principal, project_id: UUID) -> bool:
    """Whether the principal may touch the project (None = every project)."""
    return principal.project_ids is None or project_id in principal.project_ids


def visible_projects(principal: Principal) -> frozenset[UUID] | None:
    """For list routes: the projects to filter to, or None for every project."""
    return principal.project_ids
