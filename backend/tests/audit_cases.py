"""The SEC-3 actions and how to perform each one (P0-15). Data only: T-P0-15-07
(`test_each_action_writes_one_row`) is parametrized over `AUDIT_CASES`, so a work package
that adds an audited action appends its case here without editing the locked test.

Each case's `perform` drives the real route through the `Ctx` clients
(tumnis/core/tests/integration/_audit.py); the test then expects exactly one row with that
action, `actor_type`, the actor's id, `occurred_at` from the clock, the source address and
the `X-Request-ID` the client sent as `correlation_id`.

`PENDING` names the phase 0 actions whose operation lands with a later work package; that
work package adds the `record()` call and moves its action from `PENDING` to a case.
`setup.completed` (P0-13) has no case here: setup runs only while no user exists, so it
cannot run in a Ctx's workspace; T-P0-13-23 checks its row instead. `auth.totp_reset`
(P0-13) is a CLI action with no request (no address or correlation ID): T-P0-13-29 checks
its row.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tumnis.core.tests.integration._audit import Ctx

ActorType = Literal["user", "api_key", "task_token", "device", "system"]


@dataclass(frozen=True)
class AuditCase:
    action: str
    perform: Callable[[Ctx], Awaitable[None]]
    actor_type: ActorType


async def export_csv(ctx: Ctx) -> None:
    response = await ctx.session_client.get("/v1/audit.csv")
    response.raise_for_status()


def _open_dead_letter(ctx: Ctx) -> str:
    """One open dead letter in the case's workspace, written as the owner (P0-07)."""
    import uuid  # noqa: PLC0415

    import psycopg  # noqa: PLC0415
    from psycopg.types.json import Jsonb  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    event_id = uuid.uuid4()
    envelope = {
        "event_id": str(event_id),
        "name": "test.ping",
        "schema_version": 1,
        "workspace_id": str(ctx.workspace_id),
        "occurred_at": ctx.clock.now().isoformat(),
        "actor": "system",
        "trace_context": {},
        "payload": {"schema_version": 1, "note": "audit"},
    }
    with psycopg.connect(ctx.db.libpq(OWNER), autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO dead_letters (workspace_id, event_id, subscriber, event_name, envelope,"
            " error, attempts, last_at) VALUES (%s, %s, 'testa.record', 'test.ping', %s,"
            " 'RuntimeError: boom', 5, now()) RETURNING id",
            (ctx.workspace_id, event_id, Jsonb(envelope)),
        ).fetchone()
    assert row is not None
    return str(row[0])


async def retry_dead_letter(ctx: Ctx) -> None:
    """POST /v1/dead-letters/{id}/retry; the api enqueues through a DBOSClient, which needs
    the DBOS schema in the app's system database (a launched worker would have made it)."""
    import asyncio  # noqa: PLC0415

    from dbos import run_dbos_database_migrations  # noqa: PLC0415

    await asyncio.to_thread(run_dbos_database_migrations, ctx.app.state.settings.dbos_system_url)
    item = _open_dead_letter(ctx)
    response = await ctx.session_client.post(f"/v1/dead-letters/{item}/retry", json={"version": 1})
    response.raise_for_status()


async def discard_dead_letter(ctx: Ctx) -> None:
    item = _open_dead_letter(ctx)
    response = await ctx.session_client.post(
        f"/v1/dead-letters/{item}/discard", json={"version": 1}
    )
    response.raise_for_status()


async def change_timezone(ctx: Ctx) -> None:
    """PUT /v1/settings/workspace with a new zone: `settings.changed` and
    `workspace.timezone_changed` (P0-08)."""
    current = await ctx.session_client.get("/v1/settings/workspace")
    current.raise_for_status()
    body = {"timezone": "Australia/Sydney", "version": current.json()["version"]}
    response = await ctx.session_client.put("/v1/settings/workspace", json=body)
    response.raise_for_status()


# --- Sign-in and sessions (P0-13) --------------------------------------------------------


async def _password_step(ctx: Ctx, password: str) -> Any:
    return await ctx.session_client.post(
        "/v1/auth/login", json={"email": ctx.email, "password": password}
    )


async def sign_in(ctx: Ctx) -> None:
    """Password and TOTP through the routes: `auth.login`."""
    from tumnis.core.tests.integration._audit import sign_in as sign_in_routes  # noqa: PLC0415

    await sign_in_routes(ctx)


async def wrong_password(ctx: Ctx) -> None:
    """A wrong password on a known account: `auth.login_failed`."""
    response = await _password_step(ctx, "not-the-password")
    if response.status_code != 401:
        raise RuntimeError(response.text)


async def wrong_code(ctx: Ctx) -> None:
    """The right password, then a wrong TOTP code: `auth.totp_failed`."""
    from tests._auth import totp_code  # noqa: PLC0415

    first = await _password_step(ctx, ctx.password or "")
    first.raise_for_status()
    right = totp_code(ctx.totp_secret or "", ctx.clock.now())
    response = await ctx.session_client.post(
        "/v1/auth/totp",
        json={"preauth": first.json()["preauth"], "code": "000000" if right != "000000" else "1"},
    )
    if response.status_code != 401:
        raise RuntimeError(response.text)


async def lock_out(ctx: Ctx) -> None:
    """Five wrong passwords: the fifth locks the email, `auth.locked_out` once."""
    for _ in range(5):
        await wrong_password(ctx)


async def log_out(ctx: Ctx) -> None:
    response = await ctx.session_client.post("/v1/auth/logout")
    response.raise_for_status()


async def sign_out_other_devices(ctx: Ctx) -> None:
    response = await ctx.session_client.delete("/v1/auth/sessions")
    response.raise_for_status()


AUDIT_CASES: tuple[AuditCase, ...] = (
    AuditCase("audit.exported", export_csv, "user"),
    AuditCase("dead_letter.retried", retry_dead_letter, "user"),
    AuditCase("dead_letter.discarded", discard_dead_letter, "user"),
    AuditCase("settings.changed", change_timezone, "user"),
    AuditCase("workspace.timezone_changed", change_timezone, "user"),
    AuditCase("auth.login", sign_in, "user"),
    AuditCase("auth.login_failed", wrong_password, "user"),
    AuditCase("auth.totp_failed", wrong_code, "user"),
    AuditCase("auth.locked_out", lock_out, "user"),
    AuditCase("auth.logout", log_out, "user"),
    AuditCase("auth.sessions_revoked", sign_out_other_devices, "user"),
)

# action -> the work package that builds its operation and adds its case.
PENDING: dict[str, str] = {
    "key.created": "P0-14",
    "key.rotated": "P0-14",
    "key.revoked": "P0-14",
    # P0-08 records it in set_module_enabled; the route that toggles a module arrives with
    # the Settings screen.
    "module.toggled": "P0-26",
    "drill.completed": "P0-28",
}
