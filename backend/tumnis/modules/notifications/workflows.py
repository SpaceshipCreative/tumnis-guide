"""notifications DBOS workflows and steps (P4-05, FR-8.3; [DBOS workflows]
(https://docs.dbos.dev/python/tutorials/workflow-tutorial)).

`notifications.deliver_push(workspace_id, notification_id, retry_delays_s)`: one
notification pushed to every live subscription of the workspace, through the
`notifications.webpush` adapter. Each attempt is a step that records its own
`delivery_attempts` row: `sent` and `gone` end that subscription's delivery; `failed`
(timeouts, 429, 5xx) is tried again after each of `retry_delays_s` (a durable
`DBOS.sleep_async`), so a subscription gets at most `len(retry_delays_s) + 1` attempts;
`rejected` is not retried. Workflow id `notifications.push:<notification id>`, so a
re-run subscriber never pushes twice.

The api never calls out: subscribers record and decide (api), the worker sends (here).
"""

import asyncio
import contextvars
import functools
from collections.abc import Callable, Coroutine
from typing import Any, Final
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from tumnis.core.adapters.errors import AdapterError
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.clock import SystemClock
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.notifications import api, rules
from tumnis.modules.notifications.adapters.port import PushSubscription, VapidKey, WebPushAdapter

PUSH_QUEUE: Final = "notifications"
ADAPTER: Final = "notifications.webpush"
DEFAULT_RETRY_DELAYS_S: Final = (30.0, 120.0)  # three attempts in all

PushFactory = Callable[[str, Any], Any]

_factory: list[PushFactory | None] = [None]
_delays: list[tuple[float, ...]] = [DEFAULT_RETRY_DELAYS_S]
_instances: dict[tuple[str, str, str], WebPushAdapter] = {}


def use(
    factory: PushFactory | None = None, *, retry_delays_s: tuple[float, ...] | None = None
) -> None:
    """Test seam: the push adapter factory (`factory(workspace_id, vapid)`) and the waits
    between attempts (P4-05). No arguments: production (the registry's adapter, 30 s and
    2 min)."""
    _factory[0] = factory
    _delays[0] = DEFAULT_RETRY_DELAYS_S if retry_delays_s is None else retry_delays_s
    _instances.clear()


@functools.cache
def _net_policy() -> NetPolicy:
    from tumnis.settings import Settings  # noqa: PLC0415  # the worker's own settings

    return Settings().net_policy()  # read from the environment


def _adapter(workspace_id: str, vapid: VapidKey) -> WebPushAdapter:
    factory = _factory[0]
    if factory is not None:
        adapter: WebPushAdapter = factory(workspace_id, vapid)
        return adapter
    key = (workspace_id, vapid.private_key, vapid.subject)
    if key not in _instances:
        mode = current_mode()
        deps: dict[str, Any] = {"vapid": vapid}
        if mode == "real":
            deps["policy"] = _net_policy()
        _instances[key] = resolve(ADAPTER, mode, **deps)
    return _instances[key]


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


# --- Steps ---------------------------------------------------------------------------------


@DBOS.step(name="notifications.push_targets")
async def push_targets(workspace_id: str, notification_id: str) -> dict[str, Any]:
    payload, ids = await api.push_targets(_ctx(workspace_id), UUID(notification_id))
    return {"payload": payload, "subscriptions": [str(i) for i in ids]}


@DBOS.step(name="notifications.push_once")
async def push_once(
    workspace_id: str, notification_id: str, subscription_id: str, payload: dict[str, Any]
) -> str:
    """One attempt to one subscription, recorded; its status ("skipped" when the
    subscription or the VAPID key is gone)."""
    ctx = _ctx(workspace_id)
    sub = await api.subscription(ctx, UUID(subscription_id))
    vapid = await api.vapid_key(ctx)
    if sub is None or vapid is None:
        return "skipped"
    adapter = _adapter(workspace_id, VapidKey(private_key=vapid.private_key, subject=vapid.subject))
    status: api.AttemptStatus
    code: int | None
    try:
        result = await adapter.send(
            PushSubscription(endpoint=sub.endpoint, p256dh=sub.p256dh, auth=sub.auth),
            rules.PushPayload.model_validate(payload),
            rules.PUSH_TTL_S,
        )
        status, code = result.outcome, result.status_code
    except AdapterError as exc:
        status = "failed" if exc.retryable else "rejected"
        code = int(exc.message) if exc.message.isdigit() else None
    await api.record_attempt(
        ctx,
        notification_id=UUID(notification_id),
        subscription_id=sub.id,
        status=status,
        status_code=code,
        now=SystemClock().now(),
    )
    return status


# --- Workflow ------------------------------------------------------------------------------


@DBOS.workflow(name="notifications.deliver_push")
async def deliver_push(
    workspace_id: str, notification_id: str, retry_delays_s: list[float]
) -> None:
    """Every live subscription once, then the failed ones again after each delay."""
    targets = await push_targets(workspace_id, notification_id)
    if not targets["payload"]:
        return
    waiting: list[str] = targets["subscriptions"]
    for delay in [*retry_delays_s, None]:
        failed = [
            sub
            for sub in waiting
            if await push_once(workspace_id, notification_id, sub, targets["payload"]) == "failed"
        ]
        if not failed or delay is None:
            return
        await DBOS.sleep_async(delay)
        waiting = failed


# --- Starting it (from subscribers) --------------------------------------------------------


async def _fresh(make: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run outside the subscriber's DBOS step: DBOS refuses to start a workflow from one."""
    await asyncio.get_running_loop().create_task(make(), context=contextvars.Context())


def push_workflow_id(notification_id: UUID) -> str:
    return f"notifications.push:{notification_id}"


async def start_push(workspace_id: UUID, notification_id: UUID) -> None:
    """Queue the notification's push (once: its workflow id is the notification's)."""

    async def run() -> None:
        with SetWorkflowID(push_workflow_id(notification_id)):
            await DBOS.enqueue_workflow_async(
                PUSH_QUEUE,
                deliver_push,
                str(workspace_id),
                str(notification_id),
                list(_delays[0]),
            )

    await _fresh(run)
