"""Task tokens and device tokens (P0-14, R-27, R-28, R-29, FR-14.10).

- A task token (`tmt_`) is bound to one run and one project; its scopes are a subset of
  the issuing key's (ScopeEscalation otherwise). It lives exactly as long as its run: valid
  from issue until `revoke_task_tokens_for_run` runs when the run ends, whatever the
  outcome. `expires_at = now + TASK_TOKEN_CEILING` only guards against a run whose end was
  never recorded (R-29's wall-clock backstop), never the normal end.
- A device token (`tmd_`) belongs to a runner; issuing again revokes the previous one.

Both are stored like API keys (prefix plus HMAC under the pepper), resolved by the bearer
resolver through `app.auth_resolve_token` and cached in `auth.api_key_by_prefix`;
revocation drops the cached entries in every process on commit.
"""

from datetime import datetime, timedelta
from typing import Final, cast
from uuid import UUID

from sqlalchemy import Table, select, update

from tumnis.core import crypto
from tumnis.core.ids import uuid7
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.modules.auth import keys
from tumnis.modules.auth.models import ApiKey, DeviceToken, TaskToken
from tumnis.modules.auth.scopes import unknown_scopes

TASK_TOKEN_CEILING: Final = timedelta(hours=24)  # R-29 wall-clock backstop, never the normal end
TASK_TOKENS = cast("Table", TaskToken.__table__)
DEVICE_TOKENS = cast("Table", DeviceToken.__table__)
API_KEYS = cast("Table", ApiKey.__table__)


class ScopeEscalation(PermissionError):  # noqa: N818  # the plan's name
    """A task token asked for a scope its issuing key does not hold (or the key is gone)."""


async def _key_scopes(ctx: WorkspaceContext, key_id: UUID) -> frozenset[str] | None:
    """The scopes of a live key of the workspace (None when missing or revoked)."""
    async with tenant_session(ctx) as s:
        row = (
            await s.execute(
                select(API_KEYS.c.scopes).where(
                    API_KEYS.c.id == key_id,
                    API_KEYS.c.revoked_at.is_(None),
                    API_KEYS.c.deleted_at.is_(None),
                )
            )
        ).first()
    return None if row is None else frozenset(row.scopes)


async def issue_task_token(
    ctx: WorkspaceContext,
    *,
    run_id: UUID,
    project_id: UUID,
    api_key_id: UUID,
    scopes: frozenset[str],
    now: datetime,
) -> keys.NewSecret:
    """A `tmt_` token for the run, limited to its project and to `scopes`, which must be
    a subset of the issuing key's."""
    held = await _key_scopes(ctx, api_key_id)
    if held is None or unknown_scopes(scopes) or not scopes <= held:
        extra = sorted(scopes - (held or frozenset()))
        raise ScopeEscalation(f"the issuing key does not hold: {', '.join(extra) or 'the key'}")
    new = keys.generate("tmt", crypto.peppers())
    async with tenant_session(ctx) as s:
        await s.execute(
            TASK_TOKENS.insert().values(
                id=uuid7(),
                run_id=run_id,
                project_id=project_id,
                api_key_id=api_key_id,
                scopes=sorted(scopes),
                prefix=new.prefix,
                token_hmac=new.secret_hmac,
                pepper_version=new.pepper_version,
                expires_at=now + TASK_TOKEN_CEILING,
            )
        )
        await keys.invalidate(s, "tmt", new.prefix)
    return new


async def revoke_task_tokens_for_run(ctx: WorkspaceContext, run_id: UUID, *, now: datetime) -> int:
    """Sets `revoked_at` on every live token of the run and drops their cache entries in
    every process on commit; returns how many it revoked."""
    async with tenant_session(ctx) as s:
        result = await s.execute(
            update(TASK_TOKENS)
            .where(
                TASK_TOKENS.c.run_id == run_id,
                TASK_TOKENS.c.revoked_at.is_(None),
                TASK_TOKENS.c.deleted_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(TASK_TOKENS.c.prefix)
        )
        prefixes = [row.prefix for row in result]
        await keys.invalidate(s, "tmt", *prefixes)
    return len(prefixes)


async def issue_device_token(
    ctx: WorkspaceContext, *, runner_id: UUID, now: datetime
) -> keys.NewSecret:
    """A `tmd_` token for the runner; the runner's previous token is revoked in the same
    transaction (and dropped from every process's cache on commit)."""
    new = keys.generate("tmd", crypto.peppers())
    async with tenant_session(ctx) as s:
        result = await s.execute(
            update(DEVICE_TOKENS)
            .where(
                DEVICE_TOKENS.c.runner_id == runner_id,
                DEVICE_TOKENS.c.revoked_at.is_(None),
                DEVICE_TOKENS.c.deleted_at.is_(None),
            )
            .values(revoked_at=now, rotated_at=now)
            .returning(DEVICE_TOKENS.c.prefix)
        )
        previous = [row.prefix for row in result]
        await s.execute(
            DEVICE_TOKENS.insert().values(
                id=uuid7(),
                runner_id=runner_id,
                prefix=new.prefix,
                token_hmac=new.secret_hmac,
                pepper_version=new.pepper_version,
            )
        )
        await keys.invalidate(s, "tmd", new.prefix, *previous)
    return new
