"""Task tokens and device tokens (P0-14, R-27). Spec skeleton."""

from datetime import datetime
from uuid import UUID

from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.auth.keys import NewSecret


class ScopeEscalation(PermissionError):  # noqa: N818  # the plan's name
    """A task token asked for a scope its issuing key does not hold."""


async def issue_task_token(
    ctx: WorkspaceContext,
    *,
    run_id: UUID,
    project_id: UUID,
    api_key_id: UUID,
    scopes: frozenset[str],
    now: datetime,
) -> NewSecret:
    raise NotImplementedError("P0-14")


async def revoke_task_tokens_for_run(ctx: WorkspaceContext, run_id: UUID, *, now: datetime) -> int:
    raise NotImplementedError("P0-14")


async def issue_device_token(ctx: WorkspaceContext, *, runner_id: UUID, now: datetime) -> NewSecret:
    raise NotImplementedError("P0-14")
