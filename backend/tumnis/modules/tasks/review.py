"""Review items: the only way any module queues a human decision (P0-18, R-03).

A module registers each kind it queues at startup with `register_review_kind` (a
duplicate kind raises): its owner, the payload schema, the actions the review screen
offers (R-04's accept, edit, reject, snooze, answer, approve, deny) and how far its impact
reaches. `add_review_item` validates the payload with the kind's schema and writes the row
in the caller's transaction (its `session`, else a new one in the current workspace
context); an open item with the same `dedupe_key` is returned instead of a second one.
`kind` is stored as plain text: the registry, not a Postgres enum, says what is valid.
P0-18 registers no production kinds; later WPs do (`project_match`, P3-06).

`review_badge_count` counts what waits now: not decided, not trashed, not snoozed past
`now`. Re-exported by tasks.api; other modules import it from there.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, StringConstraints
from sqlalchemy import Table, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import tenancy
from tumnis.core.errors import ProblemError
from tumnis.core.live import mark_changed
from tumnis.core.tenancy import tenant_session
from tumnis.modules.github import api as github
from tumnis.modules.tasks.models import ReviewItem

_review: Table = ReviewItem.__table__  # type: ignore[assignment]

LIVE_ENTITY: Final = "review_item"  # R-05
KIND_RE: Final = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
ACTIONS: Final = frozenset({"accept", "edit", "reject", "snooze", "answer", "approve", "deny"})
ImpactScope = Literal["task", "project", "workspace"]
OPEN_WHERE: Final = "decided_at IS NULL AND deleted_at IS NULL"


@dataclass(frozen=True, slots=True)
class ReviewKindSpec:
    kind: str  # text slug, ^[a-z][a-z0-9_]{2,40}$; e.g. "project_match"
    owner_module: str
    payload_schema: type[BaseModel]
    actions: tuple[str, ...]  # a subset of ACTIONS (P1-13, R-04)
    impact_scope: ImpactScope


class DuplicateReviewKind(ValueError):  # noqa: N818  # reads as the condition it reports
    pass


class UnknownReviewKind(ProblemError):  # noqa: N818  # the plan's name
    """No module registered this kind: 422 `unknown_review_kind`."""

    def __init__(self, kind: str) -> None:
        super().__init__(422, "unknown_review_kind", f"No review kind {kind!r} is registered")
        self.kind = kind


_KINDS: dict[str, ReviewKindSpec] = {}


def register_review_kind(spec: ReviewKindSpec) -> None:
    """Registers a kind at module startup. A kind already registered raises
    DuplicateReviewKind; a malformed slug or an unknown action raises ValueError."""
    if not KIND_RE.fullmatch(spec.kind):
        raise ValueError(f"review kind {spec.kind!r} is not a slug ^[a-z][a-z0-9_]{{2,40}}$")
    unknown = set(spec.actions) - ACTIONS
    if unknown or not spec.actions:
        raise ValueError(
            f"review kind {spec.kind!r}: actions must be a subset of {sorted(ACTIONS)}"
        )
    existing = _KINDS.get(spec.kind)
    if existing is not None:
        raise DuplicateReviewKind(
            f"review kind {spec.kind!r} is already registered by {existing.owner_module}"
        )
    _KINDS[spec.kind] = spec


def review_kinds() -> Mapping[str, ReviewKindSpec]:
    """Every registered kind, read-only."""
    return MappingProxyType(_KINDS)


class TargetRef(BaseModel):
    """What the decision is about: a row of some module (`type` names it)."""

    type: Annotated[str, StringConstraints(min_length=1, max_length=60)]
    id: UUID


async def _insert(  # noqa: PLR0917  # add_review_item's fields, spelled out
    s: AsyncSession,
    kind: str,
    target: TargetRef,
    project_id: UUID | None,
    payload: dict[str, Any],
    dedupe_key: str | None,
) -> UUID:
    stmt = pg_insert(_review).values(
        kind=kind,
        project_id=project_id,
        target_type=target.type,
        target_id=target.id,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[_review.c.workspace_id, _review.c.dedupe_key],
            index_where=text(OPEN_WHERE),
        )
    item_id: UUID | None = await s.scalar(stmt.returning(_review.c.id))
    if item_id is None:  # an open item holds this dedupe key
        item_id = await s.scalar(
            select(_review.c.id).where(_review.c.dedupe_key == dedupe_key, text(OPEN_WHERE))
        )
        assert item_id is not None  # noqa: S101  # the conflict names a live row
    mark_changed(s, LIVE_ENTITY, item_id)
    return item_id


async def add_review_item(
    kind: str,
    *,
    target: TargetRef,
    project_id: UUID | None,
    payload: Mapping[str, Any],
    dedupe_key: str | None = None,
    session: AsyncSession | None = None,
) -> UUID:
    """Queues a decision of a registered `kind`; returns its id. UnknownReviewKind (422
    `unknown_review_kind`) for a kind nobody registered; pydantic's ValidationError when the
    payload fails the kind's schema. Runs in `session` when given (the caller's
    transaction), else in a new transaction of the current workspace context."""
    spec = _KINDS.get(kind)
    if spec is None:
        raise UnknownReviewKind(kind)
    body = spec.payload_schema.model_validate(dict(payload)).model_dump(mode="json")
    if session is not None:
        return await _insert(session, kind, target, project_id, body, dedupe_key)
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("add_review_item called outside a workspace context")
    async with tenant_session(ctx) as s:
        return await _insert(s, kind, target, project_id, body, dedupe_key)


async def review_badge_count(s: AsyncSession, now: datetime) -> int:
    """Open items: not decided, not trashed, and not snoozed past `now`."""
    count: int | None = await s.scalar(
        select(func.count())
        .select_from(_review)
        .where(
            text(OPEN_WHERE),
            or_(_review.c.snoozed_until.is_(None), _review.c.snoozed_until <= now),
        )
    )
    return count or 0


# --- Flags (P2-13) ---------------------------------------------------------------------------

RESULT_KIND: Final = "result"  # P2-04 queues these: an agent's finished work, with its links
CHECKS_RED: Final = "checks_red"


async def flag_pull_request_results(s: AsyncSession, key: str, *, red: bool) -> int:
    """Sets (`red`) or clears the `checks_red` flag on the open `result` review items whose
    links name the pull request `key` (`owner/repo#number`, `github.pull_request_key`). A
    decided or trashed item is left alone, and an item already in the wanted state is not
    touched, so a repeated delivery changes nothing. Returns the items changed."""
    repo, _, number = key.rpartition("#")
    needle = f"/{repo}/pull/{number}".lower()
    rows = (
        await s.execute(
            select(_review.c.id, _review.c.payload, _review.c.flags).where(
                _review.c.kind == RESULT_KIND,
                text(OPEN_WHERE),
                func.strpos(func.lower(_review.c.payload["links"].astext), needle) > 0,
            )
        )
    ).all()
    changed = 0
    for item_id, payload, flags in rows:
        links = payload.get("links") if isinstance(payload, dict) else None
        names = [
            github.pull_request_key(str(link.get("url", "")))
            for link in (links if isinstance(links, list) else [])
            if isinstance(link, dict)
        ]
        if key not in names or (CHECKS_RED in flags) == red:
            continue
        wanted = [*flags, CHECKS_RED] if red else [f for f in flags if f != CHECKS_RED]
        await s.execute(_review.update().where(_review.c.id == item_id).values(flags=wanted))
        mark_changed(s, LIVE_ENTITY, item_id)
        changed += 1
    return changed
