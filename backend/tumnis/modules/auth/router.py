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
from tumnis.core.pagination import PageParams, page_params
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


def _lockouts(request: Request) -> bool:
    """Lockouts count unless the app switched them off (`state.auth_lockouts = False`, the
    schema fuzzer's test app only, like its `rate_limiter = None`)."""
    return getattr(request.app.state, "auth_lockouts", True) is not False


def _signed_in(signed_in: api.SignedIn) -> JSONResponse:
    response = JSONResponse(signed_in.out.model_dump(mode="json"))
    set_session_cookies(response, signed_in.token, signed_in.csrf)
    return response


@router.post("/auth/login", response_model=api.LoginOut)
@route_policy(_SIGN_IN)
async def login(body: api.LoginIn, request: Request) -> api.LoginOut:
    """The password step: answers `{"step": "totp", "preauth": ...}` and sets no cookie."""
    return await api.sign_in_password(body, clock=_clock(request), lockouts=_lockouts(request))


@router.post("/auth/totp", response_model=api.SignedInOut)
@route_policy(_SIGN_IN)
async def totp(body: api.TotpIn, request: Request) -> JSONResponse:
    """The TOTP step: sets the session and CSRF cookies."""
    return _signed_in(
        await api.sign_in_totp(body, clock=_clock(request), lockouts=_lockouts(request))
    )


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
    return _signed_in(
        await api.confirm_setup(body, clock=_clock(request), lockouts=_lockouts(request))
    )


# --- Account and second factor (P0-26) -------------------------------------------------------

_ENROL = RoutePolicy(
    auth="session",
    idempotent=False,
    not_idempotent_reason="credentials and one-time codes: a retry must check them again",
)


@router.get("/auth/account", response_model=api.AccountOut)
@route_policy(RoutePolicy(auth="session"))
async def get_account(caller: Caller) -> api.AccountOut:
    """The signed-in user's email and second factor."""
    return await api.get_account(caller)


@router.post("/auth/totp/enrol", response_model=api.TotpEnrolOut)
@route_policy(_ENROL)
async def start_totp_enrolment(
    body: api.TotpEnrolIn, request: Request, caller: Caller
) -> api.TotpEnrolOut:
    """With the password: a new TOTP secret (shown once) and its enrolment token. 401
    `invalid_credentials`, 429 `locked_out`."""
    return await api.start_totp_enrolment(
        caller, body, clock=_clock(request), lockouts=_lockouts(request)
    )


@router.post("/auth/totp/enrol/confirm", status_code=204)
@route_policy(_ENROL)
async def confirm_totp_enrolment(
    body: api.TotpEnrolConfirmIn, request: Request, caller: Caller
) -> Response:
    """A code from the new secret replaces the old one. 401 `invalid_code`,
    `invalid_enrol_token`."""
    await api.confirm_totp_enrolment(caller, body, now=_clock(request).now())
    return Response(status_code=204)


# --- API keys (P0-14, SEC-2, FR-9.3) --------------------------------------------------------
#
# Session only: no key can list, make, rotate or revoke keys (the authorization matrix
# checks it). The create and rotate responses carry the key once; an idempotent replay
# stores and answers them without it (`redact_on_replay`).

_KEY_ONCE: Final = ("key",)
_NO_GRACE: Final = api.RotateIn()  # no body: the old secret stops at once


def _invalid(exc: api.KeyInvalid) -> ProblemError:
    return ProblemError(422, exc.code, str(exc))


@router.get("/keys", response_model=api.KeyPage)
@route_policy(RoutePolicy(auth="session", paginated=True))
async def list_keys(
    ctx: Session, session: SessionDep, page: Annotated[PageParams, Depends(page_params)]
) -> api.KeyPage:
    """The workspace's API keys: name, prefix, scopes, projects, created, expires, last
    used, revoked; never the secret."""
    return await api.list_keys(session, cursor=page.cursor, limit=page.limit)


@router.post("/keys", status_code=201, response_model=api.KeyCreated)
@route_policy(RoutePolicy(auth="session", idempotent=True, redact_on_replay=_KEY_ONCE))
async def create_key(
    body: api.KeyIn, request: Request, ctx: Session, session: SessionDep
) -> api.KeyCreated:
    """A new key; the response shows `key` once. 422 `unknown_scope`."""
    try:
        return await api.create_key(ctx, body, now=_clock(request).now(), session=session)
    except api.KeyInvalid as invalid:
        raise _invalid(invalid) from None


@router.post("/keys/{id}/rotate", response_model=api.KeyCreated)
@route_policy(RoutePolicy(auth="session", idempotent=True, redact_on_replay=_KEY_ONCE))
async def rotate_key(
    id: UUID,  # the plan's path, /v1/keys/{id}; the A0.3 sweep maps {id} after keys
    request: Request,
    ctx: Session,
    session: SessionDep,
    body: api.RotateIn = _NO_GRACE,
) -> api.KeyCreated:
    """A new secret on the same key, shown once; the old one stops at once or after
    `grace_minutes` (0 to 1,440). 409 `key_revoked`."""
    try:
        return await api.rotate_key(
            ctx, id, grace_minutes=body.grace_minutes, now=_clock(request).now(), session=session
        )
    except api.KeyInvalid as invalid:
        raise _invalid(invalid) from None


@router.delete("/keys/{id}", status_code=204)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def revoke_key(
    id: UUID,  # the plan's path, /v1/keys/{id}; the A0.3 sweep maps {id} after keys
    request: Request,
    ctx: Session,
    session: SessionDep,
) -> Response:
    """Revokes the key: every process refuses it within a second."""
    await api.revoke_key(ctx, id, now=_clock(request).now(), session=session)
    return Response(status_code=204)
