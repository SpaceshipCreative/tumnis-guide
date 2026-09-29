"""Per-workspace settings, sealed with the workspace data key (P0-08, SEC-6)."""

from dataclasses import dataclass

from pydantic import BaseModel

from tumnis.core.tenancy import WorkspaceContext


@dataclass(frozen=True)
class Versioned[T]:
    value: T
    version: int


async def get_setting[M: BaseModel](
    ctx: WorkspaceContext, key: str, model: type[M]
) -> Versioned[M] | None:
    raise NotImplementedError


async def put_setting(
    ctx: WorkspaceContext, key: str, value: BaseModel, *, expected_version: int | None
) -> int:
    raise NotImplementedError
