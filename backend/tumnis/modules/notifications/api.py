"""notifications public functions and DTOs; the only file other modules may import.

Browser push (P4-05, FR-8.3) on P2-16's delivery seam (FR-8.4):
- the workspace's VAPID key (RFC 8292) is generated on first use and kept in the sealed
  workspace settings (`notifications.vapid`, like every other server secret: AES-256-GCM
  under the workspace data key); only its public half leaves, for
  `pushManager.subscribe({ applicationServerKey })`;
- `subscribe` / `unsubscribe` only store a browser's subscription: the api never calls out,
  the worker sends (`workflows.deliver_push`);
- `record_review_item` / `record_focus_event` write one `notifications` row per event (its
  id the dedupe key), with the level in force and `rules.delivery_decision`'s answer;
- `flush` releases what Quiet held once `rules.flush_due` says the break came (or the level
  stopped batching), as one batch notification;
- `push_targets`, `subscription`, `record_attempt` and `vapid_key` serve the worker's steps.
Nothing here reads a clock: `now` is passed in.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.errors import ProblemError
from tumnis.core.settings_store import get_setting, put_setting
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.versioning import NotFound, StaleVersion
from tumnis.modules.focus import api as focus
from tumnis.modules.notifications import rules
from tumnis.modules.notifications.models import DeliveryAttempt, Notification, PushSubscription
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

__all__ = [
    "VAPID_SETTING",
    "AttemptStatus",
    "PushKeysIn",
    "PushSubscriptionIn",
    "PushSubscriptionOut",
    "Recorded",
    "SubscriptionView",
    "VapidPublicKeyOut",
    "VapidSettings",
    "flush",
    "push_targets",
    "record_attempt",
    "record_focus_event",
    "record_review_item",
    "subscribe",
    "subscription",
    "unsubscribe",
    "vapid_key",
    "vapid_public_key",
]

VAPID_SETTING: Final = "notifications.vapid"
CHANNEL_PUSH: Final = "push"
P256_POINT_BYTES: Final = 65  # an uncompressed P-256 public key (W3C Push API `p256dh`)
AUTH_SECRET_BYTES: Final = 16  # the subscription's `auth` secret (RFC 8291)
FAR_FUTURE: Final = datetime.max.replace(tzinfo=UTC)

AttemptStatus = Literal["sent", "gone", "failed", "rejected"]

_notifications: Table = Notification.__table__  # type: ignore[assignment]
_subscriptions: Table = PushSubscription.__table__  # type: ignore[assignment]
_attempts: Table = DeliveryAttempt.__table__  # type: ignore[assignment]


# --- DTOs ----------------------------------------------------------------------------------


class VapidSettings(BaseModel):
    """The workspace's VAPID key pair (sealed in `workspace_settings`): the raw P-256
    private value and the uncompressed public point, both base64url, and the `sub`
    contact claim."""

    model_config = ConfigDict(extra="forbid")
    private_key: str
    public_key: str
    subject: str


class VapidPublicKeyOut(BaseModel):
    public_key: str  # base64url, the `applicationServerKey` the browser subscribes with


class PushKeysIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=1, max_length=128)
    auth: str = Field(min_length=1, max_length=64)


class PushSubscriptionIn(BaseModel):
    """A browser's `PushSubscription.toJSON()`: its endpoint and keys."""

    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeysIn


class PushSubscriptionOut(BaseModel):
    id: UUID
    endpoint: str
    created_at: datetime


@dataclass(frozen=True)
class Recorded:
    """A notification row: new or written by an earlier run of the same subscriber."""

    id: UUID
    decision: rules.Decision


@dataclass(frozen=True)
class SubscriptionView:
    id: UUID
    endpoint: str
    p256dh: str
    auth: str


# --- VAPID ---------------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _new_vapid(subject: str) -> VapidSettings:
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return VapidSettings(private_key=_b64url(raw), public_key=_b64url(public), subject=subject)


async def vapid_key(ctx: WorkspaceContext, *, subject: str | None = None) -> VapidSettings | None:
    """The workspace's VAPID key; with `subject` (`mailto:` the signed-in user) it is made
    on first use. A concurrent first use keeps the key that was stored first."""
    found = await get_setting(ctx, VAPID_SETTING, VapidSettings)
    if found is not None or subject is None:
        return None if found is None else found.value
    made = _new_vapid(subject)
    try:
        await put_setting(ctx, VAPID_SETTING, made, expected_version=None)
    except StaleVersion:
        again = await get_setting(ctx, VAPID_SETTING, VapidSettings)
        if again is None:  # pragma: no cover  # stored, then gone: a deleted workspace
            raise
        return again.value
    return made


async def vapid_public_key(ctx: WorkspaceContext, *, email: str) -> VapidPublicKeyOut:
    """`GET /v1/push/vapid-public-key`: the key browsers subscribe with, made on first use."""
    key = await vapid_key(ctx, subject=f"mailto:{email}")
    assert key is not None  # noqa: S101  # made above when absent
    return VapidPublicKeyOut(public_key=key.public_key)


# --- Subscriptions -------------------------------------------------------------------------


def _check_keys(keys: PushKeysIn) -> None:
    try:
        point, secret = _unb64url(keys.p256dh), _unb64url(keys.auth)
        ok = len(point) == P256_POINT_BYTES and len(secret) == AUTH_SECRET_BYTES
        if ok:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except (ValueError, binascii.Error):
        ok = False
    if not ok:
        raise ProblemError(
            422, "subscription_keys_invalid", "The subscription's keys are not valid Web Push keys"
        )


async def subscribe(
    ctx: WorkspaceContext,
    body: PushSubscriptionIn,
    *,
    user_id: UUID,
    email: str,
    user_agent: str | None,
    session: AsyncSession,
) -> PushSubscriptionOut:
    """`POST /v1/push/subscriptions`: store (or refresh) this browser's subscription. Only
    an https endpoint of a known push service is kept (422 `endpoint_not_allowed`, SEC-5);
    the workspace's VAPID key is made now if no browser asked for it yet."""
    if not rules.endpoint_allowed(body.endpoint):
        raise ProblemError(
            422, "endpoint_not_allowed", "The endpoint is not a known browser push service"
        )
    _check_keys(body.keys)
    await vapid_key(ctx, subject=f"mailto:{email}")
    agent = None if user_agent is None else user_agent[:512]
    stmt = (
        pg_insert(_subscriptions)
        .values(
            user_id=user_id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            user_agent=agent,
        )
        .on_conflict_do_update(
            index_elements=["workspace_id", "endpoint"],
            index_where=_subscriptions.c.deleted_at.is_(None),
            set_={
                "user_id": user_id,
                "p256dh": body.keys.p256dh,
                "auth": body.keys.auth,
                "user_agent": agent,
                "failures": 0,
            },
        )
        .returning(_subscriptions.c.id, _subscriptions.c.endpoint, _subscriptions.c.created_at)
    )
    row = (await session.execute(stmt)).one()
    return PushSubscriptionOut(id=row.id, endpoint=row.endpoint, created_at=row.created_at)


async def unsubscribe(
    subscription_id: UUID, *, user_id: UUID, now: datetime, session: AsyncSession
) -> None:
    """`DELETE /v1/push/subscriptions/{id}`: forget one of this user's browser
    subscriptions (404 for one that is unknown, gone or another member's)."""
    done = await session.execute(
        update(_subscriptions)
        .where(
            _subscriptions.c.id == subscription_id,
            _subscriptions.c.user_id == user_id,
            _subscriptions.c.deleted_at.is_(None),
        )
        .values(deleted_at=now)
        .returning(_subscriptions.c.id)
    )
    if done.first() is None:
        raise NotFound("push_subscription", subscription_id)


# --- Recording notifications (the subscribers) ---------------------------------------------


@dataclass(frozen=True)
class _State:
    level: rules.Level
    in_progress: bool


async def _state(s: AsyncSession, ctx: WorkspaceContext, now: datetime) -> _State:
    level = (await focus.current(ctx, now, session=s)).level
    busy = await tasks.list_tasks(s, status=tasks.Status.IN_PROGRESS, limit=1)
    return _State(level=level, in_progress=busy.total > 0)


async def _record(  # one notification's facts
    s: AsyncSession,
    *,
    kind: str,
    target_type: str | None,
    target_id: UUID | None,
    project_id: UUID | None,
    payload: rules.PushPayload,
    dedupe_key: str,
    state: _State,
) -> Recorded:
    decision = rules.delivery_decision(state.level, state.in_progress, kind)
    row = (
        await s.execute(
            pg_insert(_notifications)
            .values(
                kind=kind,
                target_type=target_type,
                target_id=target_id,
                project_id=project_id,
                level=state.level,
                decision=decision,
                payload=payload.model_dump(mode="json"),
                dedupe_key=dedupe_key,
            )
            .on_conflict_do_nothing(index_elements=["workspace_id", "dedupe_key"])
            .returning(_notifications.c.id, _notifications.c.decision)
        )
    ).first()
    if row is None:  # an earlier run of this subscriber wrote it
        row = (
            await s.execute(
                select(_notifications.c.id, _notifications.c.decision).where(
                    _notifications.c.dedupe_key == dedupe_key
                )
            )
        ).one()
    return Recorded(id=row.id, decision=row.decision)


async def record_review_item(  # the event's facts
    ctx: WorkspaceContext,
    *,
    item_id: UUID,
    kind: str,
    project_id: UUID | None,
    dedupe_key: str,
    now: datetime,
) -> Recorded:
    """A new review item (`review_item.added`): its notification and P2-16's decision."""
    async with tenant_session(ctx) as s:
        names = await projects.project_names(s, [project_id] if project_id else [])
        source = rules.ReviewItemLite(
            id=item_id, kind=kind, project_name=names.get(project_id) if project_id else None
        )
        return await _record(
            s,
            kind=kind,
            target_type="review_item",
            target_id=item_id,
            project_id=project_id,
            payload=rules.push_payload(source),
            dedupe_key=dedupe_key,
            state=await _state(s, ctx, now),
        )


async def record_focus_event(
    ctx: WorkspaceContext, *, event_id: UUID, kind: str, dedupe_key: str, now: datetime
) -> Recorded:
    """A focus event (`focus.event`): its notification and P2-16's decision."""
    payload = rules.push_payload(rules.FocusEventLite(id=event_id, kind=kind))
    async with tenant_session(ctx) as s:
        return await _record(
            s,
            kind=payload.kind,
            target_type="focus_event",
            target_id=event_id,
            project_id=None,
            payload=payload,
            dedupe_key=dedupe_key,
            state=await _state(s, ctx, now),
        )


async def flush(
    ctx: WorkspaceContext, *, dedupe_key: str, now: datetime, day_ended: bool = False
) -> UUID | None:
    """The next natural break (FR-8.4): when `rules.flush_due` says so, or the level in
    force no longer batches, every held notification is released as one batch
    notification (its id); None when nothing is held or the break has not come. A re-run
    for the same event answers the batch it made."""
    async with tenant_session(ctx) as s:
        made = (
            await s.execute(
                select(_notifications.c.id).where(_notifications.c.dedupe_key == dedupe_key)
            )
        ).scalar_one_or_none()
        if made is not None:
            return UUID(str(made))
        held = (
            await s.execute(
                select(_notifications.c.id, _notifications.c.kind, _notifications.c.created_at)
                .where(
                    _notifications.c.decision == "batch",
                    _notifications.c.released_at.is_(None),
                    _notifications.c.deleted_at.is_(None),
                )
                .order_by(_notifications.c.created_at, _notifications.c.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not held:
            return None
        state = await _state(s, ctx, now)
        batch = [rules.NotificationView(r.id, r.kind, r.created_at) for r in held]
        due = rules.flush_due(batch, state.in_progress, now, now if day_ended else FAR_FUTURE)
        if not due and rules.delivery_decision(state.level, state.in_progress, "batch") != "now":
            return None
        await s.execute(
            update(_notifications)
            .where(_notifications.c.id.in_([r.id for r in held]))
            .values(released_at=now)
        )
        payload = rules.batch_payload(len(held))
        row = (
            await s.execute(
                pg_insert(_notifications)
                .values(
                    kind=payload.kind,
                    level=state.level,
                    decision="now",
                    payload=payload.model_dump(mode="json"),
                    dedupe_key=dedupe_key,
                )
                .returning(_notifications.c.id)
            )
        ).one()
        return UUID(str(row.id))


# --- Delivering (the worker's steps) -------------------------------------------------------


async def push_targets(
    ctx: WorkspaceContext, notification_id: UUID
) -> tuple[dict[str, Any], list[UUID]]:
    """The push a notification carries and the live subscriptions it goes to."""
    async with tenant_session(ctx) as s:
        payload = (
            await s.execute(
                select(_notifications.c.payload).where(_notifications.c.id == notification_id)
            )
        ).scalar_one_or_none()
        if payload is None:
            return {}, []
        ids = (
            await s.execute(
                select(_subscriptions.c.id)
                .where(_subscriptions.c.deleted_at.is_(None))
                .order_by(_subscriptions.c.created_at, _subscriptions.c.id)
            )
        ).scalars()
        return dict(payload), [UUID(str(i)) for i in ids]


async def subscription(ctx: WorkspaceContext, subscription_id: UUID) -> SubscriptionView | None:
    """A live subscription; None once it is gone."""
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                select(_subscriptions).where(
                    _subscriptions.c.id == subscription_id, _subscriptions.c.deleted_at.is_(None)
                )
            )
        ).first()
    if row is None:
        return None
    return SubscriptionView(id=row.id, endpoint=row.endpoint, p256dh=row.p256dh, auth=row.auth)


async def record_attempt(  # one attempt's facts
    ctx: WorkspaceContext,
    *,
    notification_id: UUID,
    subscription_id: UUID,
    status: AttemptStatus,
    status_code: int | None,
    now: datetime,
) -> None:
    """One push attempt (its own row) and what it says of the subscription: `sent` clears
    its failures and stamps `last_success_at`; `gone` deletes it (RFC 8030 404/410);
    `failed` and `rejected` count a failure."""
    async with tenant_session(ctx) as s:
        await s.execute(
            pg_insert(_attempts).values(
                notification_id=notification_id,
                channel=CHANNEL_PUSH,
                subscription_id=subscription_id,
                status=status,
                status_code=status_code,
                attempted_at=now,
            )
        )
        live = _subscriptions.c.id == subscription_id, _subscriptions.c.deleted_at.is_(None)
        if status == "sent":
            change: dict[str, Any] = {"failures": 0, "last_success_at": now}
        elif status == "gone":
            change = {"deleted_at": now}
        else:
            change = {"failures": _subscriptions.c.failures + 1}
        await s.execute(update(_subscriptions).where(*live).values(**change))
