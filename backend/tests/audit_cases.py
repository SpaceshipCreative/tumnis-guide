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
(P0-13) from the CLI has no request (no address or correlation ID): T-P0-13-29 checks that
row; its case here is the Settings re-enrolment (P0-26).
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


async def toggle_module(ctx: Ctx) -> None:
    """PUT /v1/settings/modules switching a module off: `module.toggled`
    (P0-08's set_module_enabled behind P0-26's route)."""
    response = await ctx.session_client.put(
        "/v1/settings/modules", json={"module": "calendar", "enabled": False}
    )
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


async def reenrol_totp(ctx: Ctx) -> None:
    """The password, then a code from the new secret (P0-26): `auth.totp_reset`."""
    from tests._auth import secret_from_uri, totp_code  # noqa: PLC0415

    started = await ctx.session_client.post("/v1/auth/totp/enrol", json={"password": ctx.password})
    started.raise_for_status()
    secret = secret_from_uri(started.json()["otpauth_uri"])
    confirmed = await ctx.session_client.post(
        "/v1/auth/totp/enrol/confirm",
        json={
            "enrol_token": started.json()["enrol_token"],
            "code": totp_code(secret, ctx.clock.now()),
        },
    )
    confirmed.raise_for_status()


# --- API keys (P0-14) ----------------------------------------------------------------------


async def _new_key(ctx: Ctx) -> str:
    response = await ctx.session_client.post(
        "/v1/keys", json={"name": "audit case", "scopes": ["tasks:read"]}
    )
    response.raise_for_status()
    key_id: str = response.json()["id"]
    return key_id


async def create_key(ctx: Ctx) -> None:
    """POST /v1/keys: `key.created`."""
    await _new_key(ctx)


async def rotate_key(ctx: Ctx) -> None:
    """POST /v1/keys/{id}/rotate: `key.rotated`."""
    key_id = await _new_key(ctx)
    response = await ctx.session_client.post(f"/v1/keys/{key_id}/rotate", json={})
    response.raise_for_status()


async def revoke_key(ctx: Ctx) -> None:
    """DELETE /v1/keys/{id}: `key.revoked`."""
    key_id = await _new_key(ctx)
    response = await ctx.session_client.delete(f"/v1/keys/{key_id}")
    response.raise_for_status()


# --- Runners (P1-04) ----------------------------------------------------------------------


async def _new_runner(ctx: Ctx) -> str:
    response = await ctx.session_client.post("/v1/runners", json={"name": "audit-runner"})
    response.raise_for_status()
    runner_id: str = response.json()["id"]
    return runner_id


async def create_runner(ctx: Ctx) -> None:
    """POST /v1/runners: `runner.created`."""
    await _new_runner(ctx)


async def rotate_runner_token(ctx: Ctx) -> None:
    """POST /v1/runners/{id}/rotate-token: `runner.token_rotated`."""
    runner_id = await _new_runner(ctx)
    response = await ctx.session_client.post(f"/v1/runners/{runner_id}/rotate-token")
    response.raise_for_status()


# --- Purge (P2-18) --------------------------------------------------------------------------


async def purge_project(ctx: Ctx) -> None:
    """A project made and archived through the routes, then `POST /v1/purges` with a
    reason: `data.purged` (R-37)."""
    made = await ctx.session_client.post("/v1/projects", json={"name": "Purge case"})
    made.raise_for_status()
    project = made.json()
    archived = await ctx.session_client.post(
        f"/v1/projects/{project['id']}/archive", json={"version": project["version"]}
    )
    archived.raise_for_status()
    response = await ctx.session_client.post(
        "/v1/purges", json={"scope": "project", "id": project["id"], "reason": "Audit case"}
    )
    response.raise_for_status()


# --- Decision thresholds (P3-08) -------------------------------------------------------------


async def change_threshold(ctx: Ctx) -> None:
    """PUT /v1/decisions/thresholds/{point} with a reason: `threshold.changed`."""
    response = await ctx.session_client.put(
        "/v1/decisions/thresholds/project_match",
        json={"threshold": {"min_confidence": 0.8}, "reason": "audit case"},
    )
    response.raise_for_status()


# --- Approvals (P2-05) -----------------------------------------------------------------------
# `approval.granted` / `approval.denied` are the human's decide on an `approval` review item.
# The item is queued here as a run's `request_approval` would (no run is dispatched: the
# decide hook updates the approval row when there is one and writes the audit row either
# way). `agent.gated_action` and `approval.auto` are a run's own rows (actor `task_token`,
# which a Ctx has no id for, and a dispatched run); T-P2-05-12 and
# agents/tests/integration/test_default_policy.py check those.


async def _open_approval(ctx: Ctx) -> tuple[str, int]:
    """One open `approval` review item in the case's workspace; (id, version)."""
    import uuid  # noqa: PLC0415

    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

    run_id = uuid.uuid4()
    async with tenant_session(WorkspaceContext(ctx.workspace_id, SYSTEM_ACTOR)) as s:
        item_id = await tasks.add_review_item(
            "approval",
            target=tasks.TargetRef(type="run", id=run_id),
            project_id=None,
            payload={
                "approval_id": str(uuid.uuid4()),
                "run_id": str(run_id),
                "action_class": "merge_main",
                "description": "Merge the audit case branch into main",
                "rule": "gated_by_policy",
            },
            session=s,
        )
    with psycopg.connect(ctx.db.libpq(OWNER)) as conn:
        row = conn.execute("SELECT version FROM review_items WHERE id = %s", (item_id,)).fetchone()
    assert row is not None
    return str(item_id), int(row[0])


async def _decide_approval(ctx: Ctx, action: str) -> None:
    item_id, version = await _open_approval(ctx)
    response = await ctx.session_client.post(
        f"/v1/review/{item_id}/decide",
        json={"action": action, "version": version, "payload": {"reason": "Audit case"}},
    )
    response.raise_for_status()


async def grant_approval(ctx: Ctx) -> None:
    """POST /v1/review/{id}/decide `approve` with a reason: `approval.granted`."""
    await _decide_approval(ctx, "approve")


async def deny_approval(ctx: Ctx) -> None:
    """POST /v1/review/{id}/decide `deny` with a reason: `approval.denied`."""
    await _decide_approval(ctx, "deny")


# --- Approval policy (P2-05) -----------------------------------------------------------------


async def change_policy(ctx: Ctx) -> None:
    """A project made through the routes, then `PUT /v1/projects/{id}/policy` moving one
    class to gated (the policy editor): `policy.changed`."""
    made = await ctx.session_client.post("/v1/projects", json={"name": "Policy case"})
    made.raise_for_status()
    project_id = made.json()["id"]
    read = await ctx.session_client.get(f"/v1/projects/{project_id}/policy")
    read.raise_for_status()
    policy = read.json()
    response = await ctx.session_client.put(
        f"/v1/projects/{project_id}/policy",
        json={
            "gated": [*policy["gated"], "trigger_preview_deploy"],
            "allowed": [a for a in policy["allowed"] if a != "trigger_preview_deploy"],
            "version": policy["version"],
        },
    )
    response.raise_for_status()


AUDIT_CASES: tuple[AuditCase, ...] = (
    AuditCase("audit.exported", export_csv, "user"),
    AuditCase("dead_letter.retried", retry_dead_letter, "user"),
    AuditCase("dead_letter.discarded", discard_dead_letter, "user"),
    AuditCase("settings.changed", change_timezone, "user"),
    AuditCase("workspace.timezone_changed", change_timezone, "user"),
    AuditCase("module.toggled", toggle_module, "user"),
    AuditCase("auth.login", sign_in, "user"),
    AuditCase("auth.login_failed", wrong_password, "user"),
    AuditCase("auth.totp_failed", wrong_code, "user"),
    AuditCase("auth.locked_out", lock_out, "user"),
    AuditCase("auth.logout", log_out, "user"),
    AuditCase("auth.sessions_revoked", sign_out_other_devices, "user"),
    AuditCase("auth.totp_reset", reenrol_totp, "user"),
    AuditCase("key.created", create_key, "user"),
    AuditCase("key.rotated", rotate_key, "user"),
    AuditCase("key.revoked", revoke_key, "user"),
    AuditCase("runner.created", create_runner, "user"),
    AuditCase("runner.token_rotated", rotate_runner_token, "user"),
    AuditCase("data.purged", purge_project, "user"),
    AuditCase("threshold.changed", change_threshold, "user"),
    AuditCase("approval.granted", grant_approval, "user"),
    AuditCase("approval.denied", deny_approval, "user"),
    AuditCase("policy.changed", change_policy, "user"),
)

# action -> the work package that builds its operation and adds its case.
PENDING: dict[str, str] = {
    "drill.completed": "P0-28",
}
