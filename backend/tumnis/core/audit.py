"""Append-only audit log with a per-workspace SHA-256 hash chain (P0-15, SEC-3).

`record` writes one row inside the caller's transaction, so the row commits (or rolls
back) with the action it records; an event subscriber could fail on its own, and SEC-3
wants every gated action recorded with the action. Rows are chained per workspace:
`hash = sha256(prev_hash + canonical(row))`, seq 1 chaining from GENESIS. No role the app
uses can change a row (grants and the immutability trigger, core_0006_audit), and
`verify_chain` finds edits and deletions made directly in the database. A chain cannot
show rows removed from its end: `anchor` records the head after each clean nightly
verify, and `verify_chain` reports a head below the latest anchor.
"""

import hashlib
import ipaddress
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import db, request_meta
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.types import SYSTEM_ACTOR

GENESIS: Final = bytes(32)
PROJECT_KEY: Final = "project_id"  # the details key a project's rows carry (P2-17)
SENSITIVE_KEY: Final = re.compile(
    r"(token|secret|password|passwd|hmac|key|authorization|cookie|body|prompt|content|text)",
    re.IGNORECASE,
)
MAX_DETAIL_STR: Final = 200  # plan default
# A Tumnis credential (API key, task token, device token) wherever it sits in a string.
CREDENTIAL: Final = re.compile(r"tm[ntd]_[A-Za-z0-9_\-]")
REDACTED: Final = "[redacted]"

BreakKind = Literal["hash_mismatch", "gap", "anchor_mismatch", "truncated_after_anchor"]


@dataclass(frozen=True)
class ChainBreak:
    workspace_id: UUID
    seq: int
    kind: BreakKind


def redact_details(details: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively drops values under sensitive keys (replaced by "[redacted]"), truncates
    long strings, and drops any string that looks like a Tumnis credential (tmn_, tmt_,
    tmd_ prefixes) wherever it sits. Values that are not JSON types become strings first,
    so the result is plain JSON (what jsonb stores and the hash chain covers)."""
    return {str(key): _redact_value(str(key), value) for key, value in details.items()}


def _redact_value(key: str, value: Any) -> Any:
    if SENSITIVE_KEY.search(key):
        return REDACTED
    return _clean(value)


def _clean(value: Any) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return redact_details(value)
    if isinstance(value, list | tuple | set | frozenset):
        return [_clean(item) for item in value]
    text = value if isinstance(value, str) else str(value)
    if CREDENTIAL.search(text):
        return REDACTED
    return text if len(text) <= MAX_DETAIL_STR else text[:MAX_DETAIL_STR] + "…"


@dataclass(frozen=True)
class AuditRow:
    """The chained fields of one audit_log row, normalised the way they read back from
    Postgres (UTC timestamp, UUIDs and the address as text, details as plain JSON)."""

    workspace_id: UUID
    seq: int
    occurred_at: datetime
    actor_type: str
    actor_id: UUID | None
    action: str
    target_type: str | None
    target_id: UUID | None
    source_ip: str | None
    user_agent: str | None
    correlation_id: str | None
    reason: str | None
    details: Mapping[str, Any]


class ReasonRequiredError(ValueError):
    """`record` for an action in REASON_REQUIRED without a non-empty reason."""


class NoWorkspaceError(RuntimeError):
    """`record` outside a workspace context: an audit row always belongs to a workspace."""


# Actions that must carry a reason (approvals, rejections, purges, threshold edits; P2-05,
# P3-09, P3-08).
REASON_REQUIRED: Final[frozenset[str]] = frozenset(
    {"approval.granted", "approval.denied", "data.purged", "threshold.changed"}
)
VERIFY_PAGE: Final = 1_000  # plan default

_CONTEXT = text(
    "SELECT app.current_workspace_id() AS workspace_id, app.current_actor() AS actor,"
    " pg_advisory_xact_lock(hashtextextended('audit:' || app.current_workspace_id()::text, 0))"
)
_HEAD = text("SELECT seq, hash FROM audit_log WHERE workspace_id = :ws ORDER BY seq DESC LIMIT 1")
_INSERT = text(
    "INSERT INTO audit_log (workspace_id, seq, occurred_at, actor_type, actor_id, action,"
    " target_type, target_id, source_ip, user_agent, correlation_id, reason, details,"
    " prev_hash, hash) VALUES (:workspace_id, :seq, :occurred_at, :actor_type, :actor_id,"
    " :action, :target_type, :target_id, CAST(:source_ip AS inet), :user_agent,"
    " :correlation_id, :reason, CAST(:details AS jsonb), :prev_hash, :hash)"
)
_ANCHORS = text("SELECT seq, hash FROM audit_anchors WHERE workspace_id = :ws ORDER BY seq")
_ANCHOR = text(
    "INSERT INTO audit_anchors (workspace_id, seq, hash, anchored_at)"
    " VALUES (:ws, :seq, :hash, :anchored_at) ON CONFLICT (workspace_id, seq) DO NOTHING"
)
_PAGE = text(
    "SELECT workspace_id, seq, occurred_at, actor_type, actor_id, action, target_type,"
    " target_id, host(source_ip) AS source_ip, user_agent, correlation_id, reason, details,"
    " prev_hash, hash FROM audit_log WHERE workspace_id = :ws AND seq > :after"
    " ORDER BY seq LIMIT :limit"
)


def chain_hash(prev_hash: bytes, row: AuditRow) -> bytes:
    canonical = json.dumps(
        [
            str(row.workspace_id),
            row.seq,
            row.occurred_at.astimezone(UTC).isoformat(),
            row.actor_type,
            str(row.actor_id) if row.actor_id else None,
            row.action,
            row.target_type,
            str(row.target_id) if row.target_id else None,
            row.source_ip,
            row.user_agent,
            row.correlation_id,
            row.reason,
            row.details,
        ],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(prev_hash + canonical).digest()


def _actor(actor: str) -> tuple[str, UUID | None]:
    """ "system" or "<kind>:<uuid>" (tumnis.core.types.ActorRef) as (actor_type, actor_id)."""
    if actor == "system":
        return "system", None
    kind, _, ident = actor.partition(":")
    return kind, UUID(ident)


def _address(value: str | None) -> str | None:
    """The address as Postgres' host() prints it back, so the hash sees one spelling."""
    if value is None:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


async def record(
    session: AsyncSession,
    action: str,
    *,
    target: tuple[str, UUID] | None = None,
    reason: str | None = None,
    details: Mapping[str, Any] | None = None,
    occurred_at: datetime,
    project_id: UUID | None = None,
) -> None:
    """Writes one row in the caller's transaction (so it commits with the action).
    `project_id` goes into the details (P2-17): the row then shows in that project's
    Activity (`list_for_project`).

    Serializes per workspace with pg_advisory_xact_lock(hashtextextended('audit:' ||
    workspace, 0)), reads the head (max seq), computes seq + 1 and the hash, inserts. The
    workspace and the actor are the transaction's (tumnis.core.tenancy); source address,
    user agent and correlation ID come from RequestMeta (None outside a request)."""
    if action in REASON_REQUIRED and not (reason and reason.strip()):
        raise ReasonRequiredError(f"{action} needs a reason")
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    context = (await session.execute(_CONTEXT)).one()
    if context.workspace_id is None:
        raise NoWorkspaceError(f"audit.record({action!r}) outside a workspace context")
    workspace_id: UUID = context.workspace_id
    head = (await session.execute(_HEAD, {"ws": workspace_id})).first()
    seq, prev_hash = (head.seq + 1, bytes(head.hash)) if head else (1, GENESIS)
    actor_type, actor_id = _actor(context.actor)
    meta = request_meta.current()
    row = AuditRow(
        workspace_id=workspace_id,
        seq=seq,
        occurred_at=occurred_at.astimezone(UTC),
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target[0] if target else None,
        target_id=target[1] if target else None,
        source_ip=_address(meta.source_ip),
        user_agent=meta.user_agent,
        correlation_id=meta.correlation_id,
        reason=reason,
        details=redact_details(
            {**(details or {}), **({} if project_id is None else {PROJECT_KEY: str(project_id)})}
        ),
    )
    await session.execute(
        _INSERT,
        {
            **{f.name: getattr(row, f.name) for f in fields(AuditRow)},
            "details": json.dumps(row.details),
            "prev_hash": prev_hash,
            "hash": chain_hash(prev_hash, row),
        },
    )


@dataclass(frozen=True)
class ProjectAuditRow:
    """An audit row as a project's Activity shows it (P2-17, FR-2.6)."""

    id: UUID
    occurred_at: datetime
    action: str
    actor_type: str
    target_type: str | None
    target_id: UUID | None
    reason: str | None
    details: Mapping[str, Any]


_PROJECT_FIRST = text(
    "SELECT id, occurred_at, action, actor_type, target_type, target_id, reason, details"
    " FROM audit_log WHERE workspace_id = app.current_workspace_id()"
    " AND details ? 'project_id' AND details->>'project_id' = :project"
    " ORDER BY occurred_at DESC, id DESC LIMIT :limit"
)
_PROJECT_AFTER = text(
    "SELECT id, occurred_at, action, actor_type, target_type, target_id, reason, details"
    " FROM audit_log WHERE workspace_id = app.current_workspace_id()"
    " AND details ? 'project_id' AND details->>'project_id' = :project"
    " AND (occurred_at, id) < (CAST(:at AS timestamptz), CAST(:id AS uuid))"
    " ORDER BY occurred_at DESC, id DESC LIMIT :limit"
)


async def list_for_project(
    session: AsyncSession,
    project_id: UUID,
    *,
    before: tuple[datetime, UUID] | None = None,
    limit: int,
) -> list[ProjectAuditRow]:
    """The workspace's rows written for the project (`record(..., project_id=)`), newest
    first, those before `before` (occurred_at, id) only. Rows written without a project
    (all before P2-17) are never listed."""
    params: dict[str, Any] = {"project": str(project_id), "limit": limit}
    if before is not None:
        params["at"], params["id"] = before
    rows = await session.execute(_PROJECT_FIRST if before is None else _PROJECT_AFTER, params)
    return [ProjectAuditRow(**dict(row._mapping)) for row in rows]


def _row(values: Any) -> AuditRow:
    return AuditRow(
        workspace_id=values.workspace_id,
        seq=values.seq,
        occurred_at=values.occurred_at,
        actor_type=values.actor_type,
        actor_id=values.actor_id,
        action=values.action,
        target_type=values.target_type,
        target_id=values.target_id,
        source_ip=values.source_ip,
        user_agent=values.user_agent,
        correlation_id=values.correlation_id,
        reason=values.reason,
        details=values.details,
    )


async def verify_chain(session: AsyncSession, workspace_id: UUID) -> list[ChainBreak]:
    """Walks seq 1..head in pages of 1,000 (plan default) recomputing hashes; checks every
    anchor's (seq, hash) matches the row at that seq and that head >= the latest anchor.

    A row whose recomputed hash or `prev_hash` does not match is a `hash_mismatch`; a
    missing seq is a `gap` (the row after it starts a new link, so one deletion is one
    break); an anchor whose row is gone or differs is an `anchor_mismatch`; a head below
    the latest anchor is `truncated_after_anchor`, reported at the anchored seq."""
    anchors = {
        seq: bytes(hash_) for seq, hash_ in await session.execute(_ANCHORS, {"ws": workspace_id})
    }
    breaks: list[ChainBreak] = []
    seen: dict[int, bytes] = {}
    expected_seq, expected_prev = 1, GENESIS
    while True:
        page = (
            await session.execute(
                _PAGE, {"ws": workspace_id, "after": expected_seq - 1, "limit": VERIFY_PAGE}
            )
        ).all()
        for values in page:
            prev_hash, stored = bytes(values.prev_hash), bytes(values.hash)
            if values.seq != expected_seq:
                breaks += [
                    ChainBreak(workspace_id, s, "gap") for s in range(expected_seq, values.seq)
                ]
                expected_prev = prev_hash
            if prev_hash != expected_prev or chain_hash(expected_prev, _row(values)) != stored:
                breaks.append(ChainBreak(workspace_id, values.seq, "hash_mismatch"))
            if values.seq in anchors:
                seen[values.seq] = stored
            expected_seq, expected_prev = values.seq + 1, stored
        if len(page) < VERIFY_PAGE:
            break
    head = expected_seq - 1
    for seq, hash_ in sorted(anchors.items()):
        if seq <= head and seen.get(seq) != hash_:
            breaks.append(ChainBreak(workspace_id, seq, "anchor_mismatch"))
    if anchors and max(anchors) > head:
        breaks.append(ChainBreak(workspace_id, max(anchors), "truncated_after_anchor"))
    return breaks


async def head(session: AsyncSession, workspace_id: UUID) -> tuple[int, bytes] | None:
    """The workspace's latest (seq, hash), or None before its first row."""
    row = (await session.execute(_HEAD, {"ws": workspace_id})).first()
    return (int(row.seq), bytes(row.hash)) if row else None


async def anchor(session: AsyncSession, workspace_id: UUID, now: datetime) -> None:
    """Inserts (head seq, head hash) into audit_anchors after a clean verify; nothing
    before the first row, and nothing new when the head was anchored already."""
    current = await head(session, workspace_id)
    if current is None:
        return
    seq, hash_ = current
    await session.execute(
        _ANCHOR, {"ws": workspace_id, "seq": seq, "hash": hash_, "anchored_at": now}
    )


async def workspace_ids(session: AsyncSession) -> list[UUID]:
    """Every workspace, through app.list_workspace_ids() (the app role sees none without a
    context); callable in a session with no workspace in context."""
    return list((await session.execute(text("SELECT app.list_workspace_ids()"))).scalars())


async def verify_workspaces(only: Sequence[UUID] | None = None) -> dict[UUID, list[ChainBreak]]:
    """`verify_chain` for every workspace (or `only` those), each in its own transaction as
    the app role with that workspace in context, as `system`."""
    if only is None:
        async with db.app_sessionmaker()() as s, s.begin():
            only = await workspace_ids(s)
    results: dict[UUID, list[ChainBreak]] = {}
    for workspace_id in only:
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            results[workspace_id] = await verify_chain(s, workspace_id)
    return results
