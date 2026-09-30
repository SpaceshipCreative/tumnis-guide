"""coolify DBOS workflows and steps (P2-14, FR-12.2).

- `coolify_poll_tick` (schedule `coolify-poll`, every 5 minutes, UTC; plan default; on the
  `sync` queue): one `poll_workspace` step per workspace.
- `poll_workspace` reads the workspace's Coolify settings (base URL, read-only token) and,
  for each application a live project links, the application and its ten newest
  deployments, then records the last deployment and the open-PR previews
  (`api.record_poll`). A failed read keeps the last good status and marks it out of date
  (`api.record_failure`: `unavailable` for timeouts, 5xx, 429 and an open breaker,
  `rejected` for other refusals and unexpected shapes). A workspace without settings is
  skipped, except with fakes (`TUMNIS_ADAPTERS=fake`), where the fake answers for every
  recorded application.

One adapter instance per (workspace, base URL, token) lives for the process, so a broken
Coolify opens its breaker for that workspace only and the next polls fail fast. Tests swap
the adapter factory with `use()`.
"""

import functools
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from dbos import DBOS

from tumnis.core import audit, db
from tumnis.core.adapters.errors import AdapterError, AdapterRejected
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.net import NetPolicy
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.coolify import api
from tumnis.modules.coolify.adapters.port import CoolifyStatus

POLL_SCHEDULE_NAME: Final = "coolify-poll"
POLL_SCHEDULE: Final = "*/5 * * * *"  # UTC; plan default: every 5 minutes
# A9's `sync` queue (registered by the worker), as the other connector ticks use: a
# schedule without a queue goes to DBOS's internal one, which `worker-extract` services too.
POLL_QUEUE: Final = "sync"

StatusFactory = Callable[[str, api.CoolifySettings | None], CoolifyStatus]

_factory: StatusFactory | None = None  # None: the adapter registry
_instances: dict[tuple[str, str, str], CoolifyStatus] = {}


def use(factory: StatusFactory | None) -> StatusFactory | None:
    """Swap the adapter factory (workspace id, settings) -> CoolifyStatus (tests); returns
    the previous one."""
    global _factory  # the workflows' one seam for tests
    previous, _factory = _factory, factory
    _instances.clear()
    return previous


@functools.cache
def _net_policy() -> NetPolicy:
    from tumnis.settings import Settings  # noqa: PLC0415  # the worker's own settings

    return Settings().net_policy()  # read from the environment


def _status(workspace_id: str, settings: api.CoolifySettings | None) -> CoolifyStatus:
    if _factory is not None:
        return _factory(workspace_id, settings)
    base_url, token = (settings.base_url, settings.token) if settings else ("", "")
    key = (workspace_id, base_url, token)
    if key not in _instances:
        mode = current_mode()
        deps: dict[str, Any] = {"base_url": base_url, "token": token}
        if mode == "real":
            deps["policy"] = _net_policy()
        _instances[key] = resolve("coolify.status", mode, **deps)
    return _instances[key]


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


def schedules() -> list[dict[str, Any]]:
    """This module's static schedules (A9), applied by the worker after DBOS.launch()."""
    return [
        {
            "schedule_name": POLL_SCHEDULE_NAME,
            "workflow_fn": coolify_poll_tick,
            "schedule": POLL_SCHEDULE,
            "queue_name": POLL_QUEUE,
        }
    ]


@DBOS.step()
async def list_workspaces() -> list[str]:
    async with db.app_sessionmaker()() as s, s.begin():
        return [str(workspace_id) for workspace_id in await audit.workspace_ids(s)]


@DBOS.step()
async def poll_workspace(workspace_id: str, now: datetime) -> int:
    """Polls every linked application of the workspace; returns how many were read."""
    ctx = _ctx(workspace_id)
    settings = await api.get_settings(ctx)
    if _factory is None and current_mode() == "real" and not (settings and settings.configured):
        return 0
    async with tenant_session(ctx) as s:
        apps = await api.linked_apps(s)
    if not apps:
        return 0
    coolify = _status(workspace_id, settings)
    read = 0
    for app_uuid in apps:
        try:
            app = await coolify.get_application(app_uuid)
            deployments = await coolify.list_deployments(app_uuid)
        except AdapterError as exc:
            error: api.PollError = "rejected" if isinstance(exc, AdapterRejected) else "unavailable"
            async with tenant_session(ctx) as s:
                await api.record_failure(s, app_uuid, error)
            continue
        async with tenant_session(ctx) as s:
            await api.record_poll(s, app, deployments, now=now)
        read += 1
    return read


@DBOS.workflow()
async def coolify_poll_tick(scheduled_time: datetime, context: Any) -> None:
    """Scheduled every 5 minutes; one step per workspace, at the scheduled time."""
    for workspace_id in await list_workspaces():
        await poll_workspace(workspace_id, scheduled_time)
