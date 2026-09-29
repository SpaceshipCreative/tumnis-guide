"""Sign-in helpers for the auth spec tests and the `session_client` fixture (P0-13).

No assertions live here: spec-guard locks the test bodies, and these helpers adapt to the
routes. Cookies and header names follow R-18.

- `Account`: what a test needs to sign in as one user.
- `totp_code(secret, at)`: the RFC 6238 code at an instant (pyotp, 30 s, 6 digits).
- `run_setup(client, clock, ...)`: `POST /v1/setup` and `POST /v1/setup/totp`; the clock
  moves on one TOTP step afterwards, so the next sign-in has a fresh code.
- `sign_in(client, account, clock)`: password then TOTP at `clock.now()`; returns the
  TOTP step's response (cookies land in the client's jar).
- `SessionClient`: an httpx client that adds `X-CSRF-Token` (from its own
  `__Host-tumnis_csrf` cookie) and an `Idempotency-Key` to every write that lacks them.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qs, urlparse

import httpx

if TYPE_CHECKING:
    from tumnis.core.clock import FixedClock

SESSION_COOKIE: Final = "__Host-tumnis_session"
CSRF_COOKIE: Final = "__Host-tumnis_csrf"
CSRF_HEADER: Final = "X-CSRF-Token"
WRITE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TEST_PASSWORD: Final = "test-password-not-a-secret"
TOTP_STEP: Final = timedelta(seconds=30)
BASE_URL: Final = "https://test"


@dataclass(frozen=True)
class Account:
    email: str
    password: str
    totp_secret: str
    user_id: uuid.UUID
    workspace_id: uuid.UUID


def totp_code(secret: str, at: Any) -> str:
    import pyotp  # noqa: PLC0415

    return str(pyotp.TOTP(secret).at(at))


def secret_from_uri(uri: str) -> str:
    """The base32 secret of an `otpauth://totp/...?secret=...` URI."""
    return parse_qs(urlparse(uri).query)["secret"][0]


async def start_setup(
    client: httpx.AsyncClient,
    *,
    email: str = "owner@example.test",
    password: str = TEST_PASSWORD,
    timezone: str = "America/New_York",
    name: str = "Test",
) -> httpx.Response:
    return await client.post(
        "/v1/setup",
        json={"email": email, "password": password, "timezone": timezone, "workspace_name": name},
    )


async def run_setup(
    client: httpx.AsyncClient,
    clock: FixedClock,
    *,
    email: str = "owner@example.test",
    password: str = TEST_PASSWORD,
    timezone: str = "America/New_York",
) -> Account:
    """First-run setup through the routes; returns the new account. The confirmation signs
    the client in; the clock then moves one TOTP step on."""
    started = await start_setup(client, email=email, password=password, timezone=timezone)
    started.raise_for_status()
    body = started.json()
    secret = secret_from_uri(body["otpauth_uri"])
    confirmed = await client.post(
        "/v1/setup/totp",
        json={"setup_token": body["setup_token"], "code": totp_code(secret, clock.now())},
    )
    confirmed.raise_for_status()
    clock.advance(TOTP_STEP)
    ids = confirmed.json()
    return Account(
        email,
        password,
        secret,
        uuid.UUID(ids["user_id"]),
        uuid.UUID(ids["workspace_id"]),
    )


async def password_step(
    client: httpx.AsyncClient, email: str, password: str, **extra: str
) -> httpx.Response:
    return await client.post("/v1/auth/login", json={"email": email, "password": password, **extra})


async def totp_step(client: httpx.AsyncClient, preauth: str, code: str) -> httpx.Response:
    return await client.post("/v1/auth/totp", json={"preauth": preauth, "code": code})


async def sign_in(client: httpx.AsyncClient, account: Account, clock: FixedClock) -> httpx.Response:
    """Password, then the TOTP code at the clock's time; raises on any failure."""
    first = await password_step(client, account.email, account.password)
    first.raise_for_status()
    second = await totp_step(
        client, first.json()["preauth"], totp_code(account.totp_secret, clock.now())
    )
    second.raise_for_status()
    return second


class SessionClient(httpx.AsyncClient):
    """Adds `X-CSRF-Token` and `Idempotency-Key` to writes that do not carry them."""

    account: Account | None = None

    def __init__(self, **kwargs: Any) -> None:
        hooks = kwargs.pop("event_hooks", {}) or {}
        hooks.setdefault("request", []).append(self._add_write_headers)
        super().__init__(event_hooks=hooks, **kwargs)

    @property
    def csrf(self) -> str | None:
        return self.cookies.get(CSRF_COOKIE)

    async def _add_write_headers(self, request: httpx.Request) -> None:
        if request.method not in WRITE_METHODS:
            return
        token = self.csrf
        if token is not None and CSRF_HEADER not in request.headers:
            request.headers[CSRF_HEADER] = token
        if "Idempotency-Key" not in request.headers:
            request.headers["Idempotency-Key"] = f"test-{uuid.uuid4()}"


def session_client_for(app: Any, **headers: str) -> SessionClient:
    return SessionClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, headers=headers)


def seed_user() -> tuple[str, str, str]:
    """(email, password, totp_secret) of the seed user in fixtures/seed/workspace.yaml."""
    from pathlib import Path  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    path = Path(__file__).resolve().parents[1] / "fixtures" / "seed" / "workspace.yaml"
    user = yaml.safe_load(path.read_text())["users"][0]
    return user["email"], user["password"], user["totp_secret"]


def seed_user_totp(clock: FixedClock) -> str:
    """The seed user's TOTP code at the clock's time."""
    return totp_code(seed_user()[2], clock.now())


def run_async[T](make: Callable[[], Awaitable[T]]) -> T:
    """Run a coroutine to completion on a new event loop in a helper thread, so a plain
    fixture can do async work even while the test's own loop is running."""
    import asyncio  # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_call(make))).result()


async def _call[T](make: Callable[[], Awaitable[T]]) -> T:
    return await make()


async def open_session(app: Any, workspace_id: uuid.UUID, user_id: uuid.UUID) -> tuple[str, str]:
    """A session row for the user made through the auth module (no sign-in route runs, so
    nothing is audited); returns (session cookie, CSRF token)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.auth import sessions  # noqa: PLC0415

    async with tenant_session(sessions.user_context(workspace_id, user_id)) as s:
        new = await sessions.create(
            s,
            user_id=user_id,
            now=app.state.clock.now(),
            user_agent=None,
            source_ip=None,
            second_factor="totp",
        )
    return new.token, new.csrf


async def enroll_totp(user_id: uuid.UUID, workspace_id: uuid.UUID, at: Any) -> str:
    """A fresh confirmed TOTP secret for the user (through the auth api); returns it."""
    import pyotp  # noqa: PLC0415

    from tumnis.modules.auth import api  # noqa: PLC0415

    secret = pyotp.random_base32()
    await api.enroll_totp(user_id, workspace_id, secret, confirmed_at=at)
    return secret
