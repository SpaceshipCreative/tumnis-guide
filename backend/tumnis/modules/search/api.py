"""search public functions and DTOs; the only file other modules may import (P0-20).

Interfaces only until the P0-20 spec tests turn green.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.events import EventEnvelope
from tumnis.core.pagination import Page


class SearchHit(BaseModel):
    entity_type: Literal["task", "project"]
    entity_id: UUID
    project_id: UUID | None
    title: str
    snippet: str
    score: float


async def search(
    s: AsyncSession,
    q: str,
    *,
    scope: Literal["all", "tasks", "projects"] = "all",
    project_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = 20,
    now: datetime,
) -> Page[SearchHit]:
    raise NotImplementedError


async def typeahead_projects(
    s: AsyncSession, q: str, limit: int = 8, *, now: datetime
) -> list[SearchHit]:
    raise NotImplementedError


async def typeahead_tasks(
    s: AsyncSession, q: str, project_id: UUID | None, limit: int = 8, *, now: datetime
) -> list[SearchHit]:
    raise NotImplementedError


async def index_event(s: AsyncSession, envelope: EventEnvelope) -> int:
    raise NotImplementedError
