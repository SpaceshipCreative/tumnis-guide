"""Workspace context applied on every transaction (P0-06, ADR-0009). Spec stub: the TDD
sequence fills it in."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.types import ActorRef


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: UUID
    actor: ActorRef


@contextmanager
def use_workspace(ctx: WorkspaceContext) -> Iterator[None]:
    raise NotImplementedError("P0-06")
    yield  # pragma: no cover


def current() -> WorkspaceContext | None:
    raise NotImplementedError("P0-06")


@asynccontextmanager
async def tenant_session(ctx: WorkspaceContext) -> AsyncIterator[AsyncSession]:
    raise NotImplementedError("P0-06")
    yield  # pragma: no cover
