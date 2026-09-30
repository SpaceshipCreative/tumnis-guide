"""Archiving a project's agent side (P2-18, FR-5.10): the profile's home on the agent
server and the project's run logs. The steps `projects.workflows` runs through its archive
hooks (registered at the bottom; agents.workflows imports this module in the worker).

Names: an archive id is minted once per archiving workflow (uuid5 of its workflow id), so
every replayed step writes the same `archive`, `restore` or `purge_archive` mailbox row
(message id uuid5 of the archive id and the type), and the daemon's answer (`archive_done`,
`restore_done`) reaches the workflow on `archive:<archive id>` or `restore:<archive id>`
(the socket hands it over, R-30).

Blobs (`tumnis.core.archive_blobs`, module `agents`): `profile_archive` holds the facts of
the profile's archive on the agent server (profile, runner, path, size, sha256, manifest
digest); `run_events` holds the logs of up to 500 runs each, moved out of `run_events` in
the same transaction as the blob is written and put back, ids and times included, on
unarchive.
"""

import hmac
import json
from typing import Any, Final
from uuid import UUID, uuid5

import structlog
from sqlalchemy import Table, delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import archive_blobs as blobs
from tumnis.core.clock import SystemClock
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.agents import api
from tumnis.modules.agents.models import AgentProfile, RunEventRow, Runner, RunnerMessage, RunRow
from tumnis.modules.agents.protocol import PROTOCOL_2, Archive, PurgeArchive, Restore
from tumnis.modules.projects import api as projects

_log = structlog.get_logger(__name__)

_ARCHIVE_NS: Final = UUID("5b0f3c1e-3f5e-4d0a-9d8e-6a1c2b7e9f18")
MODULE: Final = "agents"
PROFILE_ARCHIVE: Final = "profile_archive"  # blob kind: the facts of a profile's archive
RUN_EVENTS: Final = "run_events"  # blob kind: one batch of runs' logs
RUN_BATCH: Final = 500  # runs per run-log blob (plan default)

_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_runners: Table = Runner.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]
_events: Table = RunEventRow.__table__  # type: ignore[assignment]
_messages: Table = RunnerMessage.__table__  # type: ignore[assignment]


def archive_id_of(workflow_id: str) -> str:
    """The archive id of an archiving workflow (deterministic)."""
    return str(uuid5(_ARCHIVE_NS, workflow_id))


def archive_message_id(archive_id: str) -> UUID:
    return uuid5(_ARCHIVE_NS, f"archive:{archive_id}")


def restore_message_id(archive_id: str) -> UUID:
    return uuid5(_ARCHIVE_NS, f"restore:{archive_id}")


def purge_message_id(archive_id: str) -> UUID:
    return uuid5(_ARCHIVE_NS, f"purge_archive:{archive_id}")


def archive_topic(archive_id: str) -> str:
    """The DBOS topic `archive_project` receives the runner's `archive_done` on."""
    return f"archive:{archive_id}"


def restore_topic(archive_id: str) -> str:
    """The DBOS topic `unarchive_project` receives the runner's `restore_done` on."""
    return f"restore:{archive_id}"


def _ctx(workspace_id: UUID) -> WorkspaceContext:
    return WorkspaceContext(workspace_id, SYSTEM_ACTOR)


async def _queue(
    s: AsyncSession, runner_id: UUID, message: Archive | Restore | PurgeArchive
) -> None:
    """The mailbox row (once per message id) and the NOTIFY that wakes the runner's socket."""
    await s.execute(
        insert(_messages)
        .values(
            runner_id=runner_id,
            message_id=message.message_id,
            direction="out",
            type=message.type,
            payload=message.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "message_id"])
    )
    await api.notify_runner(s, runner_id)


# --- the profile's home on the agent server --------------------------------------------------


async def send_archive(workspace_id: UUID, project_id: UUID, workflow_id: str) -> str | None:
    """Queue `archive` for the project's profile; its archive id. None when the project has
    no profile on a runner that speaks protocol 2 (nothing to archive there), or its
    profile is archived already (archived again before an unarchive restored it)."""
    async with tenant_session(_ctx(workspace_id)) as s:
        if await _archived_profile(s, project_id) is not None:
            return None
        found = (
            await s.execute(
                select(_profiles.c.name, _profiles.c.runner_id, _runners.c.protocol_version)
                .join(_runners, _runners.c.id == _profiles.c.runner_id)
                .where(
                    _profiles.c.project_id == project_id,
                    _profiles.c.role == "project",
                    _profiles.c.transport == "daemon",
                    _profiles.c.deleted_at.is_(None),
                    _runners.c.deleted_at.is_(None),
                )
                .order_by(_profiles.c.created_at)
                .limit(1)
            )
        ).first()
        if found is None or (found.protocol_version or 0) < PROTOCOL_2:
            return None
        archive_id = archive_id_of(workflow_id)
        message = Archive(
            message_id=archive_message_id(archive_id),
            correlation_id=workflow_id,
            sent_at=SystemClock().now(),
            profile=found.name,
            archive_id=archive_id,
        )
        await _queue(s, found.runner_id, message)
    return archive_id


async def store_archive(workspace_id: UUID, project_id: UUID, done: dict[str, Any]) -> bool:
    """Keep the facts of the profile's archive (where it lives, its sha256 and manifest
    digest) as a blob, with the profile and runner it came from; False when the runner
    refused (e.g. `active_run`), which leaves the profile live."""
    if done.get("error_code") is not None:
        return False
    archive_id = str(done["archive_id"])
    async with tenant_session(_ctx(workspace_id)) as s:
        row = (
            await s.execute(
                select(_messages.c.runner_id, _messages.c.payload).where(
                    _messages.c.message_id == archive_message_id(archive_id),
                    _messages.c.direction == "out",
                )
            )
        ).first()
        if row is None:
            return False
        facts = {
            "archive_id": archive_id,
            "profile": row.payload["profile"],
            "runner_id": str(row.runner_id),
            "path": done["path"],
            "size": done["size"],
            "sha256": done["sha256"],
            "manifest_digest": done["manifest_digest"],
        }
        await blobs.put_blob(
            s,
            module=MODULE,
            kind=PROFILE_ARCHIVE,
            project_id=project_id,
            ref=archive_id,
            raw=json.dumps(facts, sort_keys=True).encode(),
        )
    return True


async def _archived_profile(s: AsyncSession, project_id: UUID) -> dict[str, Any] | None:
    found = await blobs.blobs(s, module=MODULE, kind=PROFILE_ARCHIVE, project_id=project_id)
    if not found:
        return None
    facts: dict[str, Any] = json.loads(found[0][1])
    return facts


async def send_restore(workspace_id: UUID, project_id: UUID, workflow_id: str) -> str | None:
    """Queue `restore` for the project's archived profile, with the manifest digest the
    archive reported; its archive id, or None when no profile was archived."""
    async with tenant_session(_ctx(workspace_id)) as s:
        facts = await _archived_profile(s, project_id)
        if facts is None:
            return None
        archive_id = str(facts["archive_id"])
        message = Restore(
            message_id=restore_message_id(archive_id),
            correlation_id=workflow_id,
            sent_at=SystemClock().now(),
            profile=facts["profile"],
            archive_id=archive_id,
            expected_manifest_digest=facts["manifest_digest"],
        )
        await _queue(s, UUID(facts["runner_id"]), message)
    return archive_id


async def finish_restore(workspace_id: UUID, project_id: UUID, done: dict[str, Any]) -> bool:
    """True when the runner restored the profile with the archived manifest digest; its
    archive blob is then dropped. False keeps it for another try."""
    async with tenant_session(_ctx(workspace_id)) as s:
        facts = await _archived_profile(s, project_id)
        if facts is None:  # a replayed step: dropped already
            return bool(done.get("ok"))
        reported = str(done.get("manifest_digest") or "")
        if not done.get("ok") or not hmac.compare_digest(
            reported.encode(), str(facts["manifest_digest"]).encode()
        ):
            _log.warning("profile_restore_mismatch", project_id=str(project_id), reply=done)
            return False
        await blobs.delete_blobs(s, module=MODULE, kind=PROFILE_ARCHIVE, project_id=project_id)
    return True


# --- run logs --------------------------------------------------------------------------------


async def archive_run_logs(workspace_id: UUID, project_id: UUID) -> bool:
    """The `run_events` of up to 500 of the project's runs into one blob (ref: the batch's
    first run id), deleted in the same transaction; True while more runs have logs."""
    async with tenant_session(_ctx(workspace_id)) as s:
        with_logs = (
            select(_runs.c.id)
            .join(_profiles, _profiles.c.id == _runs.c.profile_id)
            .where(
                _profiles.c.project_id == project_id,
                exists().where(_events.c.run_id == _runs.c.id),
            )
            .order_by(_runs.c.id)
        )
        batch: list[UUID] = list(await s.scalars(with_logs.limit(RUN_BATCH + 1)))
        if not batch:
            return False
        runs, more = batch[:RUN_BATCH], len(batch) > RUN_BATCH
        rows = await blobs.snapshot_rows(
            s,
            "run_events",
            "t.run_id = ANY(CAST(:runs AS uuid[]))",
            {"runs": list(runs)},
        )
        await blobs.put_blob(
            s,
            module=MODULE,
            kind=RUN_EVENTS,
            project_id=project_id,
            ref=str(runs[0]),
            raw=blobs.encode_rows(rows),
        )
        await s.execute(delete(_events).where(_events.c.run_id.in_(runs)))
    return more


async def restore_run_logs(workspace_id: UUID, project_id: UUID) -> bool:
    """One run-log blob back into `run_events` (then dropped); True while more are left."""
    async with tenant_session(_ctx(workspace_id)) as s:
        found = await blobs.blobs(s, module=MODULE, kind=RUN_EVENTS, project_id=project_id)
        if not found:
            return False
        ref, raw = found[0]
        await blobs.restore_rows(s, "run_events", blobs.decode_rows(raw))
        await blobs.delete_blobs(s, module=MODULE, kind=RUN_EVENTS, project_id=project_id, ref=ref)
    return len(found) > 1


# --- purge and dispatch ---------------------------------------------------------------------


async def purge_archive(workspace_id: UUID, project_id: UUID) -> None:
    """A purged project: the runner drops its profile archive (`purge_archive`), and the
    agents blobs go."""
    async with tenant_session(_ctx(workspace_id)) as s:
        facts = await _archived_profile(s, project_id)
        if facts is not None:
            archive_id = str(facts["archive_id"])
            message = PurgeArchive(
                message_id=purge_message_id(archive_id),
                correlation_id=f"purge:{project_id}",
                sent_at=SystemClock().now(),
                profile=facts["profile"],
                archive_id=archive_id,
            )
            await _queue(s, UUID(facts["runner_id"]), message)
        await blobs.delete_blobs(s, module=MODULE, project_id=project_id)


async def withdraw_command(workspace_id: UUID, command: str, archive_id: str) -> bool:
    """Withdraw the `archive` or `restore` command of `archive_id` when its runner never
    received it (still `queued`: the agent server has been offline all along), so the
    workflow waiting for its answer can let the one `archive` queue slot go; True when
    withdrawn. A command the runner has received stays: its answer is on the way."""
    message_id = (
        archive_message_id(archive_id) if command == "archive" else restore_message_id(archive_id)
    )
    async with tenant_session(_ctx(workspace_id)) as s:
        withdrawn = await s.scalar(
            update(_messages)
            .where(
                _messages.c.message_id == message_id,
                _messages.c.direction == "out",
                _messages.c.status == "queued",
                _messages.c.deleted_at.is_(None),
            )
            .values(deleted_at=SystemClock().now())
            .returning(_messages.c.id)
        )
    return withdrawn is not None


async def dispatch_allowed(s: AsyncSession, profile_id: UUID) -> bool:
    """Whether runs may be dispatched to the profile: never while its project is archived
    or its archive or unarchive is under way (P2-18). A master profile has no project."""
    project_id = await s.scalar(select(_profiles.c.project_id).where(_profiles.c.id == profile_id))
    return project_id is None or not await projects.dormant_projects(s, [project_id])


_HOOKS: dict[str, projects.ArchiveHook] = {
    "agents.send_archive": send_archive,
    "agents.store_archive": store_archive,
    "agents.archive_run_logs": archive_run_logs,
    "agents.restore_run_logs": restore_run_logs,
    "agents.send_restore": send_restore,
    "agents.finish_restore": finish_restore,
    "agents.purge_archive": purge_archive,
    "agents.withdraw_command": withdraw_command,
}
for _name, _hook in _HOOKS.items():
    projects.register_archive_hook(_name, _hook)
