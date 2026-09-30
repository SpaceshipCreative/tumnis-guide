"""github DBOS workflows and steps (P2-13, FR-12.1).

- `github_refresh_artifact(workspace_id, artifact_id)` on the `github` queue: one read of a
  pull request (`api.refresh_artifact`): the pull request, the statuses and check runs of
  its head commit and its reviews, conditional on the ETags of the last read; the status,
  raw answers and a `github.fetched` event are stored, `artifact.updated` goes out when the
  state or checks changed, and the browsers of the tasks that link it are told over `/ws`.
  Skipped while the module is off for the workspace or, with the real adapter, no token is
  set. A GitHub outage fails the step; DBOS retries it, and the poll retries later.
- `github_poll_tick`: every 5 minutes (plan default, Polling is the tested path: a
  private-network Tumnis cannot receive webhooks), a refresh for each open pull request
  artifact of every workspace with the module on. Merged and closed ones are left alone.
  Deduplication id `refresh:<artifact_id>`: one queued or running refresh per artifact.

The queue is the module's own, with a limiter (`worker.register_queues`): DBOS limiters are
per queue, so sharing `sync` would let GitHub's budget throttle the calendar. The client and
the clock come from `use()` (tests) or the adapter registry and the system clock.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from dbos import DBOS, SetEnqueueOptions

from tumnis.core import audit, db, modules
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.github import api
from tumnis.modules.github.adapters.port import GitHubStatus

QUEUE: Final = api.QUEUE
REFRESH_WORKFLOW: Final = api.REFRESH_WORKFLOW
POLL_SCHEDULE_NAME: Final = "github-poll"
POLL_SCHEDULE: Final = "*/5 * * * *"  # plan default
STEP_RETRY: Final[dict[str, Any]] = {
    "retries_allowed": True,
    "max_attempts": 3,
    "interval_seconds": 2.0,
    "backoff_rate": 2.0,
}

type StatusFactory = Callable[[str, api.GitHubSettings], GitHubStatus | None]

_factory: StatusFactory | None = None  # None: built from the adapter registry
_clock: Clock | None = None  # None: the system clock
_built: dict[tuple[str, str], GitHubStatus] = {}  # one client (and breaker) per workspace token


def use(
    factory: StatusFactory | None = None, clock: Clock | None = None
) -> tuple[StatusFactory | None, Clock | None]:
    """Swap the client factory (workspace id, settings) and the clock the workflows use
    (tests); returns the previous pair."""
    global _factory, _clock  # the workflows' one seam for tests
    previous = (_factory, _clock)
    _factory, _clock = factory, clock
    return previous


def _now_clock() -> Clock:
    return _clock or SystemClock()


def _ctx(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), SYSTEM_ACTOR)


def _status_for(workspace_id: str, settings: api.GitHubSettings) -> GitHubStatus | None:
    """The client for the workspace: the injected one, else the registry's (a fake in fake
    mode; the real one only with a token). One instance per workspace and token, so one
    workspace's failing token does not open another's circuit breaker."""
    if _factory is not None:
        return _factory(workspace_id, settings)
    mode = current_mode()
    if mode == "fake":
        return _build(workspace_id, "", lambda: resolve("github.status", "fake"))
    if not settings.token:
        return None
    fingerprint = hashlib.sha256(settings.token.encode()).hexdigest()
    return _build(
        workspace_id,
        fingerprint,
        lambda: resolve("github.status", "real", token=settings.token, clock=_now_clock()),
    )


def _build(workspace_id: str, fingerprint: str, make: Callable[[], GitHubStatus]) -> GitHubStatus:
    key = (workspace_id, fingerprint)
    if key not in _built:
        _built[key] = make()
    return _built[key]


# --- Refresh ------------------------------------------------------------------------------


async def refresh(workspace_id: str, artifact_id: str) -> str:
    """One read of the pull request artifact (see `api.refresh_artifact`); "skipped" while
    the module is off for the workspace. Called by the step below and directly by tests."""
    ctx = _ctx(workspace_id)
    if not await modules.enabled("github", ctx.workspace_id):
        return "skipped"
    return await api.refresh_artifact(
        ctx,
        UUID(artifact_id),
        lambda settings: _status_for(workspace_id, settings),
        now=_now_clock().now(),
    )


@DBOS.step(**STEP_RETRY)
async def refresh_read(workspace_id: str, artifact_id: str) -> str:
    return await refresh(workspace_id, artifact_id)


@DBOS.workflow(name=REFRESH_WORKFLOW)
async def refresh_artifact(workspace_id: str, artifact_id: str) -> str:
    return await refresh_read(workspace_id, artifact_id)


# --- Scheduled poll -----------------------------------------------------------------------


@DBOS.step()
async def open_pull_requests() -> list[tuple[str, str]]:
    """(workspace, artifact) for every open pull request of every workspace with the module
    on, in a repository its allow-list names."""
    async with db.app_sessionmaker()() as s, s.begin():
        workspaces = await audit.workspace_ids(s)
    found: list[tuple[str, str]] = []
    for workspace in workspaces:
        if not await modules.enabled("github", workspace):
            continue
        ctx = WorkspaceContext(workspace, SYSTEM_ACTOR)
        found += [(str(workspace), str(artifact)) for artifact in await api.pollable(ctx)]
    return found


def schedules() -> list[Any]:
    """This module's DBOS schedules, applied by the worker after launch."""
    return [
        {
            "schedule_name": POLL_SCHEDULE_NAME,
            "workflow_fn": github_poll_tick,
            "schedule": POLL_SCHEDULE,
            "queue_name": QUEUE,
        }
    ]


@DBOS.workflow(name="github_poll_tick")
async def github_poll_tick(scheduled_at: datetime, context: Any) -> None:
    """Scheduled `*/5 * * * *`: a refresh for each open pull request, one at a time per
    artifact (a refresh still queued or running from an earlier tick or an open is reused)."""
    for workspace_id, artifact_id in await open_pull_requests():
        with SetEnqueueOptions(
            deduplication_id=f"refresh:{artifact_id}", duplication_policy="return-existing"
        ):
            await DBOS.enqueue_workflow_async(QUEUE, refresh_artifact, workspace_id, artifact_id)
