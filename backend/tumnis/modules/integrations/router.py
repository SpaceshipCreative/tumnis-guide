"""integrations FastAPI router; thin calls into api.py.

`POST /v1/purges {scope, id, reason}` (R-37, P2-18): the one purge route, session only (an
API key is refused, whatever its scopes), idempotent, answering 202 once the purge is
recorded and audited; the deletes it starts run in the worker. P2-18 purges an archived
project; P3-09 adds scope `connection` and `GET /v1/purges/{id}`.

Connections (P3-02), all session only (connecting accounts is the signed-in owner's job):
- `GET /v1/connections/providers`: what can be connected.
- `GET /v1/connections`, `POST /v1/connections` (201, `pending_auth`).
- `GET`, `PATCH` (label and settings, at the version read), `DELETE` (204, with a reason;
  audited `connector.disconnected`) `/v1/connections/{connection_id}`.
- `POST /v1/connections/{connection_id}/oauth/start` (202 `{workflow_id}`), then
  `GET .../oauth/url` until the sign-in page's URL is there (never waits).
- `GET /v1/connections/oauth/callback?code&state[&iss]`: the provider sends the browser
  back here. The code is stored sealed and the waiting workflow told; no outbound call, no
  wait (R-30); 302 to the connection in Settings. A state that matches no consent in
  flight is 400 `oauth_state_mismatch`, audited.
- `POST /v1/connections/{connection_id}/sync` (202): Sync now.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.errors import ProblemError
from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.integrations import api
from tumnis.modules.integrations import testing as _testing  # noqa: F401  # registers the tick

router = v1_router("integrations", tags=["purges"])

PURGE = RoutePolicy(auth="session", idempotent=True)
READ = RoutePolicy(auth="session")
WRITE = RoutePolicy(auth="session", idempotent=True)
SETTINGS_PATH = "/settings/connections"

Session = Annotated[WorkspaceContext, Depends(require_session)]


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _base_url(request: Request) -> str:
    configured: str | None = request.app.state.settings.public_base_url
    return configured or str(request.base_url)


@router.post("/purges", status_code=202)
@route_policy(PURGE)
async def purge(body: api.PurgeIn, request: Request, session: SessionDep) -> api.PurgeOut:
    clock: Clock = request.app.state.clock
    return await api.purge(session, body, now=clock.now())


@router.get("/connections/providers", tags=["connections"])
@route_policy(RoutePolicy(auth="session", unpaginated_reason="one row per registered provider"))
async def list_providers(ctx: Session) -> list[api.ProviderOut]:
    return api.list_providers()


@router.get("/connections", tags=["connections"])
@route_policy(
    RoutePolicy(auth="session", unpaginated_reason="one row per connected account, a handful")
)
async def list_connections(ctx: Session) -> list[api.ConnectionOut]:
    return await api.list_connections(ctx)


@router.post("/connections", status_code=201, tags=["connections"])
@route_policy(WRITE)
async def create_connection(
    body: api.ConnectionCreate, request: Request, ctx: Session, session: SessionDep
) -> api.ConnectionOut:
    try:
        return await api.create_connection(
            ctx,
            body.provider,
            body.settings,
            account_label=body.account_label,
            consent_acknowledged=body.consent_acknowledged,
            now=_clock(request).now(),
            session=session,
        )
    except api.UnknownProvider:
        raise ProblemError(422, "unknown_provider", f"No provider {body.provider!r}") from None
    except api.ConsentRequired as exc:
        raise ProblemError(422, "consent_required", str(exc)) from None


# Declared before /connections/{connection_id}, which would otherwise take "oauth".
@router.get(
    "/connections/oauth/callback",
    response_class=RedirectResponse,
    status_code=302,
    tags=["connections"],
)
@route_policy(READ)
async def oauth_callback(
    request: Request,
    ctx: Session,
    *,
    state: Annotated[str, Query(max_length=256)],
    code: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
    iss: Annotated[str | None, Query(max_length=2048)] = None,
) -> RedirectResponse:
    now = _clock(request).now()
    accepted = await api.accept_connection_callback(
        ctx, state=state, code=None if error is not None else code, iss=iss, now=now
    )
    if accepted.outcome == "mismatch":
        await api.audit_state_mismatch(ctx, now=now)
        raise ProblemError(
            400, "oauth_state_mismatch", "This sign-in link is not one Tumnis started, or expired"
        )
    await api.deliver_callback(accepted)
    query = f"?connection={accepted.connection_id}"
    if accepted.outcome == "declined":
        query += "&connect_error=1"
    return RedirectResponse(SETTINGS_PATH + query, status_code=302)


@router.get("/connections/{connection_id}", tags=["connections"])
@route_policy(READ)
async def get_connection(connection_id: UUID, ctx: Session) -> api.ConnectionOut:
    return await api.get_connection(ctx, connection_id)


@router.patch("/connections/{connection_id}", tags=["connections"])
@route_policy(WRITE)
async def update_connection(
    connection_id: UUID, body: api.ConnectionPatch, ctx: Session, session: SessionDep
) -> api.ConnectionOut:
    return await api.update_connection(ctx, connection_id, body, session=session)


@router.delete("/connections/{connection_id}", status_code=204, tags=["connections"])
@route_policy(WRITE)
async def disconnect(
    connection_id: UUID,
    body: api.DisconnectIn,
    request: Request,
    ctx: Session,
    session: SessionDep,
) -> Response:
    """Disconnects with a reason (audited); what it synced stays until purged (P3-09)."""
    await api.disconnect(
        ctx, connection_id, body.reason, now=_clock(request).now(), session=session
    )
    return Response(status_code=204)


@router.post("/connections/{connection_id}/oauth/start", status_code=202, tags=["connections"])
@route_policy(
    RoutePolicy(
        auth="session",
        idempotent=False,
        not_idempotent_reason="starts a new sign-in; the newest one is the one polled",
    )
)
async def start_oauth(
    connection_id: UUID, request: Request, ctx: Session
) -> api.ConnectionsOAuthStart:
    return await api.start_oauth(ctx, connection_id, base_url=_base_url(request))


@router.get("/connections/{connection_id}/oauth/url", tags=["connections"])
@route_policy(READ)
async def oauth_url(connection_id: UUID, request: Request, ctx: Session) -> api.AuthorizeUrlOut:
    return await api.authorize_url(ctx, connection_id, now=_clock(request).now())


@router.post("/connections/{connection_id}/sync", status_code=202, tags=["connections"])
@route_policy(
    RoutePolicy(
        auth="session",
        idempotent=False,
        not_idempotent_reason="enqueues a sync; one is queued or running per connection",
    )
)
async def sync_now(connection_id: UUID, ctx: Session) -> api.ConnectionOut:
    return await api.request_sync(ctx, connection_id)
