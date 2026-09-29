"""Live updates (P0-22, ADR-0004). Spec stub: implemented in the P0-22 green steps."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


def mark_changed(session: AsyncSession, entity: str, id: UUID) -> None:
    raise NotImplementedError(f"mark_changed {entity} {id} ({session}): spec:P0-22")
