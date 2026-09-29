"""The SEC-3 actions and how to perform each one (P0-15). Data only: T-P0-15-07
(`test_each_action_writes_one_row`) is parametrized over `AUDIT_CASES`, so a work package
that adds an audited action appends its case here without editing the locked test.

Each case's `perform` drives the real route through the `Ctx` clients
(tumnis/core/tests/integration/_audit.py); the test then expects exactly one row with that
action, `actor_type`, the actor's id, `occurred_at` from the clock, the source address and
the `X-Request-ID` the client sent as `correlation_id`.

`PENDING` names the phase 0 actions whose operation lands with a later work package; that
work package adds the `record()` call and moves its action from `PENDING` to a case.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

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


AUDIT_CASES: tuple[AuditCase, ...] = (AuditCase("audit.exported", export_csv, "user"),)

# action -> the work package that builds its operation and adds its case.
PENDING: dict[str, str] = {
    "auth.login": "P0-13",
    "auth.login_failed": "P0-13",
    "auth.totp_failed": "P0-13",
    "auth.locked_out": "P0-13",
    "auth.logout": "P0-13",
    "auth.sessions_revoked": "P0-13",
    "setup.completed": "P0-13",
    "key.created": "P0-14",
    "key.rotated": "P0-14",
    "key.revoked": "P0-14",
    "settings.changed": "P0-08",
    "workspace.timezone_changed": "P0-08",
    "module.toggled": "P0-08",
    "dead_letter.retried": "P0-07",
    "dead_letter.discarded": "P0-07",
    "drill.completed": "P0-28",
}
