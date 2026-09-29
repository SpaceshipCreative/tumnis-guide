"""auth public functions and DTOs; the only file other modules may import."""

import asyncio
import logging
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from typing import Any, Final, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Table, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from tumnis.core import audit, crypto, request_meta
from tumnis.core.cache import invalidate_on_commit
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.ids import uuid7
from tumnis.core.outbox import emit
from tumnis.core.pagination import Page
from tumnis.core.principal import AuthFailure, Principal, register_resolver
from tumnis.core.settings_store import SETTINGS_CACHE, seal_for_workspace, settings_cache_key
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.core.versioning import StaleVersion, Version, update_versioned
from tumnis.modules.auth import sessions, throttle
from tumnis.modules.auth.csrf import SESSION_COOKIE, sign, unsign
from tumnis.modules.auth.events import AuthFailedV1
from tumnis.modules.auth.models import Membership, User, Workspace
from tumnis.modules.auth.passwords import hash_password
from tumnis.modules.auth.providers import (
    LOCAL_PASSWORD,
    TOTP,
    CodeReplayed,
    DeploymentMode,
    InvalidCredentials,
    SignInResult,
    check_totp,
    factor,
    new_totp_secret,
    provider,
    provisioning_uri,
    totp_aad,
)
from tumnis.modules.auth.rules import is_iana_zone
from tumnis.modules.auth.sessions import user_context
from tumnis.seed import (
    SeedWriterUnavailableError,
    UserSeed,
    WorkspaceSeed,
    register_seed_writer,
)

SUBTASK_THRESHOLD_RANGE: Final = (5, 480)  # minutes (plan default bounds; FR-3.8 default 30)
WORKSPACES = cast("Table", Workspace.__table__)
# The resource's entry in the settings cache (ws:<id>:settings:workspace).
WORKSPACE_SETTINGS_KEY: Final = "workspace"


class WorkspaceSettingsInvalid(ValueError):  # noqa: N818  # carries the problem code
    """A value the resource refuses; `code` is the problem code (422)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class WorkspaceSettingsOut(BaseModel):
    timezone: str  # IANA name (REL-6, FR-4.7)
    subtask_threshold_min: int  # FR-3.8
    version: int  # the workspaces row version: one optimistic lock for the resource


class WorkspaceSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str | None = None
    subtask_threshold_min: int | None = None
    version: Version


@cache
def _zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def _out(row: Any) -> WorkspaceSettingsOut:
    return WorkspaceSettingsOut(
        timezone=row["timezone"],
        subtask_threshold_min=row["subtask_threshold_min"],
        version=row["version"],
    )


def _validate(body: WorkspaceSettingsIn) -> None:
    if body.timezone is not None and not is_iana_zone(body.timezone, _zones()):
        raise WorkspaceSettingsInvalid(
            "invalid_timezone", f"{body.timezone!r} is not an IANA time zone name"
        )
    low, high = SUBTASK_THRESHOLD_RANGE
    threshold = body.subtask_threshold_min
    if threshold is not None and not low <= threshold <= high:
        raise WorkspaceSettingsInvalid(
            "validation_error", f"subtask_threshold_min must be {low} to {high} minutes"
        )


async def get_workspace_settings(ctx: WorkspaceContext) -> WorkspaceSettingsOut:
    """The workspace's timezone, subtask threshold and version, through the settings cache
    (so a changed timezone reaches the next planner tick in every process, REL-6)."""
    key = settings_cache_key(ctx.workspace_id, WORKSPACE_SETTINGS_KEY)
    cached = await SETTINGS_CACHE.get(key)
    if cached is not None:
        return WorkspaceSettingsOut.model_validate_json(cached)
    token = SETTINGS_CACHE.token()
    columns = (WORKSPACES.c.timezone, WORKSPACES.c.subtask_threshold_min, WORKSPACES.c.version)
    async with tenant_session(ctx) as session:
        row = (
            (await session.execute(select(*columns).where(WORKSPACES.c.id == ctx.workspace_id)))
            .mappings()
            .one()
        )
    settings = _out(row)
    await SETTINGS_CACHE.fill(key, settings.model_dump_json().encode(), since=token)
    return settings


async def put_workspace_settings(
    ctx: WorkspaceContext,
    body: WorkspaceSettingsIn,
    *,
    now: datetime,
    session: AsyncSession | None = None,
) -> WorkspaceSettingsOut:
    """Partial update of the workspaces row at `body.version` (stale: StaleVersion with the
    current resource, 409). Validates the zone (`invalid_timezone`) and the threshold range
    (`validation_error`), both WorkspaceSettingsInvalid (422), and invalidates the settings
    cache on commit. Later keys that live in workspace_settings join this resource and bump
    the same row version. Audited in the same transaction (SEC-3): `settings.changed` with
    the changed fields, and `workspace.timezone_changed` with both zones when the zone
    moves. With `session` (the request's idempotent transaction) it writes there; else in
    a transaction of its own."""
    _validate(body)
    if session is not None:
        return await _put_workspace_settings(session, ctx, body, now)
    async with tenant_session(ctx) as own:
        return await _put_workspace_settings(own, ctx, body, now)


async def _put_workspace_settings(
    session: AsyncSession, ctx: WorkspaceContext, body: WorkspaceSettingsIn, now: datetime
) -> WorkspaceSettingsOut:
    changes = body.model_dump(exclude={"version"}, exclude_none=True)
    values: dict[str, Any] = {**changes, "version": WORKSPACES.c.version + 1, "updated_at": now}
    target = ("workspace", ctx.workspace_id)
    before: str = (
        await session.execute(
            select(WORKSPACES.c.timezone)
            .where(WORKSPACES.c.id == ctx.workspace_id)
            # FOR NO KEY UPDATE: an idempotent request's own row holds FOR KEY SHARE on
            # this row through its foreign key (P0-11's fuzzer found the wait).
            .with_for_update(key_share=True)
        )
    ).scalar_one()
    try:
        row = await update_versioned(session, WORKSPACES, ctx.workspace_id, body.version, values)
    except StaleVersion as stale:
        raise StaleVersion(current=_out(stale.current).model_dump()) from None
    await audit.record(
        session,
        "settings.changed",
        target=target,
        details={"fields": sorted(changes)},
        occurred_at=now,
    )
    if row["timezone"] != before:
        await audit.record(
            session,
            "workspace.timezone_changed",
            target=target,
            details={"from": before, "to": row["timezone"]},
            occurred_at=now,
        )
    await invalidate_on_commit(
        session, settings_cache_key(ctx.workspace_id, WORKSPACE_SETTINGS_KEY)
    )
    return _out(row)


# --- Identity, sign-in and sessions (P0-13, SEC-1, FR-9.1, FR-9.2) -------------------------
#
# Sign-in is two steps. The password step (any registered provider) answers a pre-auth
# token and never a session; the TOTP step turns a valid pre-auth token and a fresh code
# into a session cookie and a CSRF cookie. Failures count toward lockouts per email, per
# address and per user's second factor (auth.throttle); on a known account they are
# audited and emitted as `auth.failed` in that account's workspace. First-run setup
# creates the workspace, its owner and a TOTP secret, and no session exists until the
# first code is confirmed.

PREAUTH_TTL: Final = timedelta(minutes=5)  # plan default
SETUP_TTL: Final = timedelta(hours=1)  # plan default: time to scan the code and confirm
MIN_PASSWORD_LENGTH: Final = 12  # plan default
SESSIONS_LIMIT_DEFAULT, SESSIONS_LIMIT_MAX = 50, 200
USERS = cast("Table", User.__table__)
MEMBERSHIPS = cast("Table", Membership.__table__)
_USER_EXISTS: Final = text("SELECT app.auth_user_exists()")
_SETUP_LOCK: Final = text("SELECT pg_advisory_xact_lock(hashtextextended('tumnis:setup', 0))")

log = logging.getLogger(__name__)


class SetupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(max_length=1024)
    timezone: str = "UTC"  # from the browser (Intl), validated as an IANA name
    workspace_name: str = Field(default="My workspace", min_length=1, max_length=200)


class SetupOut(BaseModel):
    otpauth_uri: str  # shown once: the TOTP secret for the authenticator app
    setup_token: str  # proves this setup to POST /v1/setup/totp


class SetupTotpIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    setup_token: str = Field(max_length=2048)
    code: str = Field(max_length=16)


class LoginIn(BaseModel):
    """`provider` picks the sign-in provider; the other string fields are its credentials
    (`email` and `password` for `local_password`)."""

    model_config = ConfigDict(extra="allow")
    provider: str = LOCAL_PASSWORD
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=1024)

    def credentials(self) -> dict[str, str]:
        values = self.model_dump(exclude={"provider"}, exclude_none=True)
        return {key: value for key, value in values.items() if isinstance(value, str)}


class LoginOut(BaseModel):
    step: Literal["totp"] = "totp"
    preauth: str


class TotpIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preauth: str = Field(max_length=2048)
    code: str = Field(max_length=16)


class SignedInOut(BaseModel):
    user_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class SignedIn:
    """A new session: the body to answer and the two cookie values."""

    out: SignedInOut
    token: str
    csrf: str


class SessionOut(BaseModel):
    id: UUID
    device_label: str | None
    user_agent: str | None
    source_ip: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool


SessionPage = Page[SessionOut]


def _invalid_credentials() -> ProblemError:
    # One body for an unknown email, a wrong password and an unknown provider (SEC-1).
    return ProblemError(401, "invalid_credentials", "The email or password is not right")


def _locked(exc: throttle.LockedOut) -> ProblemError:
    minutes = max(1, -(-exc.retry_after_s // 60))
    return ProblemError(
        429,
        "locked_out",
        f"Too many failed attempts; try again in {minutes} minutes",
        headers={"Retry-After": str(exc.retry_after_s)},
    )


def _address() -> str | None:
    return request_meta.current().source_ip


async def _record_failure(
    workspace_id: UUID,
    user_id: UUID,
    action: str,
    *,
    step: Literal["password", "totp"],
    locked: bool,
    details: dict[str, str],
    now: datetime,
) -> None:
    """The failure on a known account: its audit row (and `auth.locked_out` when this
    failure locked it) and `auth.failed`, in the account's workspace."""
    target = ("user", user_id)
    async with tenant_session(user_context(workspace_id, user_id)) as s:
        await audit.record(s, action, target=target, details=details, occurred_at=now)
        if locked:
            await audit.record(
                s, "auth.locked_out", target=target, details={"step": step}, occurred_at=now
            )
        payload = AuthFailedV1(user_id=user_id, source_ip=_address(), step=step, locked=locked)
        await emit(s, payload, occurred_at=now)


async def _totp_confirmed(user_id: UUID, workspace_id: UUID) -> bool:
    async with tenant_session(user_context(workspace_id, user_id)) as s:
        confirmed = (
            await s.execute(select(USERS.c.totp_confirmed_at).where(USERS.c.id == user_id))
        ).scalar_one_or_none()
    return confirmed is not None


async def sign_in_password(body: LoginIn, *, clock: Clock) -> LoginOut:
    """The password step: a pre-auth token (5 minutes) for the TOTP step, never a session.
    401 `invalid_credentials` (the same for an unknown email), 403 `setup_incomplete`
    before the first code is confirmed, 429 `locked_out` with Retry-After."""
    now = clock.now()
    credentials = body.credentials()
    chosen = provider(body.provider)
    keys = [throttle.address_key(_address())]
    if credentials.get("email"):
        keys.insert(0, throttle.email_key(credentials["email"]))
    result: SignInResult | None = None
    refused: InvalidCredentials | None = None
    try:
        async with throttle.Attempt(keys, now) as attempt:
            try:
                if chosen is None:
                    raise InvalidCredentials
                result = await chosen.authenticate(
                    credentials, source_ip=_address() or "", clock=clock
                )
            except InvalidCredentials as exc:
                refused = exc
                attempt.failed()
            else:
                attempt.succeeded(*keys[:-1])  # the email's counter; the address keeps its
    except throttle.LockedOut as exc:
        raise _locked(exc) from None
    if refused is not None or result is None:
        if refused is not None and refused.user_id and refused.workspace_id:
            await _record_failure(
                refused.workspace_id,
                refused.user_id,
                "auth.login_failed",
                step="password",
                locked=bool(attempt.locked_now),
                details={"provider": body.provider},
                now=now,
            )
        else:
            log.info("auth.login_failed for no known account", extra={"source": _address()})
        raise _invalid_credentials()
    if not await _totp_confirmed(result.user_id, result.workspace_id):
        raise ProblemError(403, "setup_incomplete", "Confirm the first code to finish setup")
    claims = {"u": str(result.user_id), "w": str(result.workspace_id), "pr": body.provider}
    return LoginOut(preauth=sign("preauth", claims, expires_at=now + PREAUTH_TTL))


async def _open_session(
    user_id: UUID, workspace_id: UUID, *, action: str, details: dict[str, str], now: datetime
) -> SignedIn:
    meta = request_meta.current()
    async with tenant_session(user_context(workspace_id, user_id)) as s:
        new = await sessions.create(
            s,
            user_id=user_id,
            now=now,
            user_agent=meta.user_agent,
            source_ip=meta.source_ip,
            second_factor=TOTP,
        )
        await audit.record(
            s,
            action,
            target=("user", user_id),
            details={**details, "session_id": str(new.id)},
            occurred_at=now,
        )
    return SignedIn(SignedInOut(user_id=user_id, workspace_id=workspace_id), new.token, new.csrf)


async def _second_factor(
    user_id: UUID, workspace_id: UUID, code: str, *, clock: Clock, setup: bool
) -> None:
    """Checks the code under the user's TOTP lockout; raises 401 `invalid_code` or
    `totp_replayed` (audited as `auth.totp_failed`), or 429 `locked_out`."""
    now = clock.now()
    key = throttle.totp_key(user_id)
    outcome = "invalid"
    try:
        async with throttle.Attempt([key], now) as attempt:
            if setup:
                outcome = await check_totp(user_id, workspace_id, code, now, confirmed=False)
            else:
                chosen = factor(TOTP)
                try:
                    proof = {"code": code, "workspace_id": str(workspace_id)}
                    ok = chosen is not None and await chosen.verify(user_id, proof, clock=clock)
                    outcome = "ok" if ok else "invalid"
                except CodeReplayed:
                    outcome = "replayed"
            if outcome == "ok":
                attempt.succeeded(key)
            else:
                attempt.failed()
    except throttle.LockedOut as exc:
        raise _locked(exc) from None
    if outcome == "ok":
        return
    await _record_failure(
        workspace_id,
        user_id,
        "auth.totp_failed",
        step="totp",
        locked=bool(attempt.locked_now),
        details={"reason": outcome},
        now=now,
    )
    if outcome == "replayed":
        raise ProblemError(401, "totp_replayed", "This code was used already; wait for the next")
    raise ProblemError(401, "invalid_code", "The code is not right")


async def sign_in_totp(body: TotpIn, *, clock: Clock) -> SignedIn:
    """The TOTP step: a pre-auth token from the password step and a fresh code open a
    session (audited as `auth.login`)."""
    now = clock.now()
    claims = unsign("preauth", body.preauth, now=now)
    if claims is None:
        raise ProblemError(401, "invalid_preauth", "Sign in with your password again")
    user_id, workspace_id = UUID(claims["u"]), UUID(claims["w"])
    await _second_factor(user_id, workspace_id, body.code, clock=clock, setup=False)
    details = {"provider": str(claims.get("pr", LOCAL_PASSWORD)), "second_factor": TOTP}
    return await _open_session(user_id, workspace_id, action="auth.login", details=details, now=now)


def _validate_setup(body: SetupIn) -> None:
    if not is_iana_zone(body.timezone, _zones()):
        raise ProblemError(422, "invalid_timezone", f"{body.timezone!r} is not an IANA time zone")
    if "@" not in body.email.strip()[1:-1]:
        raise ProblemError(422, "validation_error", "email must be an email address")
    if len(body.password) < MIN_PASSWORD_LENGTH:
        detail = f"The password needs at least {MIN_PASSWORD_LENGTH} characters"
        raise ProblemError(422, "password_too_short", detail)


async def start_setup(body: SetupIn, *, mode: DeploymentMode, clock: Clock) -> SetupOut:
    """First run: the workspace, its owner and a TOTP secret, only while no user exists
    (409 `already_set_up`; 403 `signup_disabled` when the deployment refuses local
    sign-up, as hosted mode does). The otpauth URI is shown once."""
    local = provider(LOCAL_PASSWORD)
    if local is None or not local.signup_allowed(mode):
        raise ProblemError(403, "signup_disabled", "This deployment does not allow sign-up")
    _validate_setup(body)
    now = clock.now()
    email = body.email.strip()
    password_hash = await asyncio.to_thread(hash_password, body.password)
    secret = new_totp_secret()
    workspace_id, user_id = uuid7(), uuid7()
    async with tenant_session(user_context(workspace_id, user_id)) as s:
        await s.execute(_SETUP_LOCK)  # two first runs at once: one wins
        if (await s.execute(_USER_EXISTS)).scalar_one():
            raise ProblemError(409, "already_set_up", "This deployment has its owner already")
        await s.execute(
            WORKSPACES.insert().values(
                id=workspace_id,
                name=body.workspace_name,
                timezone=body.timezone,
                deployment_mode=mode,
            )
        )
        await _insert_user(
            s,
            workspace_id,
            user_id,
            email=email,
            password_hash=password_hash,
            totp_secret=secret,
            confirmed_at=None,
        )
    token = sign("setup", {"u": str(user_id), "w": str(workspace_id)}, expires_at=now + SETUP_TTL)
    return SetupOut(otpauth_uri=provisioning_uri(secret, email), setup_token=token)


async def _insert_user(
    s: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    *,
    email: str,
    password_hash: str,
    totp_secret: str | None,
    confirmed_at: datetime | None,
) -> None:
    values: dict[str, Any] = {
        "id": user_id,
        "email": email,
        "password_hash": password_hash,
        "home_workspace_id": workspace_id,
        "totp_confirmed_at": confirmed_at,
    }
    if totp_secret is not None:
        version, sealed = await seal_for_workspace(
            s, workspace_id, totp_secret.encode(), aad=totp_aad(user_id)
        )
        values |= {"totp_secret_enc": sealed, "totp_key_version": version}
    await s.execute(USERS.insert().values(**values))
    await s.execute(MEMBERSHIPS.insert().values(user_id=user_id, role="owner"))


async def confirm_setup(body: SetupTotpIn, *, clock: Clock) -> SignedIn:
    """The first code: completes setup (`setup.completed`) and signs the owner in."""
    now = clock.now()
    claims = unsign("setup", body.setup_token, now=now)
    if claims is None:
        raise ProblemError(401, "invalid_setup_token", "Start setup again")
    user_id, workspace_id = UUID(claims["u"]), UUID(claims["w"])
    if await _totp_confirmed(user_id, workspace_id):
        raise ProblemError(409, "already_set_up", "Setup is complete; sign in instead")
    await _second_factor(user_id, workspace_id, body.code, clock=clock, setup=True)
    return await _open_session(
        user_id, workspace_id, action="setup.completed", details={"second_factor": TOTP}, now=now
    )


async def resolve_principal(request: Request) -> Principal | AuthFailure | None:
    """The authentication middleware's session resolver: the `__Host-tumnis_session`
    cookie, or nothing to say when there is none."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return await sessions.resolve(token, request.app.state.clock.now())


register_resolver("session", resolve_principal)


def _session_of(principal: Principal) -> tuple[UUID, UUID]:
    if principal.kind != "session" or principal.subject_id is None or principal.session_id is None:
        raise ProblemError(403, "session_required", "Only a signed-in session can do this")
    return principal.subject_id, principal.session_id


async def logout(s: AsyncSession, principal: Principal, *, now: datetime) -> None:
    """Revokes the calling session (`auth.logout`), in the caller's transaction."""
    user_id, session_id = _session_of(principal)
    await sessions.revoke(s, user_id, session_id, now)
    await audit.record(
        s,
        "auth.logout",
        target=("user", user_id),
        details={"session_id": str(session_id)},
        occurred_at=now,
    )


async def list_sessions(
    s: AsyncSession, principal: Principal, *, now: datetime, cursor: str | None, limit: int
) -> SessionPage:
    """The caller's active sessions, newest first, with the calling one marked current."""
    user_id, session_id = _session_of(principal)
    rows, next_cursor = await sessions.page(s, user_id, now, cursor=cursor, limit=limit)
    items = [
        SessionOut(
            id=row.id,
            device_label=row.device_label,
            user_agent=row.user_agent,
            source_ip=row.source_ip,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            current=row.id == session_id,
        )
        for row in rows
    ]
    return SessionPage(items=items, next_cursor=next_cursor)


async def revoke_other_sessions(s: AsyncSession, principal: Principal, *, now: datetime) -> int:
    """Signs out every other device of the caller (R-21), `auth.sessions_revoked`."""
    user_id, session_id = _session_of(principal)
    revoked = await sessions.revoke_others(s, user_id, session_id, now)
    await audit.record(
        s,
        "auth.sessions_revoked",
        target=("user", user_id),
        details={"scope": "others", "count": len(revoked)},
        occurred_at=now,
    )
    return len(revoked)


async def revoke_session(
    s: AsyncSession, principal: Principal, target: UUID, *, now: datetime
) -> None:
    """Signs out one of the caller's sessions; 404 `not_found` for any other id."""
    user_id, _ = _session_of(principal)
    if not await sessions.revoke(s, user_id, target, now):
        raise ProblemError(404, "not_found", "No such session")
    await audit.record(
        s,
        "auth.sessions_revoked",
        target=("user", user_id),
        details={"scope": "one", "revoked": str(target), "count": 1},
        occurred_at=now,
    )


async def create_user(
    workspace_id: UUID,
    *,
    email: str,
    password: str,
    totp_secret: str | None = None,
    user_id: UUID | None = None,
    now: datetime,
) -> UUID:
    """An owner of an existing workspace (seed data, tests, the admin CLI): its password
    hashed and, when given, its TOTP secret sealed and confirmed."""
    user_id = user_id or uuid7()
    password_hash = await asyncio.to_thread(hash_password, password)
    async with tenant_session(user_context(workspace_id, user_id)) as s:
        confirmed = now if totp_secret is not None else None
        await _insert_user(
            s,
            workspace_id,
            user_id,
            email=email,
            password_hash=password_hash,
            totp_secret=totp_secret,
            confirmed_at=confirmed,
        )
    return user_id


async def enroll_totp(
    user_id: UUID, workspace_id: UUID, secret: str, *, confirmed_at: datetime | None
) -> None:
    """Seal `secret` as the user's TOTP secret (confirmed at `confirmed_at`, or awaiting
    its first code when None); the step history starts over."""
    async with tenant_session(user_context(workspace_id, user_id)) as s:
        version, sealed = await seal_for_workspace(
            s, workspace_id, secret.encode(), aad=totp_aad(user_id)
        )
        await s.execute(
            update(USERS)
            .where(USERS.c.id == user_id)
            .values(
                totp_secret_enc=sealed,
                totp_key_version=version,
                totp_confirmed_at=confirmed_at,
                totp_last_step=0,
            )
        )


# --- Seed writers (P0-04's seed sets; P0-13 stores the workspace and its user) ------------


async def seed_workspace(rec: WorkspaceSeed) -> UUID:
    """The seed set's workspace, made as the system actor."""
    workspace_id = uuid7()
    async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
        await s.execute(
            WORKSPACES.insert().values(id=workspace_id, name=rec.name, timezone=rec.timezone)
        )
    return workspace_id


async def seed_user(workspace_id: UUID, rec: UserSeed) -> UUID:
    """A seed user who can sign in at once: its password and its confirmed TOTP secret
    come from the seed file (test-only credentials). Without a master key the secret
    cannot be sealed, and the user is skipped (SeedWriterUnavailableError)."""
    try:
        crypto.master_keys()
    except crypto.MasterKeyError as exc:
        raise SeedWriterUnavailableError(f"seed user not stored: {exc}") from exc
    return await create_user(
        workspace_id,
        email=rec.email,
        password=rec.password,
        totp_secret=rec.totp_secret,
        now=SystemClock().now(),
    )


register_seed_writer("workspace", seed_workspace)
register_seed_writer("user", seed_user)
