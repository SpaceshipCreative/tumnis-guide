"""usage FastAPI router under /v1/usage; thin calls into api.py.

`GET /v1/usage?from=YYYY-MM-DD&to=YYYY-MM-DD` (`auth="session"`): the signed-in workspace's
counters per UTC day, both ends inclusive. Until P0-10's v1_router and route policy and
P0-13's sessions land, the route sits on a plain router and takes the signed-in context
from the same session seam as the audit routes (tumnis.core.audit_router.require_session:
401 until P0-13). A workspace (or deployment) with the usage module off gets 404.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from tumnis.core.audit_router import require_session
from tumnis.core.errors import ProblemError
from tumnis.core.modules import require_module
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.usage import api

router = APIRouter(
    prefix="/v1/usage", tags=["usage"], dependencies=[Depends(require_module("usage"))]
)

Session = Annotated[WorkspaceContext, Depends(require_session)]


@router.get("")
async def get_usage(
    ctx: Session,
    day_from: Annotated[date, Query(alias="from")],
    day_to: Annotated[date, Query(alias="to")],
) -> list[api.UsageRow]:
    if day_to < day_from:
        raise ProblemError(422, "invalid_range", "`to` is before `from`")
    async with tenant_session(ctx) as session:
        return await api.report(session, day_from, day_to)
