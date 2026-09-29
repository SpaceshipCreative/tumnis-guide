"""usage FastAPI router under /v1/usage; thin calls into api.py.

`GET /v1/usage?from=YYYY-MM-DD&to=YYYY-MM-DD` (`auth="session"`): the signed-in workspace's
counters per UTC day, both ends inclusive. Declared on `v1_router` (P0-10, with the usage
module's on/off flag: a workspace or deployment with usage off gets 404); the signed-in
context comes from the shared session seam (tumnis.core.audit_router.require_session). The
answer is a list bounded by the day range, not a cursor page.
"""

from datetime import date
from typing import Annotated

from fastapi import Depends, Query

from tumnis.core.audit_router import require_session
from tumnis.core.errors import ProblemError
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.usage import api

router = v1_router("usage", prefixed=True, tags=["usage"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


@router.get("")
@route_policy(
    RoutePolicy(
        auth="session",
        unpaginated_reason="one row per counter and day, bounded by the from/to range",
    )
)
async def get_usage(
    ctx: Session,
    day_from: Annotated[date, Query(alias="from")],
    day_to: Annotated[date, Query(alias="to")],
) -> list[api.UsageRow]:
    if day_to < day_from:
        raise ProblemError(422, "invalid_range", "`to` is before `from`")
    async with tenant_session(ctx) as session:
        return await api.report(session, day_from, day_to)
