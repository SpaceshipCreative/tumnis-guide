"""focus FastAPI router under /v1/focus; thin calls into api.py (P2-15).

- `GET /current`: what the focus bar shows (the level in force, the open session, today's
  messages with their rule attribution).
- `PUT /level` `{level}`: the workspace's focus level.
- `POST /respond` `{event_id, response, to_task_id?}`: a one-tap answer (404 for an
  unknown event).
- `POST /less` `{event_id?}`: "less of this": today's level one lower until day close.

Session-only; every write is idempotent and runs in the request's transaction. Each write
answers what `GET /current` would.
"""

from typing import Annotated

from fastapi import Depends, Request

from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.focus import api
from tumnis.modules.focus import testing as _testing  # noqa: F401  # registers `focus-wake`

router = v1_router("focus", prefix="/focus", tags=["focus"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


@router.get("/current")
@route_policy(RoutePolicy(auth="session"))
async def get_current(request: Request, ctx: Session) -> api.FocusCurrentOut:
    return await api.current(ctx, _clock(request).now())


@router.put("/level")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def put_level(
    body: api.LevelIn, request: Request, ctx: Session, session: SessionDep
) -> api.FocusCurrentOut:
    return await api.set_level(ctx, body, now=_clock(request).now(), session=session)


@router.post("/respond")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def respond(
    body: api.RespondIn, request: Request, ctx: Session, session: SessionDep
) -> api.FocusCurrentOut:
    return await api.respond(ctx, body, now=_clock(request).now(), session=session)


@router.post("/less")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def less(
    body: api.LessIn, request: Request, ctx: Session, session: SessionDep
) -> api.FocusCurrentOut:
    return await api.less_of_this(ctx, body, now=_clock(request).now(), session=session)
