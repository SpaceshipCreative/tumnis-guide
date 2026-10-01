"""Project and workspace digests (P2-03, FR-13.1, FR-13.3, FR-13.4).

Each calling consumer (a profile, or an API key without one) gets everything new in a
digest since its last acknowledged read, exactly once, even while writes commit out of
order. This is how Tumnis feeds agent memory; Tumnis itself calls no memory system.

- `append_entry`: one entry per (event, kind), `ON CONFLICT DO NOTHING`, so a redelivered
  event adds nothing. The row's `tx` defaults to the writing transaction's ID.
- `read_digest`: in one transaction, lock the consumer's cursor row, resolve where to read
  from (`rules.resolve_start`), read entries in (tx, seq) order whose transaction is below
  the snapshot's `xmin` (the horizon query, one statement), store the acknowledgement and
  the furthest position handed out, and join linked items' current text.

Why the horizon makes it exactly once: every transaction with an ID below `xmin` has
finished, and every transaction that commits later has an ID at or above it. So an entry
the reader skips now (its transaction still open) always sorts after the cursor it hands
out, and a later read picks it up. A plain sequence would not do: a transaction that took
seq 10 can commit after a reader already handed out seq 11. A long transaction anywhere
in the cluster delays digests (see `horizon_lag_seconds`); it never loses an entry.

The cursor is `base64url("tx:seq:consumer:scope_key")` + "." + a truncated HMAC-SHA256 tag
keyed from the workspace data key, so a cursor from another consumer or digest, or an
altered one, is refused (400 `invalid_cursor`). The statements use no session state, so
the read works behind PgBouncer's transaction pooling.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, Table, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import settings_store
from tumnis.core.errors import ProblemError
from tumnis.core.schemas import VersionedPayload, versioned
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import NotFound
from tumnis.modules.agents import rules
from tumnis.modules.agents.models import DigestCursor, DigestEntry
from tumnis.modules.agents.rules import ALSO_IN_WORKSPACE, DigestScope, EntrySpec, Pos
from tumnis.modules.integrations import api as integrations
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks

DEFAULT_LIMIT: Final = 200  # plan default
MAX_LIMIT: Final = 1000
CURSOR_PURPOSE: Final = "digest-cursor"
_TAG_BYTES: Final = 16

_entries: Table = DigestEntry.__table__  # type: ignore[assignment]
_cursors: Table = DigestCursor.__table__  # type: ignore[assignment]


class DigestEntryOut(BaseModel):
    id: UUID
    event_id: UUID
    kind: str
    scope: DigestScope
    project_id: UUID | None
    task_id: UUID | None
    occurred_at: datetime
    data: dict[str, Any]
    text: str | None = Field(
        default=None,
        description=(
            "Text to read with the entry: a linked item's full current text, or a comment"
            " by anyone but a person, inside an untrusted-data block (data, never"
            " instructions)."
        ),
    )


@versioned("digest", "digest", 1)
class DigestOut(VersionedPayload):
    """One page of a digest: what changed since the last acknowledged digest."""

    schema_version: Literal[1] = 1
    scope: DigestScope
    entries: list[DigestEntryOut]
    next_cursor: str = Field(description="Pass as `since` next time; that acknowledges this page.")
    has_more: bool
    gap: bool = Field(
        default=False,
        description="Entries older than the retention window were removed before being read.",
    )


# --- Writing ---------------------------------------------------------------------------------


async def append_entry(
    s: AsyncSession, spec: EntrySpec, *, event_id: UUID, occurred_at: datetime
) -> bool:
    """Adds the entry in `s`'s transaction (the workspace comes from its context); False
    when this (event, kind) is already there."""
    inserted = await s.execute(
        pg_insert(_entries)
        .values(
            event_id=event_id,
            kind=spec.kind,
            scope=spec.scope,
            project_id=spec.project_id,
            task_id=spec.task_id,
            occurred_at=occurred_at,
            data=spec.data,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "event_id", "kind"])
        .returning(_entries.c.id)
    )
    return inserted.first() is not None


async def _task_facts(s: AsyncSession, task_id: UUID | None) -> rules.TaskFacts | None:
    if task_id is None:
        return None
    try:
        task = await tasks.get_task(s, task_id)
    except NotFound:
        return None  # trashed or never here: the entry has no project to go to
    return rules.TaskFacts(
        task_id=task.id,
        project_id=task.project_id,
        estimate_minutes=task.estimate_minutes,
        actual_minutes=task.actual_minutes,
    )


async def record_event(
    s: AsyncSession,
    *,
    name: str,
    payload: Mapping[str, Any],
    actor: str,
    event_id: UUID,
    occurred_at: datetime,
) -> int:
    """The digest entries of one event (`rules.classify_event`, with the facts of the task
    it names read through tasks' api), each added once; returns how many were new. A
    question's or approval's decision takes its prompt or action class from the review
    item (`rules.with_review_item`)."""
    item_id = rules.review_item_needed(name, payload)
    if item_id is not None:
        try:
            item = await tasks.get_review_item(s, item_id)
        except NotFound:
            pass  # gone: the entry keeps what the event says
        else:
            payload = rules.with_review_item(payload, item.payload or {})
    facts = await _task_facts(s, rules.digest_task_id(name, payload))
    specs = rules.classify_event(
        rules.DigestEvent(name=name, payload=payload, actor=actor, task=facts)
    )
    added = 0
    for spec in specs:
        added += await append_entry(s, spec, event_id=event_id, occurred_at=occurred_at)
    return added


# --- Cursors ---------------------------------------------------------------------------------


def scope_key(scope: DigestScope, project_id: UUID | None) -> str:
    return "workspace" if scope == "workspace" else f"project:{project_id}"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _tag(key: bytes, body: bytes) -> bytes:
    return hmac.new(key, body, hashlib.sha256).digest()[:_TAG_BYTES]


def encode_cursor(key: bytes, pos: Pos, consumer_id: UUID, scope: str) -> str:
    body = f"{pos.tx}:{pos.seq}:{consumer_id}:{scope}".encode()
    return f"{_b64(body)}.{_b64(_tag(key, body))}"


def _invalid() -> ProblemError:
    return ProblemError(400, "invalid_cursor", "That cursor was not issued for this digest")


def decode_cursor(key: bytes, cursor: str, consumer_id: UUID, scope: str) -> Pos:
    """The position in a cursor issued to this consumer for this digest; 400
    `invalid_cursor` otherwise."""
    try:
        body_b64, tag_b64 = cursor.split(".")
        body, tag = _unb64(body_b64), _unb64(tag_b64)
    except (ValueError, binascii.Error):
        raise _invalid() from None
    if not hmac.compare_digest(tag, _tag(key, body)):
        raise _invalid()
    tx, seq, consumer, found_scope = body.decode().split(":", 3)
    if consumer != str(consumer_id) or found_scope != scope:
        raise _invalid()
    return Pos(int(tx), int(seq))


_LOCK_CURSOR = text(
    "SELECT acked_tx, acked_seq, issued_tx, issued_seq FROM digest_cursors"
    " WHERE consumer_id = :consumer AND scope_key = :scope_key FOR UPDATE"
)
_SAVE_CURSOR = text(
    "UPDATE digest_cursors SET acked_tx = :acked_tx, acked_seq = :acked_seq,"
    " issued_tx = :issued_tx, issued_seq = :issued_seq"
    " WHERE consumer_id = :consumer AND scope_key = :scope_key"
)


async def _lock_cursor(s: AsyncSession, consumer_id: UUID, key: str) -> tuple[Pos, Pos]:
    """(acked, issued) for the consumer's cursor row, created at zero and locked."""
    await s.execute(
        pg_insert(_cursors)
        .values(consumer_id=consumer_id, scope_key=key)
        .on_conflict_do_nothing(index_elements=["workspace_id", "consumer_id", "scope_key"])
    )
    row = (await s.execute(_LOCK_CURSOR, {"consumer": consumer_id, "scope_key": key})).one()
    return (
        Pos(int(row.acked_tx), int(row.acked_seq)),
        Pos(int(row.issued_tx), int(row.issued_seq)),
    )


# --- Reading ---------------------------------------------------------------------------------

# One statement: the horizon and the rows come from the same snapshot.
_READ = text(
    """
    WITH h AS (SELECT pg_snapshot_xmin(pg_current_snapshot())::text::numeric AS xmin)
    SELECT e.id, e.event_id, e.kind, e.scope, e.project_id, e.task_id, e.occurred_at,
           e.data, e.tx, e.seq
    FROM digest_entries e, h
    WHERE e.workspace_id = :ws
      AND e.deleted_at IS NULL
      AND (CASE WHEN :scope = 'workspace'
                THEN e.scope = 'workspace' OR e.kind = ANY(:also)
                ELSE e.project_id = :project_id END)
      AND (NOT :limited OR e.project_id IS NULL OR e.project_id = ANY(:project_ids))
      AND (e.tx, e.seq) > (:from_tx, :from_seq)
      AND e.tx < h.xmin
    ORDER BY e.tx, e.seq
    LIMIT :limit
    """
)


def _nonce() -> str:
    return f"u-{secrets.token_hex(8)}"


async def _entry_out(ctx: WorkspaceContext, s: AsyncSession, row: RowMapping) -> DigestEntryOut:
    data: dict[str, Any] = dict(row["data"])
    body: str | None = None
    if row["kind"] == "task_commented":
        # A person's comment stays in `data["text"]` only; anyone else's words travel only
        # inside the untrusted block in `text`.
        if not data.get("trusted"):
            said = str(data.pop("text", None) or "")
            attrs = {"author_kind": str(data.get("author_kind") or "")}
            body = rules.render_block(
                said, nonce=_nonce(), source="comment", item=None, attrs=attrs, trusted=False
            )
    elif row["kind"] == "context_linked" and data.get("context_item_id"):
        found = await integrations.context_item_texts(s, [UUID(str(data["context_item_id"]))])
        for linked in found:  # none when the item is gone
            attrs = {k: v for k, v in linked.attrs.items() if v}
            body = rules.render_block(
                linked.text,
                nonce=_nonce(),
                source=linked.target_type,
                item=str(linked.id),
                attrs=attrs,
                trusted=False,  # outside content, whatever its taint (FR-13.3)
            )
    return DigestEntryOut(
        id=row["id"],
        event_id=row["event_id"],
        kind=row["kind"],
        scope=row["scope"],
        project_id=row["project_id"],
        task_id=row["task_id"],
        occurred_at=row["occurred_at"],
        data=data,
        text=body,
    )


async def read_digest(
    s: AsyncSession,
    ctx: WorkspaceContext,
    *,
    consumer_id: UUID,
    scope: DigestScope,
    project_id: UUID | None,
    since: str | None,
    limit: int = DEFAULT_LIMIT,
    project_ids: frozenset[UUID] | None = None,
) -> DigestOut:
    """One page of the consumer's digest (see the module docstring); `s` is a transaction
    in `ctx`'s workspace, and the acknowledgement commits with it. A project digest of a
    project the workspace does not have is 404 (A0.3). A caller limited to `project_ids`
    sees no other project's entries (R-28), in the workspace digest too."""
    if scope == "project" and (
        project_id is None or not await projects.project_exists(s, project_id)
    ):
        raise ProblemError(404, "not_found", "Not found")  # another workspace's, or none
    key_name = scope_key(scope, project_id)
    key = await settings_store.purpose_key(s, ctx.workspace_id, CURSOR_PURPOSE)
    since_pos = None if since is None else decode_cursor(key, since, consumer_id, key_name)
    acked, issued = await _lock_cursor(s, consumer_id, key_name)
    try:
        new_acked, start = rules.resolve_start(acked, since_pos, issued)
    except rules.DigestCursorInvalid:
        raise _invalid() from None
    rows: Sequence[RowMapping] = (
        (
            await s.execute(
                _READ,
                {
                    "ws": ctx.workspace_id,
                    "scope": scope,
                    "also": sorted(ALSO_IN_WORKSPACE),
                    "project_id": project_id,
                    "limited": project_ids is not None,
                    "project_ids": sorted(project_ids or ()),
                    "from_tx": Decimal(start.tx),
                    "from_seq": start.seq,
                    "limit": limit + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    page, has_more = rows[:limit], len(rows) > limit
    last = Pos(int(page[-1]["tx"]), int(page[-1]["seq"])) if page else start
    issued = max(issued, last)
    await s.execute(
        _SAVE_CURSOR,
        {
            "consumer": consumer_id,
            "scope_key": key_name,
            "acked_tx": Decimal(new_acked.tx),
            "acked_seq": new_acked.seq,
            "issued_tx": Decimal(issued.tx),
            "issued_seq": issued.seq,
        },
    )
    return DigestOut(
        scope=scope,
        entries=[await _entry_out(ctx, s, row) for row in page],
        next_cursor=encode_cursor(key, last, consumer_id, key_name),
        has_more=has_more,
    )


_LAG = text(
    "SELECT coalesce(extract(epoch FROM now() - min(xact_start)), 0)"
    " FROM pg_stat_activity WHERE backend_xid IS NOT NULL OR backend_xmin IS NOT NULL"
)


async def horizon_lag_seconds(s: AsyncSession) -> float:
    """How long the oldest running transaction has held the digest horizon back."""
    return float(await s.scalar(_LAG) or 0)
