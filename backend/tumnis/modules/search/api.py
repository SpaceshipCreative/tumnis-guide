"""search public functions and DTOs; the only file other modules may import (P0-20, FR-3.9).

One Postgres full-text index serves global search and the project and task typeaheads,
ranked by text match, recency and project match (constants in `rules.py`):

    score = ts_rank_cd(tsv, query) * (PROJECT_BOOST when project_id matches, else 1)
            / (1 + age_days / RECENCY_DAYS)

One statement per call. `search` pages by keyset on (score descending, entity_id), with
`now` frozen in the cursor, so page 2 ranks like page 1 however the clock or the index
moved in between. The typeaheads are the same query restricted by entity type, without
snippets or a cursor. User text is only ever a bound argument of `phraseto_tsquery`,
`plainto_tsquery` or `to_tsquery` (after `rules.parse_query`).

The index is written only by the subscribers in `events.py` (`index_event`), since search
calls no other module (boundary rule 4). Every read runs in the caller's workspace
context: row-level security keeps each workspace's rows to itself. `project_ids` (a
project-limited key, R-28) keeps only rows of those projects.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.events import EventEnvelope
from tumnis.core.pagination import Cursor, Page
from tumnis.modules.search.rules import PROJECT_BOOST, RECENCY_DAYS, parse_query, tsquery_parts

Scope = Literal["all", "tasks", "projects"]
EntityType = Literal["task", "project"]

TYPEAHEAD_LIMIT: Final = 8  # plan default
_SCOPES: Final[dict[str, tuple[str, ...]]] = {
    "all": ("task", "project"),
    "tasks": ("task",),
    "projects": ("project",),
}
# (sql_fn from rules.tsquery_parts) -> the call with its fixed configuration.
_PART_SQL: Final = {
    "phraseto_tsquery": "phraseto_tsquery('english'::regconfig, CAST(:{name} AS text))",
    "plainto_tsquery": "plainto_tsquery('english'::regconfig, CAST(:{name} AS text))",
    "to_tsquery": "to_tsquery('simple'::regconfig, CAST(:{name} AS text))",
}
_SNIPPET: Final = (
    "ts_headline('english'::regconfig, CASE WHEN r.body = '' THEN r.title ELSE r.body END,"
    " q.query, 'MaxFragments=1,MaxWords=12,MinWords=4')"
)


_RANKING: Final = """
WITH q AS (SELECT {query} AS query),
ranked AS (
  SELECT si.entity_type, si.entity_id, si.project_id, si.title, si.body,
         CAST(ts_rank_cd(si.tsv, q.query) AS float8)
           * CASE WHEN si.project_id = CAST(:project_id AS uuid)
                  THEN CAST(:boost AS float8) ELSE 1.0 END
           / (1.0 + GREATEST(0.0, CAST(extract(epoch FROM
                    (CAST(:now AS timestamptz) - si.source_updated_at)) AS float8))
                    / 86400.0 / CAST(:recency_days AS float8)) AS score
  FROM search_index si, q
  WHERE si.tsv @@ q.query AND si.deleted_at IS NULL
    AND si.entity_type = ANY(CAST(:entity_types AS text[])) {limited}
)
SELECT r.entity_type, r.entity_id, r.project_id, r.title, r.score, {snippet} AS snippet
FROM ranked r, q
{keyset}
ORDER BY r.score DESC, r.entity_id
LIMIT :limit
"""


class SearchHit(BaseModel):
    entity_type: EntityType
    entity_id: UUID
    project_id: UUID | None
    title: str
    snippet: str
    score: float


def _statement(
    parts: Sequence[tuple[str, str]],
    *,
    project_ids: Collection[UUID] | None,
    after: bool,
    snippets: bool,
) -> tuple[str, dict[str, Any]]:
    """The ranking SQL for these parts, and the bound values of the query text."""
    params: dict[str, Any] = {}
    calls = []
    for i, (fn, arg) in enumerate(parts):
        params[f"part{i}"] = arg
        calls.append(_PART_SQL[fn].format(name=f"part{i}"))
    limited = "AND si.project_id = ANY(CAST(:project_ids AS uuid[]))" if project_ids else ""
    keyset = (
        "WHERE r.score < CAST(:after_score AS float8) OR"
        " (r.score = CAST(:after_score AS float8) AND r.entity_id > CAST(:after_id AS uuid))"
        if after
        else ""
    )
    # Only fixed fragments are formatted in; every user value is a bound parameter.
    sql = _RANKING.format(
        query=" && ".join(calls),
        limited=limited,
        snippet=_SNIPPET if snippets else "''",
        keyset=keyset,
    )
    return sql, params


async def _ranked(
    s: AsyncSession,
    q: str,
    *,
    entity_types: tuple[str, ...],
    project_id: UUID | None,
    project_ids: Collection[UUID] | None,
    now: datetime,
    limit: int,
    after: Cursor | None = None,
    snippets: bool = True,
) -> list[SearchHit]:
    parts = tsquery_parts(parse_query(q))
    if not parts or (project_ids is not None and not project_ids):
        return []
    sql, params = _statement(
        parts, project_ids=project_ids, after=after is not None, snippets=snippets
    )
    params |= {
        "project_id": project_id,
        "boost": PROJECT_BOOST,
        "recency_days": RECENCY_DAYS,
        "now": now,
        "entity_types": list(entity_types),
        "limit": limit,
    }
    if project_ids:
        params["project_ids"] = list(project_ids)
    if after is not None:
        params |= {"after_score": after.keys[0], "after_id": after.id}
    rows = (await s.execute(text(sql), params)).mappings().all()
    return [SearchHit.model_validate(dict(row)) for row in rows]


async def search(
    s: AsyncSession,
    q: str,
    *,
    scope: Scope = "all",
    project_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = 20,
    now: datetime,
    project_ids: Collection[UUID] | None = None,
) -> Page[SearchHit]:
    """Tasks and projects matching `q`, best first; rows of `project_id` are boosted. An
    empty query is an empty page. A cursor carries the first page's `now`; a malformed one
    is 400 `invalid_cursor`."""
    after = None
    if cursor is not None:
        after = Cursor.decode(cursor, [float, datetime])
        now = after.keys[1]
    hits = await _ranked(
        s,
        q,
        entity_types=_SCOPES[scope],
        project_id=project_id,
        project_ids=project_ids,
        now=now,
        limit=limit + 1,
        after=after,
    )
    next_cursor = None
    if len(hits) > limit:
        hits = hits[:limit]
        last = hits[-1]
        next_cursor = Cursor((last.score, now), last.entity_id).encode()
    return Page[SearchHit](items=hits, next_cursor=next_cursor)


async def typeahead_projects(
    s: AsyncSession,
    q: str,
    limit: int = TYPEAHEAD_LIMIT,
    *,
    now: datetime,
    project_ids: Collection[UUID] | None = None,
) -> list[SearchHit]:
    """Live projects matching `q` (archived ones leave the index), best first."""
    return await _ranked(
        s,
        q,
        entity_types=("project",),
        project_id=None,
        project_ids=project_ids,
        now=now,
        limit=limit,
        snippets=False,
    )


async def typeahead_tasks(
    s: AsyncSession,
    q: str,
    project_id: UUID | None,
    limit: int = TYPEAHEAD_LIMIT,
    *,
    now: datetime,
    project_ids: Collection[UUID] | None = None,
) -> list[SearchHit]:
    """Live tasks matching `q`, best first; tasks of `project_id` are boosted."""
    return await _ranked(
        s,
        q,
        entity_types=("task",),
        project_id=project_id,
        project_ids=project_ids,
        now=now,
        limit=limit,
        snippets=False,
    )


# --- Indexing (the subscribers in events.py) ----------------------------------------------

BODY_MAX_BYTES: Final = 8_192  # plan default: 8 KB of body per row


class _TaskDoc(BaseModel):
    """What task events carry for search (tasks' TaskDoc, read without importing tasks)."""

    title: str
    body: str = ""
    deleted: bool = False
    project_id: UUID | None = None
    updated_at: datetime | None = None


class _Change(BaseModel):
    """One row's new state; None leaves a column as it is."""

    entity_type: EntityType
    entity_id: UUID
    project_id: UUID | None = None
    title: str | None = None
    body: str | None = None
    deleted: bool | None = None
    at: datetime


def _cap(body: str) -> str:
    return body.encode()[:BODY_MAX_BYTES].decode(errors="ignore")


def _task_change(payload: dict[str, Any], at: datetime) -> _Change:
    doc = _TaskDoc.model_validate(payload["doc"])
    return _Change(
        entity_type="task",
        entity_id=payload["task_id"],
        project_id=doc.project_id or payload.get("project_id"),
        title=doc.title,
        body=_cap(doc.body),
        deleted=doc.deleted,
        at=doc.updated_at or at,
    )


def _project_change(payload: dict[str, Any], at: datetime) -> _Change:
    name = payload.get("name")
    archived = payload.get("archived")
    return _Change(
        entity_type="project",
        entity_id=payload["project_id"],
        project_id=payload["project_id"],
        title=name,
        body=None if name is None else _cap(payload.get("goal") or ""),
        deleted=archived,
        at=at,
    )


def change_for(envelope: EventEnvelope) -> _Change | None:
    """The index change an event makes; None for an event search does not index."""
    payload, at = envelope.payload, envelope.occurred_at
    match envelope.name:
        case "task.created" | "task.updated":
            return _task_change(payload, at)
        case "project.created":
            return _project_change({**payload, "archived": False}, at)
        case "project.updated":
            return _project_change(payload, at)
        case "project.archived":
            return _project_change({"project_id": payload["project_id"], "archived": True}, at)
    return None


# Newest source wins: a late (older) event changes nothing. A row first made by an event
# without text (an archive seen before its create) holds an empty title until text comes.
_UPSERT: Final = text(
    """
    INSERT INTO search_index AS si (workspace_id, entity_type, entity_id, project_id, title,
                                    body, source_updated_at, deleted_at)
    VALUES (:ws, :entity_type, :entity_id, CAST(:project_id AS uuid),
            COALESCE(CAST(:title AS text), ''), COALESCE(CAST(:body AS text), ''), :at,
            CASE WHEN CAST(:deleted AS boolean) THEN CAST(:at AS timestamptz) END)
    ON CONFLICT (workspace_id, entity_type, entity_id) DO UPDATE SET
      project_id = COALESCE(EXCLUDED.project_id, si.project_id),
      title = CASE WHEN CAST(:title AS text) IS NULL THEN si.title ELSE EXCLUDED.title END,
      body = CASE WHEN CAST(:body AS text) IS NULL THEN si.body ELSE EXCLUDED.body END,
      deleted_at = CASE WHEN CAST(:deleted AS boolean) IS NULL THEN si.deleted_at
                        WHEN CAST(:deleted AS boolean)
                        THEN COALESCE(si.deleted_at, EXCLUDED.source_updated_at) END,
      source_updated_at = EXCLUDED.source_updated_at,
      version = si.version + 1
    WHERE si.source_updated_at <= EXCLUDED.source_updated_at
    RETURNING 1
    """
)


async def index_event(s: AsyncSession, envelope: EventEnvelope) -> int:
    """Applies a task or project event to the index in the caller's transaction (in the
    event's workspace context). Idempotent: the same event again writes the same row.
    Returns 1 when the row changed, 0 for a stale or unindexed event."""
    change = change_for(envelope)
    if change is None:
        return 0
    params = change.model_dump(exclude={"entity_type", "entity_id"}) | {
        "ws": envelope.workspace_id,
        "entity_type": change.entity_type,
        "entity_id": change.entity_id,
    }
    return len((await s.execute(_UPSERT, params)).all())
