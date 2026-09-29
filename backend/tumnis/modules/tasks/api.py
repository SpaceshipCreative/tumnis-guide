"""tasks public functions and DTOs; the only file other modules may import (P0-18).

Interfaces only until the P0-18 spec tests turn green.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.errors import ProblemError
from tumnis.core.types import ActorRef
from tumnis.modules.tasks.rules import Label, Status

__all__ = ["Label", "Status"]


class TaskCreate(BaseModel):
    schema_version: Literal[1] = 1
    project_id: UUID
    parent_id: UUID | None = None
    title: str
    label: Label | None = None
    estimate_minutes: int | None = None


class TaskOut(TaskCreate):
    id: UUID
    version: int
    status: Status


async def create_task(
    s: AsyncSession, actor: ActorRef, data: TaskCreate, *, now: datetime | None = None
) -> TaskOut:
    raise NotImplementedError


async def change_status(
    s: AsyncSession,
    actor: ActorRef,
    task_id: UUID,
    to: Status,
    version: int,
    *,
    now: datetime | None = None,
) -> TaskOut:
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ReviewKindSpec:
    kind: str
    owner_module: str
    payload_schema: type[BaseModel]
    actions: tuple[str, ...]
    impact_scope: Literal["task", "project", "workspace"]


class UnknownReviewKind(ProblemError):  # noqa: N818  # the plan's name
    def __init__(self, kind: str) -> None:
        super().__init__(422, "unknown_review_kind", f"No review kind {kind!r} is registered")


class TargetRef(BaseModel):
    type: str
    id: UUID


def register_review_kind(spec: ReviewKindSpec) -> None:
    raise NotImplementedError


def review_kinds() -> Mapping[str, ReviewKindSpec]:
    return {}


async def add_review_item(
    kind: str,
    *,
    target: TargetRef,
    project_id: UUID | None,
    payload: Mapping[str, Any],
    dedupe_key: str | None = None,
    session: AsyncSession | None = None,
) -> UUID:
    raise NotImplementedError
