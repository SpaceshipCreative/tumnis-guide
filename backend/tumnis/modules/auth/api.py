"""auth public functions and DTOs; the only file other modules may import."""

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict

from tumnis.core.tenancy import WorkspaceContext

SUBTASK_THRESHOLD_RANGE: Final = (5, 480)  # minutes (plan default bounds; FR-3.8 default 30)


class WorkspaceSettingsInvalid(ValueError):  # noqa: N818  # carries the problem code
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class WorkspaceSettingsOut(BaseModel):
    timezone: str
    subtask_threshold_min: int
    version: int


class WorkspaceSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str | None = None
    subtask_threshold_min: int | None = None
    version: int


async def get_workspace_settings(ctx: WorkspaceContext) -> WorkspaceSettingsOut:
    raise NotImplementedError


async def put_workspace_settings(
    ctx: WorkspaceContext, body: WorkspaceSettingsIn, *, now: datetime
) -> WorkspaceSettingsOut:
    raise NotImplementedError
