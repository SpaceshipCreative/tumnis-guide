"""projects DBOS workflows and steps: archive, unarchive and purge (P2-18, FR-5.10).

`archive_project` moves an archived project's data out of the live tables, one step at a
time, each step a hook another module registered in `api` (projects cannot import them):

    begin      the project is still `archiving` (a no-op workflow otherwise)
    profile    `agents.send_archive` queues the daemon's `archive` (None: no profile on a
               runner that can archive); the `archive_done` arrives on `archive:<id>` in
               the workflow body (R-30), then `agents.store_archive` keeps its facts
    run logs   `agents.archive_run_logs`, one step per batch of 500 runs (plan default)
    excerpts   `integrations.archive_excerpts`
    folder     `knowledge.archive_folder`: a Tumnis-made folder packed into one file, an
               existing one's index only
    finish     `archiving` -> `archived` (`project.updated`)

`unarchive_project` runs the reverse, the profile last: its `restore_done` must report
the manifest digest the archive did before the project turns live (`archive_state` null).
`purge_project_archive` drops what an archive kept once the project is purged.

Every step is idempotent (a replayed step finds its work done) and does nothing once the
project has left the state its workflow expects (unarchived, archived again, purged). All
three run on the `archive` queue, one at a time (`concurrency=1`): a second workflow for
the same project, e.g. one started by the event beside one started by hand, waits for the
first and then finds nothing left to do. (The plan runs packing on `maintenance`; a queue
of its own keeps a long pack from holding up backups and audit checks.)
"""

import asyncio
import contextvars
from typing import Any, Final
from uuid import UUID

import structlog
from dbos import DBOS, SetWorkflowID

from tumnis.core import faults
from tumnis.core.events import EventEnvelope
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR, ActorRef
from tumnis.modules.projects import api

_log = structlog.get_logger(__name__)

ARCHIVE_QUEUE: Final = "archive"
ARCHIVE_CONCURRENCY: Final = 1
WAIT_SLICE_S: Final = 3600  # one recv at a time; an offline agent server is waited for
ARCHIVE_TOPIC_PREFIX: Final = "archive:"
RESTORE_TOPIC_PREFIX: Final = "restore:"

KILL_BEGIN: Final = "projects.archive.begin"
KILL_PROFILE_SENT: Final = "projects.archive.profile_sent"
KILL_PROFILE_STORED: Final = "projects.archive.profile_stored"
KILL_RUN_LOGS: Final = "projects.archive.run_logs"
KILL_EXCERPTS: Final = "projects.archive.excerpts"
KILL_FOLDER: Final = "projects.archive.folder"
KILL_FINISH: Final = "projects.archive.finish"

PURGE_HOOKS: Final = (
    "agents.purge_archive",
    "knowledge.purge_archive",
    "integrations.purge_archive",
)


def _ctx(workspace_id: str, actor: str | None = None) -> WorkspaceContext:
    return WorkspaceContext(UUID(workspace_id), ActorRef(actor) if actor else SYSTEM_ACTOR)


async def _in_state(workspace_id: str, project_id: str, state: api.ArchiveState) -> bool:
    async with tenant_session(_ctx(workspace_id)) as s:
        facts = await api.archive_facts(s, UUID(project_id))
    return facts is not None and not facts.deleted and facts.archive_state == state


async def _hook(name: str, *args: Any) -> Any:
    hook = api.archive_hook(name)
    return None if hook is None else await hook(*args)


async def _step(
    workspace_id: str, project_id: str, state: api.ArchiveState, hook: str, *args: Any
) -> Any:
    """The hook, while the project is still in `state`; None otherwise."""
    if not await _in_state(workspace_id, project_id, state):
        return None
    return await _hook(hook, UUID(workspace_id), UUID(project_id), *args)


# --- archive_project -----------------------------------------------------------------------


@DBOS.step()
async def begin_archive_step(workspace_id: str, project_id: str) -> bool:
    going = await _in_state(workspace_id, project_id, "archiving")
    faults.killpoint(KILL_BEGIN)
    return going


@DBOS.step()
async def send_archive_step(workspace_id: str, project_id: str, workflow_id: str) -> str | None:
    """The `archive` mailbox row for the project's profile; its archive id, or None."""
    archive_id = await _step(
        workspace_id, project_id, "archiving", "agents.send_archive", workflow_id
    )
    faults.killpoint(KILL_PROFILE_SENT)
    return None if archive_id is None else str(archive_id)


@DBOS.step()
async def store_archive_step(workspace_id: str, project_id: str, done: dict[str, Any]) -> bool:
    kept = await _step(workspace_id, project_id, "archiving", "agents.store_archive", done)
    faults.killpoint(KILL_PROFILE_STORED)
    return bool(kept)


@DBOS.step()
async def run_logs_step(workspace_id: str, project_id: str) -> bool:
    """One batch of run logs into a blob; True while more are left."""
    more = await _step(workspace_id, project_id, "archiving", "agents.archive_run_logs")
    faults.killpoint(KILL_RUN_LOGS)
    return bool(more)


@DBOS.step()
async def excerpts_step(workspace_id: str, project_id: str) -> None:
    await _step(workspace_id, project_id, "archiving", "integrations.archive_excerpts")
    faults.killpoint(KILL_EXCERPTS)


@DBOS.step()
async def folder_step(workspace_id: str, project_id: str) -> None:
    await _step(workspace_id, project_id, "archiving", "knowledge.archive_folder")
    faults.killpoint(KILL_FOLDER)


@DBOS.step()
async def finish_archive_step(workspace_id: str, project_id: str, actor: str) -> bool:
    async with tenant_session(_ctx(workspace_id, actor)) as s:
        done = await api.move_archive_state(s, UUID(project_id), expect="archiving", to="archived")
    faults.killpoint(KILL_FINISH)
    return done


@DBOS.workflow(name="archive_project")
async def archive_project(workspace_id: str, project_id: str, actor: str) -> str:
    """Archive the project's data (see the module docstring); `archived`, or `skipped`
    when the project was no longer being archived."""
    if not await begin_archive_step(workspace_id, project_id):
        return "skipped"
    workflow_id = DBOS.workflow_id
    assert workflow_id is not None  # noqa: S101  # inside a workflow
    archive_id = await send_archive_step(workspace_id, project_id, workflow_id)
    if archive_id is not None:
        done = None
        while done is None:  # recv in the workflow body, never in a step (R-30)
            done = await DBOS.recv_async(
                f"{ARCHIVE_TOPIC_PREFIX}{archive_id}", timeout_seconds=WAIT_SLICE_S
            )
        if not await store_archive_step(workspace_id, project_id, done):
            _log.warning("profile_not_archived", project_id=project_id, reply=done)
    while await run_logs_step(workspace_id, project_id):
        pass
    await excerpts_step(workspace_id, project_id)
    await folder_step(workspace_id, project_id)
    finished = await finish_archive_step(workspace_id, project_id, actor)
    return "archived" if finished else "skipped"


# --- unarchive_project -----------------------------------------------------------------------


@DBOS.step()
async def begin_unarchive_step(workspace_id: str, project_id: str) -> bool:
    return await _in_state(workspace_id, project_id, "unarchiving")


@DBOS.step()
async def restore_folder_step(workspace_id: str, project_id: str) -> None:
    await _step(workspace_id, project_id, "unarchiving", "knowledge.unarchive_folder")


@DBOS.step()
async def restore_excerpts_step(workspace_id: str, project_id: str) -> None:
    await _step(workspace_id, project_id, "unarchiving", "integrations.restore_excerpts")


@DBOS.step()
async def restore_run_logs_step(workspace_id: str, project_id: str) -> bool:
    """One blob of run logs back into `run_events`; True while more are left."""
    return bool(await _step(workspace_id, project_id, "unarchiving", "agents.restore_run_logs"))


@DBOS.step()
async def send_restore_step(workspace_id: str, project_id: str, workflow_id: str) -> str | None:
    """The `restore` mailbox row for the project's archived profile; its archive id, or
    None when no profile was archived."""
    archive_id = await _step(
        workspace_id, project_id, "unarchiving", "agents.send_restore", workflow_id
    )
    return None if archive_id is None else str(archive_id)


@DBOS.step()
async def finish_restore_step(workspace_id: str, project_id: str, done: dict[str, Any]) -> bool:
    return bool(await _step(workspace_id, project_id, "unarchiving", "agents.finish_restore", done))


@DBOS.step()
async def finish_unarchive_step(workspace_id: str, project_id: str, actor: str) -> bool:
    async with tenant_session(_ctx(workspace_id, actor)) as s:
        return await api.move_archive_state(s, UUID(project_id), expect="unarchiving", to=None)


class ProfileNotRestored(RuntimeError):  # noqa: N818  # the archive's word
    """The agent server's restore did not report the archived manifest digest."""


@DBOS.workflow(name="unarchive_project")
async def unarchive_project(workspace_id: str, project_id: str, actor: str) -> str:
    """Bring the project's data back (see the module docstring); `unarchived`, or
    `skipped`. A profile whose restore does not match its manifest ends the workflow in
    error with the project still `unarchiving`: nothing is switched live unverified."""
    if not await begin_unarchive_step(workspace_id, project_id):
        return "skipped"
    workflow_id = DBOS.workflow_id
    assert workflow_id is not None  # noqa: S101  # inside a workflow
    await restore_folder_step(workspace_id, project_id)
    await restore_excerpts_step(workspace_id, project_id)
    while await restore_run_logs_step(workspace_id, project_id):
        pass
    archive_id = await send_restore_step(workspace_id, project_id, workflow_id)
    if archive_id is not None:
        done = None
        while done is None:  # R-30
            done = await DBOS.recv_async(
                f"{RESTORE_TOPIC_PREFIX}{archive_id}", timeout_seconds=WAIT_SLICE_S
            )
        if not await finish_restore_step(workspace_id, project_id, done):
            raise ProfileNotRestored(f"profile archive {archive_id} did not restore")
    finished = await finish_unarchive_step(workspace_id, project_id, actor)
    return "unarchived" if finished else "skipped"


# --- purge_project_archive -----------------------------------------------------------------


@DBOS.step()
async def purge_step(workspace_id: str, project_id: str, hook: str) -> None:
    await _hook(hook, UUID(workspace_id), UUID(project_id))


@DBOS.workflow(name="purge_project_archive")
async def purge_project_archive(workspace_id: str, project_id: str) -> None:
    """Drop what the archive of a purged project kept, module by module."""
    for hook in PURGE_HOOKS:
        await purge_step(workspace_id, project_id, hook)


# --- starting them (the subscribers in events.py) ----------------------------------------


async def _enqueue(workflow_id: str, workflow: Any, *args: str) -> None:
    """Enqueue once on the archive queue: DBOS returns the existing workflow for an id in
    use. Subscribers run inside a DBOS step, which may not start a workflow, so the enqueue
    runs in a fresh context (as agents' `start_provision` does)."""

    async def enqueue() -> None:
        with SetWorkflowID(workflow_id):
            await DBOS.enqueue_workflow_async(ARCHIVE_QUEUE, workflow, *args)

    await asyncio.get_running_loop().create_task(enqueue(), context=contextvars.Context())


def _project(envelope: EventEnvelope) -> str:
    return str(envelope.payload["project_id"])


async def start_archive(envelope: EventEnvelope) -> None:
    await _enqueue(
        f"archive_project:{envelope.event_id}",
        archive_project,
        str(envelope.workspace_id),
        _project(envelope),
        envelope.actor,
    )


async def start_unarchive(envelope: EventEnvelope) -> None:
    await _enqueue(
        f"unarchive_project:{envelope.event_id}",
        unarchive_project,
        str(envelope.workspace_id),
        _project(envelope),
        envelope.actor,
    )


async def start_purge(envelope: EventEnvelope) -> None:
    await _enqueue(
        f"purge_project_archive:{envelope.event_id}",
        purge_project_archive,
        str(envelope.workspace_id),
        _project(envelope),
    )
