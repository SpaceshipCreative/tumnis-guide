"""notifications FastAPI routes; thin calls into api.py (P4-05, FR-8.3).

Browser push (`root_router`, under /v1 itself, as the plan names the paths):
- `GET /push/vapid-public-key`: the workspace's VAPID public key (base64url), the
  `applicationServerKey` a browser subscribes with; the key pair is made on first use.
- `POST /push/subscriptions` `{endpoint, keys: {p256dh, auth}}`: store this browser's
  subscription (201; 422 `endpoint_not_allowed` for anything but an https endpoint of a
  known push service).
- `DELETE /push/subscriptions/{push_subscription_id}`: forget one of the caller's own (204;
  404 for an unknown one or another member's; the plan's `{id}`,
  named after its table so the tenant-isolation sweep finds A's row).

Session-only; the writes are idempotent and run in the request's transaction. They only
store: the worker sends.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request, Response

from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.principal import Principal, require_principal
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.modules.auth import api as auth
from tumnis.modules.notifications import (
    api,
    testing,  # noqa: F401  # P4-04: registers the `overnight-release` test tick
)

root_router = v1_router("notifications", tags=["notifications"])

Caller = Annotated[Principal, Depends(require_principal)]


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


@root_router.get("/push/vapid-public-key")
@route_policy(RoutePolicy(auth="session"))
async def get_vapid_public_key(caller: Caller) -> api.VapidPublicKeyOut:
    account = await auth.get_account(caller)
    return await api.vapid_public_key(caller.workspace_context(), email=account.email)


@root_router.post("/push/subscriptions", status_code=201)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def subscribe(
    body: api.PushSubscriptionIn, request: Request, caller: Caller, session: SessionDep
) -> api.PushSubscriptionOut:
    account = await auth.get_account(caller)
    return await api.subscribe(
        caller.workspace_context(),
        body,
        user_id=account.user_id,
        email=account.email,
        user_agent=request.headers.get("user-agent"),
        session=session,
    )


@root_router.delete("/push/subscriptions/{push_subscription_id}", status_code=204)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def unsubscribe(
    push_subscription_id: UUID, request: Request, caller: Caller, session: SessionDep
) -> Response:
    account = await auth.get_account(caller)
    await api.unsubscribe(
        push_subscription_id,
        user_id=account.user_id,
        now=_clock(request).now(),
        session=session,
    )
    return Response(status_code=204)
