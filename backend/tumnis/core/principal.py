"""Who is calling: the `Principal` on `request.state.principal` (P0-10, P0-13).

The slot is anonymous by default. P0-13's authentication middleware (sessions) and P0-14's
bearer resolver (API keys, task tokens, device tokens; registered ahead of the session
cookie) fill it; tests install a header resolver
(`X-Test-Principal`, core's `_demo.py`) until then. Idempotency keys and rate-limit buckets
are scoped by `Principal.key`; tenant work runs in `Principal.workspace_context()`.

`AuthenticationMiddleware` (P0-13) asks each registered resolver in turn
(`register_resolver`); the first that recognises the request's credentials returns its
`Principal`. A resolver that recognises them but refuses them (an expired or revoked
session) returns an `AuthFailure`, whose code the 401 carries (`session_expired`);
routes that need no auth are unaffected. Core imports no module: the auth module registers
its session resolver when its api is imported (tumnis.wiring).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final, Literal
from uuid import UUID

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from tumnis.core.errors import ProblemError
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import ActorRef

PrincipalKind = Literal["anonymous", "session", "api_key", "task_token", "device"]

# The actor kind (tumnis.core.types.ActorRef) each principal kind writes rows as.
_ACTOR_KIND: Final[dict[str, str]] = {
    "session": "user",
    "api_key": "api_key",
    "task_token": "task_token",
    "device": "device",
}


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    workspace_id: UUID | None = None
    subject_id: UUID | None = None  # user, key, token or device id
    scopes: frozenset[str] = field(default_factory=frozenset)  # sessions: all scopes (P0-14)
    project_ids: frozenset[UUID] | None = None  # None = unrestricted
    session_id: UUID | None = None  # sessions (P0-13): the sessions row
    csrf_token: str | None = field(default=None, repr=False)  # what X-CSRF-Token must be

    @property
    def anonymous(self) -> bool:
        return self.kind == "anonymous" or self.workspace_id is None or self.subject_id is None

    @property
    def actor(self) -> ActorRef:
        """`user:<id>`, `api_key:<id>`, ...: what rows it writes carry in `created_by`."""
        if self.anonymous:
            raise ValueError("an anonymous principal acts as nobody")
        return ActorRef(f"{_ACTOR_KIND[self.kind]}:{self.subject_id}")

    @property
    def key(self) -> str:
        """Stable identity for idempotency keys and rate-limit buckets."""
        return str(self.actor)

    def workspace_context(self) -> WorkspaceContext:
        if self.anonymous or self.workspace_id is None:
            raise ProblemError(401, "unauthenticated", "Sign in or send an API key")
        return WorkspaceContext(self.workspace_id, self.actor)


ANONYMOUS: Final = Principal(kind="anonymous")


def principal_of(request: Request) -> Principal:
    """The request's principal; anonymous when no resolver set one."""
    found = getattr(request.state, "principal", None)
    return found if isinstance(found, Principal) else ANONYMOUS


def auth_failure_of(request: Request) -> str:
    """The code a 401 carries: why the credentials sent were refused, or
    `unauthenticated` when none were recognised."""
    failure = getattr(request.state, "auth_failure", None)
    return failure.code if isinstance(failure, AuthFailure) else "unauthenticated"


def unauthenticated(request: Request) -> ProblemError:
    code = auth_failure_of(request)
    failure = getattr(request.state, "auth_failure", None)
    given = failure.detail if isinstance(failure, AuthFailure) else None
    detail = given or FAILURE_DETAIL.get(code, "Sign in or send an API key")
    return ProblemError(401, code, detail)


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency: the principal, or 401 (`unauthenticated`, `session_expired`)."""
    principal = principal_of(request)
    if principal.anonymous:
        raise unauthenticated(request)
    return principal


# --- Authentication (P0-13) ---------------------------------------------------------------


@dataclass(frozen=True)
class AuthFailure:
    """Credentials a resolver recognised and refused; `code` is the 401's problem code and
    `detail`, when set, its detail (an expired API key: `unauthenticated`, `key_expired`)."""

    code: str
    detail: str | None = None


FAILURE_DETAIL: Final[dict[str, str]] = {
    "session_expired": "The session ended after 30 days without use; sign in again",
}

Resolver = Callable[[Request], Awaitable[Principal | AuthFailure | None]]
_resolvers: dict[str, Resolver] = {}


def register_resolver(name: str, resolver: Resolver, *, before: str | None = None) -> None:
    """Add (or replace) a resolver; they run in registration order, or just ahead of the
    resolver named `before` (P0-14 puts bearer keys ahead of the session cookie, so a stale
    cookie never hides a valid key)."""
    if before is None or before not in _resolvers or before == name:
        _resolvers[name] = resolver
        return
    _resolvers.pop(name, None)
    items = list(_resolvers.items())
    at = [key for key, _ in items].index(before)
    items.insert(at, (name, resolver))
    _resolvers.clear()
    _resolvers.update(items)


def registered_resolvers() -> tuple[str, ...]:
    return tuple(_resolvers)


class AuthenticationMiddleware:
    """Sets `request.state.principal` (and `auth_failure`) for every HTTP request from the
    first resolver that recognises its credentials. Pure ASGI, so the state reaches the
    route and a streamed body alike."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _resolvers:
            request = Request(scope)
            for resolver in list(_resolvers.values()):
                found = await resolver(request)
                if isinstance(found, Principal):
                    request.state.principal = found
                    break
                if isinstance(found, AuthFailure):
                    request.state.auth_failure = found
                    break
        await self.app(scope, receive, send)
