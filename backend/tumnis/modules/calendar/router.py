"""calendar FastAPI router under /v1/calendar; thin calls into api.py (P1-09).

- `GET /oauth/start`: the Google consent URL for one more account (read-only scopes,
  offline access, PKCE); 409 `calendar_oauth_not_configured` without an OAuth client.
- `GET /oauth/callback?code&state`: Google sends the browser back here. The code is stored
  sealed and the exchange enqueued for the worker (no outbound call here); 302 to
  Settings > Calendar. An unknown, used or expired state is 400 `oauth_state_invalid`.
- `GET /accounts`: the connected accounts with their calendars, choice and sync status.
- `PUT /accounts/{account_id}/calendars`: choose the calendars to sync (versioned).
- `POST /accounts/{account_id}/sync`: Sync now (202).

All session-only: connecting accounts is the signed-in owner's job.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.errors import ProblemError
from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import NotFound, Version
from tumnis.modules.calendar import api

router = v1_router("calendar", prefixed=True, tags=["calendar"])

Session = Annotated[WorkspaceContext, Depends(require_session)]
READ = RoutePolicy(auth="session")
WRITE = RoutePolicy(auth="session", idempotent=True)
SETTINGS_PATH = "/settings/calendar"


class OAuthStartOut(BaseModel):
    url: str


class CalendarsIn(BaseModel):
    selected_calendar_ids: Annotated[list[str], Field(max_length=200)]
    version: Version


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _base_url(request: Request) -> str:
    configured: str | None = request.app.state.settings.public_base_url
    return configured or str(request.base_url)


@router.get("/oauth/start")
@route_policy(READ)
async def oauth_start(request: Request, ctx: Session) -> OAuthStartOut:
    try:
        url = await api.start_connect(ctx, base_url=_base_url(request), now=_clock(request).now())
    except api.OAuthNotConfigured:
        raise ProblemError(
            409,
            "calendar_oauth_not_configured",
            "Add the Google OAuth client in Settings > Calendar first",
        ) from None
    return OAuthStartOut(url=url)


@router.get("/oauth/callback", response_class=RedirectResponse, status_code=302)
@route_policy(READ)
async def oauth_callback(
    request: Request,
    ctx: Session,
    state: Annotated[str, Query(max_length=256)],
    code: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    if error is not None or code is None:
        return RedirectResponse(f"{SETTINGS_PATH}?connect_error=1", status_code=302)
    pending = await api.accept_callback(ctx, state=state, code=code, now=_clock(request).now())
    if pending is None:
        raise ProblemError(400, "oauth_state_invalid", "This sign-in link is unknown or used")
    return RedirectResponse(f"{SETTINGS_PATH}?connecting=1", status_code=302)


@router.get("/accounts")
@route_policy(
    RoutePolicy(auth="session", unpaginated_reason="one row per connected Google account")
)
async def list_accounts(ctx: Session) -> list[api.CalendarAccountOut]:
    return await api.list_accounts(ctx)


@router.put("/accounts/{account_id}/calendars")
@route_policy(WRITE)
async def select_calendars(
    account_id: UUID, body: CalendarsIn, ctx: Session, session: SessionDep
) -> api.CalendarAccountOut:
    if await api.get_account(ctx, account_id, session=session) is None:
        raise NotFound("calendar_accounts", account_id)
    try:
        return await api.select_calendars(
            ctx,
            account_id,
            body.selected_calendar_ids,
            expected_version=body.version,
            session=session,
        )
    except ValueError as exc:
        raise ProblemError(422, "unknown_calendar", str(exc)) from None


@router.post("/accounts/{account_id}/sync", status_code=202)
@route_policy(
    RoutePolicy(
        auth="session",
        idempotent=False,
        not_idempotent_reason="enqueues a sync; a repeat within the second is the same one",
    )
)
async def sync_now(account_id: UUID, request: Request, ctx: Session) -> api.CalendarAccountOut:
    return await api.request_sync(ctx, account_id, now=_clock(request).now())
