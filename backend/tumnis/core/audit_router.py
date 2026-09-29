"""`GET /v1/audit` and `GET /v1/audit.csv` (P0-15). Stubs until the implementation lands."""

from fastapi import APIRouter, Request

from tumnis.core.tenancy import WorkspaceContext

router = APIRouter(prefix="/v1", tags=["audit"])


async def require_session(request: Request) -> WorkspaceContext:
    raise NotImplementedError("P0-15")
