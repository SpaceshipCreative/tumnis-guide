"""integrations FastAPI router; thin calls into api.py.

`POST /v1/purges {scope, id, reason}` (R-37, P2-18): the one purge route, session only (an
API key is refused, whatever its scopes), idempotent, answering 202 once the purge is
recorded and audited; the deletes it starts run in the worker. P2-18 purges an archived
project; P3-09 adds scope `connection` and `GET /v1/purges/{id}`.
"""

from fastapi import Request

from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.modules.integrations import api

router = v1_router("integrations", tags=["purges"])

PURGE = RoutePolicy(auth="session", idempotent=True)


@router.post("/purges", status_code=202)
@route_policy(PURGE)
async def purge(body: api.PurgeIn, request: Request, session: SessionDep) -> api.PurgeOut:
    clock: Clock = request.app.state.clock
    return await api.purge(session, body, now=clock.now())
