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

`notifications.deliver_notification(workspace_id, notification_id, envelope,
max_attempts, base_delay_s)` (P2-16, FR-8.2, REL-3): one notification to Discord through
the master profile. Each attempt is one `notify` run of the master's `focus` skill (a
child `run_skill`, run id derived from this workflow's id and the attempt, so a replay
dispatches nothing twice), which posts the message to the one channel itself, and one
`delivery_attempts` row. A failed attempt (the run failed, timed out or was refused, the
gateway is down) is tried again after a full-jitter backoff, at most `max_attempts` times;
then the `notification.ready` delivery is dead-lettered (`core.deadletter`), so Settings
lists it with retry and discard. A retry from there is a new `deliver_event` round, hence a
new workflow id (`start_discord`) and fresh attempts. Without a master profile (none
provisioned) there is nothing to send: in-app only. `deliver_event` marks the dead letter
resolved as soon as the retry round starts this workflow; a round that fails again
re-opens it, one that succeeds resolves it for good.

The api never calls out: subscribers record and decide (api), the worker sends (here).
"""

import asyncio
import contextvars
import functools
import random
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid5

from dbos import DBOS, SetWorkflowID

from tumnis.core import deadletter
from tumnis.core.adapters.errors import AdapterError
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.backoff import full_jitter
from tumnis.core.clock import SystemClock
from tumnis.core.events import EventEnvelope
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api as agents
from tumnis.modules.notifications import api, rules
from tumnis.modules.notifications.adapters.port import PushSubscription, VapidKey, WebPushAdapter

PUSH_QUEUE: Final = "notifications"
ADAPTER: Final = "notifications.webpush"
DEFAULT_RETRY_DELAYS_S: Final = (30.0, 120.0)  # three attempts in all
DISCORD_SUBSCRIBER: Final = "notifications.deliver_notification"
DISCORD_EVENT: Final = "notification.ready"
DEFAULT_DISCORD_ATTEMPTS: Final = 3  # plan default (REL-3's per-job limit)
DEFAULT_DISCORD_BASE_S: Final = 30.0  # full jitter: up to 30 s, then up to 60 s
DISCORD_CAP_S: Final = 300.0
_RUNS: Final = UUID("6f1c8f0e-3b52-5d47-9a8e-2c4b7d1e5a90")  # uuid5 namespace of notify runs

PushFactory = Callable[[str, Any], Any]

_factory: list[PushFactory | None] = [None]
_delays: list[tuple[float, ...]] = [DEFAULT_RETRY_DELAYS_S]
_instances: dict[tuple[str, str, str], WebPushAdapter] = {}
_discord: list[tuple[int, float]] = [(DEFAULT_DISCORD_ATTEMPTS, DEFAULT_DISCORD_BASE_S)]


def use(
    factory: PushFactory | None = None, *, retry_delays_s: tuple[float, ...] | None = None
) -> None:
    """Test seam: the push adapter factory (`factory(workspace_id, vapid)`) and the waits
    between attempts (P4-05). No arguments: production (the registry's adapter, 30 s and
    2 min)."""
    _factory[0] = factory
    _delays[0] = DEFAULT_RETRY_DELAYS_S if retry_delays_s is None else retry_delays_s
    _instances.clear()
    use_discord()


def use_discord(*, max_attempts: int | None = None, base_delay_s: float | None = None) -> None:
    """Test seam: how many Discord attempts a delivery makes and the full-jitter base wait
    between them (P2-16). No arguments: production (3 attempts, 30 s base)."""
    _discord[0] = (
        DEFAULT_DISCORD_ATTEMPTS if max_attempts is None else max_attempts,
        DEFAULT_DISCORD_BASE_S if base_delay_s is None else base_delay_s,
    )


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


# --- Discord through the master (P2-16) ----------------------------------------------------


@DBOS.step(name="notifications.notify_target")
async def notify_target(workspace_id: str, notification_id: str) -> dict[str, Any]:
    """The master profile and the notify packet body; {} when there is nothing to send (no
    master provisioned, or `api.notify_request` found nothing waiting)."""
    ctx = _ctx(workspace_id)
    master = await agents.master_agent(ctx=ctx)
    if master.profile_id is None or master.availability == "not_provisioned":
        return {}
    facts = await api.notify_request(ctx, UUID(notification_id))
    if facts is None:
        return {}
    return {
        "profile_id": str(master.profile_id),
        "request": facts.request.model_dump(mode="json"),
        "tainted": facts.tainted,
    }


@DBOS.step(name="notifications.record_discord_attempt")
async def record_discord_attempt(
    workspace_id: str, notification_id: str, run_id: str, status: str, error: str | None
) -> None:
    await api.record_discord_attempt(
        _ctx(workspace_id),
        notification_id=UUID(notification_id),
        run_id=UUID(run_id),
        status="sent" if status == "sent" else "failed",
        error=error,
        now=SystemClock().now(),
    )


@DBOS.step(name="notifications.discord_delay")
async def discord_delay(attempt: int, base_delay_s: float) -> float:
    """The wait before the next attempt; the random draw is recorded by the step."""
    return full_jitter(attempt, base=base_delay_s, cap=DISCORD_CAP_S, rand=random.random)


@DBOS.step(name="notifications.discord_dead_letter")
async def discord_dead_letter(
    workspace_id: str, envelope: dict[str, Any], error: str | None, attempts: int
) -> None:
    """Open (or re-open) the dead letter of this `notification.ready` delivery (REL-3)."""
    env = EventEnvelope.model_validate(envelope)
    async with tenant_session(_ctx(workspace_id)) as s:
        await deadletter.DeadLetterRepo(s).record(
            event_id=env.event_id,
            subscriber=DISCORD_SUBSCRIBER,
            event_name=env.name,
            envelope=envelope,
            error=error or "failed",
            attempts=attempts,
        )


@DBOS.step(name="notifications.discord_resolved")
async def discord_resolved(workspace_id: str, envelope: dict[str, Any]) -> None:
    """A retry round delivered it: its dead letter, if any, is resolved."""
    env = EventEnvelope.model_validate(envelope)
    async with tenant_session(_ctx(workspace_id)) as s:
        await deadletter.DeadLetterRepo(s).resolve(env.event_id, DISCORD_SUBSCRIBER)


@DBOS.workflow(name="notifications.deliver_notification")
async def deliver_notification(  # the delivery's facts, spelled out
    workspace_id: str,
    notification_id: str,
    envelope: dict[str, Any],
    max_attempts: int,
    base_delay_s: float,
) -> str:
    """One notification to Discord through the master: "sent", "skipped" (nothing to send)
    or "dead_lettered" (see the module docstring)."""
    target = await notify_target(workspace_id, notification_id)
    if not target:
        return "skipped"
    request = agents.NotifyRequest.model_validate(target["request"])
    round_id = DBOS.workflow_id or notification_id
    error: str | None = None
    for attempt in range(1, max_attempts + 1):
        run_id = uuid5(_RUNS, f"{round_id}:{attempt}")
        packet = agents.notify_packet(
            run_id=run_id,
            profile_id=UUID(target["profile_id"]),
            request=request,
            correlation_id=f"notify:{notification_id}",
            tainted=bool(target["tainted"]),
        )
        outcome = await agents.run_notify(UUID(workspace_id), packet)
        sent = outcome.status == "succeeded"
        error = None if sent else (outcome.error or outcome.status)
        await record_discord_attempt(
            workspace_id, notification_id, str(run_id), "sent" if sent else "failed", error
        )
        if sent:
            await discord_resolved(workspace_id, envelope)
            return "sent"
        if attempt < max_attempts:
            await DBOS.sleep_async(await discord_delay(attempt, base_delay_s))
    await discord_dead_letter(workspace_id, envelope, error, max_attempts)
    return "dead_lettered"


def discord_workflow_id(round_id: str) -> str:
    return f"notifications.discord:{round_id}"


async def start_discord(envelope: EventEnvelope) -> None:
    """Queue the Discord delivery of a `notification.ready` event, from its subscriber.
    The workflow id is the subscriber's own delivery round (`<event id>:<subscriber>`, or
    `...:retry:<n>` when Settings retries its dead letter), so a re-run subscriber queues
    it once and a retry queues a fresh delivery."""
    round_id = DBOS.workflow_id or f"{envelope.event_id}:{DISCORD_SUBSCRIBER}"
    notification_id = str(envelope.payload["notification_id"])
    attempts, base = _discord[0]
    data = envelope.model_dump(mode="json")

    async def run() -> None:
        with SetWorkflowID(discord_workflow_id(round_id)):
            await DBOS.enqueue_workflow_async(
                PUSH_QUEUE,
                deliver_notification,
                str(envelope.workspace_id),
                notification_id,
                data,
                attempts,
                base,
            )

    await _fresh(run)


# --- The morning release of an unattended night (P4-04, J7, FR-8.4) ------------------------

OVERNIGHT_RELEASE_NAME: Final = "overnight-release"
OVERNIGHT_RELEASE_SCHEDULE: Final = "*/5 * * * *"  # a release goes out at most 5 minutes late


def push_options(notification_id: UUID) -> dict[str, Any]:
    """How a process without DBOS launched (the api's test tick) enqueues the push of a
    released summary through a DBOSClient: the same workflow, queue and id as `start_push`."""
    return {
        "queue_name": PUSH_QUEUE,
        "workflow_name": "notifications.deliver_push",
        "workflow_id": push_workflow_id(notification_id),
    }


def push_delays() -> list[float]:
    return list(_delays[0])


@DBOS.step(name="notifications.overnight_workspaces")
async def overnight_workspaces_step() -> list[str]:
    return [str(w) for w in await api.overnight_workspaces()]


@DBOS.step(name="notifications.release_overnight_step")
async def release_overnight_step(workspace_id: str, now: str) -> str | None:
    """One workspace's release: one summary, its `notification.ready` in the same
    transaction (Discord through the master); a replay answers the summary it made."""
    made = await api.release_overnight(_ctx(workspace_id), now=datetime.fromisoformat(now))
    return None if made is None else str(made)


@DBOS.workflow(name="notifications.release_overnight")
async def release_overnight(scheduled_time: datetime, context: Any) -> int:
    """Scheduled every 5 minutes: in each workspace, the overnight rows whose release time
    has come go out as one summary (Discord, and one browser push); returns how many
    summaries it released."""
    del context
    released = 0
    for workspace_id in await overnight_workspaces_step():
        made = await release_overnight_step(workspace_id, scheduled_time.isoformat())
        if made is not None:
            await start_push(UUID(workspace_id), UUID(made))
            released += 1
    return released


def schedules() -> list[Any]:
    """This module's DBOS schedules, applied by the worker after launch."""
    return [
        {
            "schedule_name": OVERNIGHT_RELEASE_NAME,
            "workflow_fn": release_overnight,
            "schedule": OVERNIGHT_RELEASE_SCHEDULE,
            "queue_name": PUSH_QUEUE,
        }
    ]
