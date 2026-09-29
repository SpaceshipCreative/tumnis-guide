"""Sign-in providers and second factors (P0-13, SEC-1, Hosted readiness).

A `SignInProvider` turns credentials into a user (`LocalPasswordProvider`: email and
password; later OIDC or passkeys); a `SecondFactor` checks the proof that completes the
sign-in (`TotpFactor`; later passkeys). Both plug in through `register_provider` and
`register_factor`, so hosted mode can add or refuse providers without core changes.
`POST /v1/auth/login` picks the provider by its `provider` field (default
`local_password`); every provider's result still needs the second factor (SEC-1).
"""

import asyncio
import hmac
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final, Literal, Protocol, cast
from uuid import UUID

import pyotp
from pydantic import BaseModel
from sqlalchemy import Table, select, text, update

from tumnis.core import db
from tumnis.core.clock import Clock
from tumnis.core.settings_store import open_for_workspace
from tumnis.core.tenancy import tenant_session
from tumnis.modules.auth import passwords, rules
from tumnis.modules.auth.models import User
from tumnis.modules.auth.sessions import user_context

DeploymentMode = Literal["self-hosted", "hosted"]
USERS = cast("Table", User.__table__)
LOCAL_PASSWORD: Final = "local_password"  # noqa: S105  # a provider name
TOTP: Final = "totp"
_LOOKUP: Final = text(
    "SELECT user_id, password_hash, workspace_id FROM app.auth_login_lookup(:email)"
)


class InvalidCredentials(Exception):  # noqa: N818  # the plan's name
    """The credentials sign no one in. `user_id` and `workspace_id` name the account the
    attempt was for when it exists (audited and counted there), else None."""

    def __init__(self, user_id: UUID | None = None, workspace_id: UUID | None = None) -> None:
        super().__init__("invalid credentials")
        self.user_id = user_id
        self.workspace_id = workspace_id


class CodeReplayed(Exception):  # noqa: N818  # names the problem code totp_replayed
    """A right code whose step was already used (SEC-1)."""


class SignInResult(BaseModel):
    user_id: UUID
    workspace_id: UUID
    needs_second_factor: bool = True  # always True for local password (SEC-1)


class SignInProvider(Protocol):
    name: str  # "local_password", later "oidc", "passkey"

    def signup_allowed(self, mode: DeploymentMode) -> bool: ...

    async def authenticate(
        self, credentials: Mapping[str, str], *, source_ip: str, clock: Clock
    ) -> SignInResult: ...  # raises InvalidCredentials


class SecondFactor(Protocol):
    name: str  # "totp", later "passkey"

    async def verify(self, user_id: UUID, proof: Mapping[str, str], *, clock: Clock) -> bool: ...


_providers: dict[str, SignInProvider] = {}
_factors: dict[str, SecondFactor] = {}


def register_provider(p: SignInProvider) -> None:
    _providers[p.name] = p


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def provider(name: str) -> SignInProvider | None:
    return _providers.get(name)


def register_factor(f: SecondFactor) -> None:
    _factors[f.name] = f


def factor(name: str) -> SecondFactor | None:
    return _factors.get(name)


# --- Local password -----------------------------------------------------------------------


class LocalPasswordProvider:
    """Email and password (argon2id). An unknown email verifies against a dummy hash, so it
    costs the same as a wrong password; a right password on an old-parameter hash stores a
    new hash."""

    name = LOCAL_PASSWORD

    def signup_allowed(self, mode: DeploymentMode) -> bool:
        return mode == "self-hosted"

    async def authenticate(
        self, credentials: Mapping[str, str], *, source_ip: str, clock: Clock
    ) -> SignInResult:
        email = credentials.get("email", "").strip()
        password = credentials.get("password", "")
        async with db.app_sessionmaker()() as s, s.begin():
            row = (await s.execute(_LOOKUP, {"email": email})).first() if email else None
        stored = row.password_hash if row is not None else None
        ok, new_hash = await asyncio.to_thread(passwords.verify_and_maybe_rehash, stored, password)
        if row is None:
            raise InvalidCredentials
        if not ok:
            raise InvalidCredentials(row.user_id, row.workspace_id)
        if new_hash is not None:
            async with tenant_session(user_context(row.workspace_id, row.user_id)) as s:
                await s.execute(
                    update(USERS)
                    .where(USERS.c.id == row.user_id)
                    .values(password_hash=new_hash, updated_at=clock.now())
                )
        return SignInResult(user_id=row.user_id, workspace_id=row.workspace_id)


# --- TOTP ---------------------------------------------------------------------------------

TOTP_ISSUER: Final = "Tumnis Guide"
_OFFSETS: Final = (0, -1, 1)  # the current step, then the previous and the next one


def totp_aad(user_id: UUID) -> bytes:
    """Binds a sealed TOTP secret to its user: a value copied to another row fails."""
    return f"tumnis:totp:v1:{user_id}".encode()


def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return str(pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER))


def matched_offset(secret: str, code: str, now: datetime) -> int | None:
    """Which step (-1, 0 or +1 from now) the code belongs to, or None (30 s steps, 6
    digits, RFC 6238). Every candidate is compared, in constant time."""
    totp = pyotp.TOTP(secret)
    found = None
    for offset in _OFFSETS:
        expected = totp.at(now + timedelta(seconds=offset * rules.TOTP_PERIOD_S))
        if hmac.compare_digest(str(expected).encode(), code.strip().encode()) and found is None:
            found = offset
    return found


async def check_totp(
    user_id: UUID, workspace_id: UUID, code: str, now: datetime, *, confirmed: bool
) -> Literal["ok", "invalid", "replayed"]:
    """Checks a code against the user's sealed secret and, when it is fresh, records its
    step (one transaction, the users row locked, so a code is accepted once). With
    `confirmed=False` (setup) the secret need not be confirmed yet."""
    async with tenant_session(user_context(workspace_id, user_id)) as s:
        row = (
            await s.execute(
                select(USERS.c.totp_secret_enc, USERS.c.totp_last_step, USERS.c.totp_confirmed_at)
                .where(USERS.c.id == user_id, USERS.c.deleted_at.is_(None))
                .with_for_update()
            )
        ).first()
        if row is None or row.totp_secret_enc is None:
            return "invalid"
        if confirmed and row.totp_confirmed_at is None:
            return "invalid"
        secret = (
            await open_for_workspace(
                s, workspace_id, bytes(row.totp_secret_enc), aad=totp_aad(user_id)
            )
        ).decode()
        offset = matched_offset(secret, code, now)
        if offset is None:
            return "invalid"
        step = rules.totp_step(now)
        if not rules.totp_step_ok(step, row.totp_last_step, offset):
            return "replayed"
        values: dict[str, object] = {"totp_last_step": step + offset, "updated_at": now}
        if row.totp_confirmed_at is None:
            values["totp_confirmed_at"] = now
        await s.execute(update(USERS).where(USERS.c.id == user_id).values(**values))
    return "ok"


class TotpFactor:
    """RFC 6238 codes from the user's sealed secret; accepts the previous and next step,
    each step once. `proof` is {"code": ..., "workspace_id": <the user's workspace>}."""

    name = TOTP

    async def verify(self, user_id: UUID, proof: Mapping[str, str], *, clock: Clock) -> bool:
        outcome = await check_totp(
            user_id, UUID(proof["workspace_id"]), proof.get("code", ""), clock.now(), confirmed=True
        )
        if outcome == "replayed":
            raise CodeReplayed
        return outcome == "ok"


register_provider(LocalPasswordProvider())
register_factor(TotpFactor())
