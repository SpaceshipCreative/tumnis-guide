"""Dead letters: list, retry and discard (P0-07, REL-3)."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel

from tumnis.core.tenancy import WorkspaceContext

if TYPE_CHECKING:
    from dbos import DBOSClient


class DeadLetterOut(BaseModel):
    id: UUID
    event_id: UUID
    event_name: str
    subscriber: str
    error: str
    attempts: int
    retries: int
    last_at: datetime
    status: str
    version: int


class Page(BaseModel):
    items: list[DeadLetterOut]
    next_cursor: str | None = None


class DeadLetterNotOpen(Exception):  # noqa: N818  # plan name
    status = 409
    code = "dead_letter_not_open"


class StaleVersion(Exception):  # noqa: N818  # plan name (P0-10 versioning)
    status = 409
    code = "stale_version"


def use_client(client: "DBOSClient") -> None:
    raise NotImplementedError("P0-07")


async def list_dead_letters(
    ctx: WorkspaceContext, *, status: str = "open", cursor: str | None = None, limit: int = 50
) -> Page:
    raise NotImplementedError("P0-07")


async def retry(
    ctx: WorkspaceContext, dead_letter_id: UUID, *, expected_version: int
) -> DeadLetterOut:
    raise NotImplementedError("P0-07")


async def discard(
    ctx: WorkspaceContext, dead_letter_id: UUID, *, expected_version: int
) -> DeadLetterOut:
    raise NotImplementedError("P0-07")
