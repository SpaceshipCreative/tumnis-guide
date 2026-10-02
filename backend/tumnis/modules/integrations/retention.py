"""Retention and purge (P3-09, SAAS-2, FR-5.10, Data flow rule 3): what a purge removes,
and the batches that remove it. Module-private: `api.py` re-exports the public names and
`workflows.py` runs the batches.

A purge removes ingested content from Postgres, never at the provider: the canonical
messages, notes and threads it covers, their raw payloads, and nothing else. The context
items that pointed at them stay, marked `target_purged_at`, so a task keeps its link and
shows "Removed by retention". What a purge covers:

- `connection`: every message, note and thread of the connection.
- `project`: what the project held when the purge was asked for (snapshotted into the
  purge's `targets`, since the project's archive goes meanwhile): the records its own
  context items (in its archive, or still live) and its tasks' point at, less any record
  something outside the project also links.
- `retention`: records older than the cutoff (`rules.purge_candidates`), except those
  an open task links and those an archived project holds (its own archived context items,
  or its tasks'): archive compresses, it never purges.

Batches of `limit` records of one type, in the order messages, notes, threads (a thread
goes once no message points at it). One batch is one transaction: rows, raw payloads,
context items, the purge's counts and its batch number, and one `items.purged` per type,
commit together, so a batch killed before its commit is redone whole and nothing is
counted twice. Kill point `integrations.purge.batch_<n>.committing` fires inside it.

A raw payload goes with the purged rows that point at it; a kept integrations row that
points at it too (a person made from a purged message's raw item, a thread whose last
message went) keeps its row and loses only the pointer. Raw payloads of record types
another module owns (calendar events, knowledge documents) are never touched here.
"""

from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Table,
    and_,
    delete,
    exists,
    false,
    func,
    literal_column,
    or_,
    select,
    true,
    union_all,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import archive_blobs as blobs
from tumnis.core import faults
from tumnis.core.outbox import emit
from tumnis.modules.integrations import rules
from tumnis.modules.integrations.models import (
    Artifact,
    ContextItem,
    Message,
    Note,
    Person,
    Purge,
    RawPayload,
    Thread,
)
from tumnis.modules.integrations.payloads import ItemsPurgedV1
from tumnis.modules.projects import api as projects

PURGED_TEXT: Final = "Removed by retention"  # what a purged item shows (packet, drawer)
ORDER: Final = ("message", "note", "thread")
COUNT_KEYS: Final = {"message": "messages", "note": "notes", "thread": "threads"}
ZERO_COUNTS: Final = {
    "messages": 0,
    "threads": 0,
    "notes": 0,
    "raw_payloads": 0,
    "context_items": 0,
}
ARCHIVE_MODULE: Final = "integrations"  # archive.MODULE (archive imports projects' hooks)
ARCHIVE_KIND: Final = "context_items"  # archive.CONTEXT_ITEMS

_messages: Table = Message.__table__  # type: ignore[assignment]
_notes: Table = Note.__table__  # type: ignore[assignment]
_threads: Table = Thread.__table__  # type: ignore[assignment]
_raw: Table = RawPayload.__table__  # type: ignore[assignment]
_context: Table = ContextItem.__table__  # type: ignore[assignment]
_purges: Table = Purge.__table__  # type: ignore[assignment]
_people: Table = Person.__table__  # type: ignore[assignment]
_artifacts: Table = Artifact.__table__  # type: ignore[assignment]
_TABLES: Final[dict[str, Table]] = {"message": _messages, "note": _notes, "thread": _threads}
# Every integrations table whose rows may point at a raw payload.
_POINTERS: Final[tuple[Table, ...]] = (
    _messages,
    _notes,
    _threads,
    _people,
    _artifacts,
)
_OWN_RAW_TYPES: Final = sorted(
    t for t, owner in rules.RECORD_OWNERS.items() if owner == "integrations"
)


def _at(record_type: str) -> ColumnElement[Any]:
    """The time retention judges a record by: a message's send time, a note's meeting
    (its end, else its start), a thread's last message; else when it was fetched."""
    t = _TABLES[record_type]
    match record_type:
        case "message":
            return func.coalesce(t.c.sent_at, t.c.fetched_at)
        case "note":
            return func.coalesce(t.c.end_at, t.c.start_at, t.c.fetched_at)
    return func.coalesce(t.c.last_message_at, t.c.fetched_at)


# --- what other modules hold (registered by them; integrations imports none of them) ---------


@dataclass(frozen=True)
class LinkRef:
    """A live context item pointing at a message, note or thread: its id and its owner."""

    id: UUID
    owner_type: str
    owner_id: UUID


@dataclass(frozen=True)
class Holds:
    """Which of the context items asked about keep their records past the cutoff: an open
    task links them, or an archived project holds them."""

    open_task: frozenset[UUID] = frozenset()
    archived: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class ProjectHolders:
    """What links content to a project through another module: owners of context items
    by owner type (tasks: the project's task ids), and items its own tables link."""

    owners: Mapping[str, frozenset[UUID]] = field(default_factory=dict)
    item_ids: frozenset[UUID] = frozenset()


HoldLookup = Callable[[AsyncSession, Sequence[LinkRef]], Awaitable[Holds]]
HoldersLookup = Callable[[AsyncSession, UUID], Awaitable[ProjectHolders]]
_holds: dict[str, HoldLookup] = {}
_holders: dict[str, HoldersLookup] = {}


def register_retention_hold(name: str, lookup: HoldLookup) -> None:
    """A module that owns context items' owners (tasks) tells retention which items to
    keep (an open task's, an archived project's task's)."""
    _holds[name] = lookup


def register_project_holders(name: str, lookup: HoldersLookup) -> None:
    """A module that links content to projects (tasks) tells a project purge what the
    project holds through it."""
    _holders[name] = lookup


# --- what a purge covers ----------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    kind: str  # retention | project | connection
    target_id: UUID | None = None
    cutoff: datetime | None = None
    targets: Mapping[str, frozenset[UUID]] = field(default_factory=dict)
    open_task: frozenset[tuple[str, UUID]] = frozenset()  # retention: held by an open task
    archived: frozenset[tuple[str, UUID]] = frozenset()  # retention: held by an archive

    def held(self, record_type: str) -> list[UUID]:
        return sorted((i for t, i in self.open_task | self.archived if t == record_type), key=str)


def _where(scope: Scope, record_type: str, *, planning: bool) -> list[ColumnElement[bool]]:
    """The records of this type the purge covers. A thread is covered once no message
    points at it (planning: no message the purge keeps)."""
    t = _TABLES[record_type]
    where: list[ColumnElement[bool]] = []
    match scope.kind:
        case "connection":
            where.append(t.c.connection_id == scope.target_id)
        case "project":
            ids = sorted(scope.targets.get(record_type, ()), key=str)
            where.append(t.c.id.in_(ids) if ids else false())
        case _:
            assert scope.cutoff is not None  # noqa: S101  # ck_purges_cutoff
            where.append(_at(record_type) < scope.cutoff)
            held = scope.held(record_type)
            if held:
                where.append(t.c.id.not_in(held))
            threads = scope.held("thread")
            if record_type == "message" and threads:
                where.append(or_(t.c.thread_id.is_(None), t.c.thread_id.not_in(threads)))
    if record_type == "thread":
        kept = [_messages.c.thread_id == t.c.id]
        if planning:
            # a message the purge removes does not keep its thread
            kept.append(~and_(*_where(scope, "message", planning=True)))
        where.append(~exists(select(literal_column("1")).select_from(_messages).where(*kept)))
    return where


async def holds(s: AsyncSession) -> tuple[frozenset[tuple[str, UUID]], frozenset[tuple[str, UUID]]]:
    """(held by an open task, held by an archived project) as (record type, id): from the
    live context items pointing at messages, notes and threads, asked of the modules that
    own their owners, and from the archived projects' own (archived) context items."""
    rows = (
        await s.execute(
            select(
                _context.c.id,
                _context.c.owner_type,
                _context.c.owner_id,
                _context.c.target_type,
                _context.c.target_id,
            ).where(
                _context.c.target_type.in_(ORDER),
                _context.c.target_id.is_not(None),
                _context.c.deleted_at.is_(None),
                _context.c.target_purged_at.is_(None),
            )
        )
    ).all()
    target = {row.id: (str(row.target_type), row.target_id) for row in rows}
    refs = [LinkRef(row.id, str(row.owner_type), row.owner_id) for row in rows]
    open_ids: set[UUID] = set()
    archived_ids: set[UUID] = set()
    own = [ref for ref in refs if ref.owner_type == "project"]
    if own:
        dormant = await projects.dormant_projects(s, {ref.owner_id for ref in own})
        archived_ids |= {ref.id for ref in own if ref.owner_id in dormant}
    for lookup in _holds.values():
        found = await lookup(s, refs)
        open_ids |= found.open_task
        archived_ids |= found.archived
    archived = {target[i] for i in archived_ids if i in target}
    for project_id in sorted(await projects.archived_project_ids(s), key=str):
        archived |= {(t, i) for _item, t, i in await _archived_items(s, project_id)}
    return frozenset(target[i] for i in open_ids if i in target), frozenset(archived)


async def _archived_items(s: AsyncSession, project_id: UUID) -> list[tuple[UUID, str, UUID]]:
    """(item id, target type, target id) of the project's archived context items that
    point at a message, note or thread."""
    found = []
    for _ref, raw in await blobs.blobs(
        s, module=ARCHIVE_MODULE, kind=ARCHIVE_KIND, project_id=project_id
    ):
        for row in blobs.decode_rows(raw):
            if row.get("target_type") in ORDER and row.get("target_id"):
                found.append((UUID(row["id"]), str(row["target_type"]), UUID(row["target_id"])))
    return found


async def project_targets(s: AsyncSession, project_id: UUID) -> dict[str, list[str]]:
    """The records a project purge covers, by type (ids as text, for the purge row)."""
    holders = [await lookup(s, project_id) for lookup in _holders.values()]
    held_by: list[ColumnElement[bool]] = [
        (_context.c.owner_type == "project") & (_context.c.owner_id == project_id)
    ]
    item_ids: set[UUID] = set()
    for found in holders:
        item_ids |= found.item_ids
        for owner_type, owners in found.owners.items():
            if owners:
                held_by.append(
                    (_context.c.owner_type == owner_type)
                    & _context.c.owner_id.in_(sorted(owners, key=str))
                )
    if item_ids:
        held_by.append(_context.c.id.in_(sorted(item_ids, key=str)))
    live = (
        await s.execute(
            select(_context.c.id, _context.c.target_type, _context.c.target_id).where(
                or_(*held_by),
                _context.c.target_type.in_(ORDER),
                _context.c.target_id.is_not(None),
                _context.c.deleted_at.is_(None),
            )
        )
    ).all()
    mine = {row.id for row in live}
    targets = {(str(row.target_type), row.target_id) for row in live}
    for item_id, target_type, target_id in await _archived_items(s, project_id):
        mine.add(item_id)
        targets.add((target_type, target_id))
    if targets:
        # A record something outside the project also links stays.
        elsewhere = await s.execute(
            select(_context.c.target_type, _context.c.target_id).where(
                _context.c.target_id.in_(sorted({i for _t, i in targets}, key=str)),
                _context.c.id.not_in(sorted(mine, key=str)) if mine else true(),
                _context.c.deleted_at.is_(None),
            )
        )
        targets -= {(str(row.target_type), row.target_id) for row in elsewhere}
        # So does one another archived project links: its links live in its archive.
        for other in sorted(await projects.archived_project_ids(s) - {project_id}, key=str):
            if not targets:
                break
            targets -= {(t, i) for _item, t, i in await _archived_items(s, other)}
    return {
        record_type: sorted(str(i) for t, i in targets if t == record_type) for record_type in ORDER
    }


def scope_of(row: Any, *, held: tuple[frozenset[Any], frozenset[Any]] | None = None) -> Scope:
    """The scope of a `purges` row (retention: with what is held now)."""
    targets = {
        record_type: frozenset(UUID(i) for i in ids)
        for record_type, ids in (row.targets or {}).items()
    }
    open_task, archived = held or (frozenset(), frozenset())
    return Scope(
        kind=row.scope,
        target_id=row.target_id,
        cutoff=row.cutoff,
        targets=targets,
        open_task=open_task,
        archived=archived,
    )


async def plan(s: AsyncSession, scope: Scope) -> dict[str, int]:
    """What the purge will remove, counted the way its batches count (the audit row's
    counts): records by type, their raw payloads, and the context items to mark."""
    counts = dict(ZERO_COUNTS)
    selects = []
    for record_type in ORDER:
        t = _TABLES[record_type]
        where = _where(scope, record_type, planning=True)
        counts[COUNT_KEYS[record_type]] = int(
            await s.scalar(select(func.count()).select_from(t).where(*where)) or 0
        )
        selects.append(select(t.c.raw_payload_id.label("raw_id")).where(*where))
        counts["context_items"] += int(
            await s.scalar(
                select(func.count())
                .select_from(_context)
                .where(
                    _context.c.target_type == record_type,
                    _context.c.target_id.in_(select(t.c.id).where(*where)),
                    _context.c.deleted_at.is_(None),
                    _context.c.target_purged_at.is_(None),
                )
            )
            or 0
        )
    covered = union_all(*selects).subquery()
    counts["raw_payloads"] = int(
        await s.scalar(
            select(func.count(func.distinct(_raw.c.id))).where(
                _raw.c.id.in_(select(covered.c.raw_id)),
                _raw.c.record_type.in_(_OWN_RAW_TYPES),
            )
        )
        or 0
    )
    return counts


# --- the batches --------------------------------------------------------------------------


async def run_batch(
    s: AsyncSession, purge_id: UUID, batch_no: int, *, limit: int, now: datetime
) -> int:
    """Batch `batch_no` (1-based) of the purge, in the caller's transaction: up to `limit`
    records of the first type that has any left. The number of records removed; -1 when
    this batch committed already (a retried step), 0 when nothing is left."""
    row = (await s.execute(select(_purges).where(_purges.c.id == purge_id).with_for_update())).one()
    if row.status == "done":
        return 0
    if row.batches >= batch_no:
        return -1
    scope = scope_of(row, held=await holds(s) if row.scope == "retention" else None)
    record_type, ids, raw_ids = await _take(s, scope, limit)
    counts = dict(ZERO_COUNTS)
    if ids:
        counts[COUNT_KEYS[record_type]] = len(ids)
        counts["raw_payloads"] = await _drop_raw(s, raw_ids)
        counts["context_items"] = await _mark_items(s, record_type, ids, now)
        await emit(
            s,
            ItemsPurgedV1(purge_id=purge_id, record_type=record_type, ids=ids),
            occurred_at=now,
        )
    total = {key: int((row.counts or {}).get(key, 0)) + n for key, n in counts.items()}
    await s.execute(
        update(_purges)
        .where(_purges.c.id == purge_id)
        .values(counts=total, batches=batch_no, status="running")
    )
    faults.killpoint(f"integrations.purge.batch_{batch_no}.committing")
    return len(ids)


async def _take(s: AsyncSession, scope: Scope, limit: int) -> tuple[str, list[UUID], set[UUID]]:
    """Deletes up to `limit` covered records of the first type that has any; (type, ids,
    their raw payload ids)."""
    for record_type in ORDER:
        t = _TABLES[record_type]
        rows = (
            await s.execute(
                select(t.c.id, t.c.raw_payload_id, _at(record_type).label("at"))
                .where(*_where(scope, record_type, planning=False))
                .order_by(t.c.id)
                .limit(limit)
            )
        ).all()
        if scope.kind == "retention":
            rows = [
                r
                for r in rows
                if rules.purge_candidates(
                    rules.IngestedLite(
                        at=r.at, linked_to_open_task=(record_type, r.id) in scope.open_task
                    ),
                    scope.cutoff,
                    (record_type, r.id) in scope.archived,
                )
            ]
        if not rows:
            continue
        ids = [r.id for r in rows]
        await s.execute(delete(t).where(t.c.id.in_(ids)))
        return record_type, ids, {r.raw_payload_id for r in rows if r.raw_payload_id}
    return ORDER[0], [], set()


async def _drop_raw(s: AsyncSession, raw_ids: Collection[UUID]) -> int:
    """Deletes these raw payloads (integrations' record types only); kept rows pointing at
    one lose the pointer first. The number deleted."""
    ids = sorted(raw_ids, key=str)
    if not ids:
        return 0
    ours: list[UUID] = list(
        await s.scalars(
            select(_raw.c.id).where(_raw.c.id.in_(ids), _raw.c.record_type.in_(_OWN_RAW_TYPES))
        )
    )
    if not ours:
        return 0
    for table in _POINTERS:
        await s.execute(
            update(table).where(table.c.raw_payload_id.in_(ours)).values(raw_payload_id=None)
        )
    result = await s.execute(delete(_raw).where(_raw.c.id.in_(ours)))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _mark_items(s: AsyncSession, record_type: str, ids: Sequence[UUID], now: datetime) -> int:
    """Marks the live context items pointing at the purged records; the number marked."""
    result = await s.execute(
        update(_context)
        .where(
            _context.c.target_type == record_type,
            _context.c.target_id.in_(list(ids)),
            _context.c.deleted_at.is_(None),
            _context.c.target_purged_at.is_(None),
        )
        .values(target_purged_at=now)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def finish(s: AsyncSession, purge_id: UUID, now: datetime) -> dict[str, int]:
    """The purge is done; its counts."""
    counts: dict[str, int] = (
        await s.execute(
            update(_purges)
            .where(_purges.c.id == purge_id)
            .values(status="done", finished_at=func.coalesce(_purges.c.finished_at, now))
            .returning(_purges.c.counts)
        )
    ).scalar_one()
    return counts
