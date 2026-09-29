"""Who is calling: the `Principal` on `request.state.principal` (P0-10).

The slot is anonymous by default. P0-13's authentication middleware (sessions) and P0-14's
resolvers (API keys, task tokens, device tokens) fill it; tests install a header resolver
(`X-Test-Principal`, core's `_demo.py`) until then. Idempotency keys and rate-limit buckets
are scoped by `Principal.key`; tenant work runs in `Principal.workspace_context()`.
"""

from dataclasses import dataclass, field
from typing import Final, Literal
from uuid import UUID

from starlette.requests import Request

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


async def require_principal(request: Request) -> Principal:
    """FastAPI dependency: the principal, or 401 `unauthenticated`."""
    principal = principal_of(request)
    if principal.anonymous:
        raise ProblemError(401, "unauthenticated", "Sign in or send an API key")
    return principal
