"""auth public functions and DTOs; the only file other modules may import."""

import zoneinfo
from datetime import datetime
from functools import cache
from typing import Any, Final, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Table, select

from tumnis.core import audit
from tumnis.core.cache import invalidate_on_commit
from tumnis.core.settings_store import SETTINGS_CACHE, settings_cache_key
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.versioning import StaleVersion, update_versioned
from tumnis.modules.auth.models import Workspace
from tumnis.modules.auth.rules import is_iana_zone

SUBTASK_THRESHOLD_RANGE: Final = (5, 480)  # minutes (plan default bounds; FR-3.8 default 30)
WORKSPACES = cast("Table", Workspace.__table__)
# The resource's entry in the settings cache (ws:<id>:settings:workspace).
WORKSPACE_SETTINGS_KEY: Final = "workspace"


class WorkspaceSettingsInvalid(ValueError):  # noqa: N818  # carries the problem code
    """A value the resource refuses; `code` is the problem code (422)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class WorkspaceSettingsOut(BaseModel):
    timezone: str  # IANA name (REL-6, FR-4.7)
    subtask_threshold_min: int  # FR-3.8
    version: int  # the workspaces row version: one optimistic lock for the resource


class WorkspaceSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str | None = None
    subtask_threshold_min: int | None = None
    version: int


@cache
def _zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def _out(row: Any) -> WorkspaceSettingsOut:
    return WorkspaceSettingsOut(
        timezone=row["timezone"],
        subtask_threshold_min=row["subtask_threshold_min"],
        version=row["version"],
    )


def _validate(body: WorkspaceSettingsIn) -> None:
    if body.timezone is not None and not is_iana_zone(body.timezone, _zones()):
        raise WorkspaceSettingsInvalid(
            "invalid_timezone", f"{body.timezone!r} is not an IANA time zone name"
        )
    low, high = SUBTASK_THRESHOLD_RANGE
    threshold = body.subtask_threshold_min
    if threshold is not None and not low <= threshold <= high:
        raise WorkspaceSettingsInvalid(
            "validation_error", f"subtask_threshold_min must be {low} to {high} minutes"
        )


async def get_workspace_settings(ctx: WorkspaceContext) -> WorkspaceSettingsOut:
    """The workspace's timezone, subtask threshold and version, through the settings cache
    (so a changed timezone reaches the next planner tick in every process, REL-6)."""
    key = settings_cache_key(ctx.workspace_id, WORKSPACE_SETTINGS_KEY)
    cached = await SETTINGS_CACHE.get(key)
    if cached is not None:
        return WorkspaceSettingsOut.model_validate_json(cached)
    token = SETTINGS_CACHE.token()
    columns = (WORKSPACES.c.timezone, WORKSPACES.c.subtask_threshold_min, WORKSPACES.c.version)
    async with tenant_session(ctx) as session:
        row = (
            (await session.execute(select(*columns).where(WORKSPACES.c.id == ctx.workspace_id)))
            .mappings()
            .one()
        )
    settings = _out(row)
    await SETTINGS_CACHE.fill(key, settings.model_dump_json().encode(), since=token)
    return settings


async def put_workspace_settings(
    ctx: WorkspaceContext, body: WorkspaceSettingsIn, *, now: datetime
) -> WorkspaceSettingsOut:
    """Partial update of the workspaces row at `body.version` (stale: StaleVersion with the
    current resource, 409). Validates the zone (`invalid_timezone`) and the threshold range
    (`validation_error`), both WorkspaceSettingsInvalid (422), and invalidates the settings
    cache on commit. Later keys that live in workspace_settings join this resource and bump
    the same row version. Audited in the same transaction (SEC-3): `settings.changed` with
    the changed fields, and `workspace.timezone_changed` with both zones when the zone
    moves."""
    _validate(body)
    changes = body.model_dump(exclude={"version"}, exclude_none=True)
    values: dict[str, Any] = {**changes, "version": WORKSPACES.c.version + 1, "updated_at": now}
    target = ("workspace", ctx.workspace_id)
    async with tenant_session(ctx) as session:
        before: str = (
            await session.execute(
                select(WORKSPACES.c.timezone)
                .where(WORKSPACES.c.id == ctx.workspace_id)
                .with_for_update()
            )
        ).scalar_one()
        try:
            row = await update_versioned(
                session, WORKSPACES, ctx.workspace_id, body.version, values
            )
        except StaleVersion as stale:
            raise StaleVersion(current=_out(stale.current).model_dump()) from None
        await audit.record(
            session,
            "settings.changed",
            target=target,
            details={"fields": sorted(changes)},
            occurred_at=now,
        )
        if row["timezone"] != before:
            await audit.record(
                session,
                "workspace.timezone_changed",
                target=target,
                details={"from": before, "to": row["timezone"]},
                occurred_at=now,
            )
        await invalidate_on_commit(
            session, settings_cache_key(ctx.workspace_id, WORKSPACE_SETTINGS_KEY)
        )
    return _out(row)


async def enroll_totp(
    user_id: UUID, workspace_id: UUID, secret: str, *, confirmed_at: datetime | None
) -> None:
    """Seal `secret` as the user's TOTP secret (P0-13). Spec skeleton."""
    raise NotImplementedError("P0-13")
