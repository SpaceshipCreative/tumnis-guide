"""auth FastAPI routers; thin calls into api.py.

Sign-in, sessions and setup (P0-13): `router` (below). The workspace settings resource
(R-14): GET and PUT /v1/settings/workspace (`auth="session"`,
PUT idempotent), declared on `v1_router` (P0-10). The signed-in context comes from the shared
session seam (tumnis.core.audit_router.require_session). A stale version raises
`StaleVersion`, which the problem handlers answer as 409 `stale_version` with `current`.
"""

from typing import Annotated, Final
from uuid import UUID

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.errors import ProblemError
from tumnis.core.idempotency import SessionDep
from tumnis.core.principal import Principal, require_principal
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.auth import api
from tumnis.modules.auth.csrf import clear_session_cookies, set_session_cookies

settings_router = v1_router("auth", prefix="/settings", tags=["settings"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


@settings_router.get("/workspace")
@route_policy(RoutePolicy(auth="session"))
async def get_workspace_settings(ctx: Session) -> api.WorkspaceSettingsOut:
    return await api.get_workspace_settings(ctx)


@settings_router.put("/workspace")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def put_workspace_settings(
    body: api.WorkspaceSettingsIn, request: Request, ctx: Session, session: SessionDep
) -> api.WorkspaceSettingsOut:
    try:
        return await api.put_workspace_settings(
            ctx, body, now=request.app.state.clock.now(), session=session
        )
    except api.WorkspaceSettingsInvalid as invalid:
        raise ProblemError(422, invalid.code, str(invalid)) from None


# --- Sign-in, sessions and first-run setup (P0-13) ----------------------------------------
#
# /v1/auth/login, /v1/auth/totp and /v1/setup(/totp) run before any session exists: no
# CSRF token can exist yet, so they are exempt, and TumnisRoute refuses them from another
# Origin instead (403 `bad_origin`). They share the `login` rate bucket (P0-10) on top of
# the lockouts. Session routes are `auth="session"`: an API key never reaches them.

router = v1_router("auth", tags=["auth"])

PRE_SESSION: Final = "no session (and so no CSRF token) exists yet; the Origin is checked"
_SIGN_IN = RoutePolicy(
    auth="none",
    idempotent=False,
    not_idempotent_reason="credentials: a retry must check them again, never replay",
    csrf=False,
    csrf_exempt_reason=PRE_SESSION,
    rate_limit="login",
)
_SETUP = RoutePolicy(
    auth="none",
    idempotent=False,
    not_idempotent_reason="first-run bootstrap: runs once, then answers 409",
    csrf=False,
    csrf_exempt_reason=PRE_SESSION,
    rate_limit="login",
)
Caller = Annotated[Principal, Depends(require_principal)]


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _signed_in(signed_in: api.SignedIn) -> JSONResponse:
    response = JSONResponse(signed_in.out.model_dump(mode="json"))
    set_session_cookies(response, signed_in.token, signed_in.csrf)
    return response


@router.post("/auth/login", response_model=api.LoginOut)
@route_policy(_SIGN_IN)
async def login(body: api.LoginIn, request: Request) -> api.LoginOut:
    """The password step: answers `{"step": "totp", "preauth": ...}` and sets no cookie."""
    return await api.sign_in_password(body, clock=_clock(request))


@router.post("/auth/totp", response_model=api.SignedInOut)
@route_policy(_SIGN_IN)
async def totp(body: api.TotpIn, request: Request) -> JSONResponse:
    """The TOTP step: sets the session and CSRF cookies."""
    return _signed_in(await api.sign_in_totp(body, clock=_clock(request)))


@router.post("/auth/logout", status_code=204)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def logout(request: Request, caller: Caller, session: SessionDep) -> Response:
    """Ends this session and clears its cookies."""
    await api.logout(session, caller, now=_clock(request).now())
    response = Response(status_code=204)
    clear_session_cookies(response)
    return response


@router.get("/auth/sessions", response_model=api.SessionPage)
@route_policy(RoutePolicy(auth="session", paginated=True))
async def list_sessions(
    request: Request,
    caller: Caller,
    session: SessionDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=api.SESSIONS_LIMIT_MAX)] = api.SESSIONS_LIMIT_DEFAULT,
) -> api.SessionPage:
    """This user's signed-in devices, newest first, the calling one `current`."""
    return await api.list_sessions(
        session, caller, now=_clock(request).now(), cursor=cursor, limit=limit
    )


@router.delete("/auth/sessions", status_code=204)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def sign_out_other_devices(request: Request, caller: Caller, session: SessionDep) -> Response:
    """Revokes every session of this user but the calling one (R-21)."""
    await api.revoke_other_sessions(session, caller, now=_clock(request).now())
    return Response(status_code=204)


@router.delete("/auth/sessions/{session_id}", status_code=204)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def revoke_session(
    session_id: UUID, request: Request, caller: Caller, session: SessionDep
) -> Response:
    """Revokes one of this user's sessions."""
    await api.revoke_session(session, caller, session_id, now=_clock(request).now())
    return Response(status_code=204)


@router.post("/setup", status_code=201, response_model=api.SetupOut)
@route_policy(_SETUP)
async def setup(body: api.SetupIn, request: Request) -> api.SetupOut:
    """First run: creates the workspace and its owner and shows the TOTP secret once.
    409 `already_set_up`; 403 `signup_disabled` in hosted mode."""
    mode = request.app.state.settings.deployment_mode
    return await api.start_setup(body, mode=mode, clock=_clock(request))


@router.post("/setup/totp", response_model=api.SignedInOut)
@route_policy(_SETUP)
async def setup_totp(body: api.SetupTotpIn, request: Request) -> JSONResponse:
    """Confirms the first code; completes setup and signs the owner in."""
    return _signed_in(await api.confirm_setup(body, clock=_clock(request)))
