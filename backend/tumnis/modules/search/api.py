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

from collections.abc import Sequence
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
    project_ids: Sequence[UUID] | None,
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
    project_ids: Sequence[UUID] | None,
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
    project_ids: Sequence[UUID] | None = None,
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
    project_ids: Sequence[UUID] | None = None,
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
    project_ids: Sequence[UUID] | None = None,
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


async def index_event(s: AsyncSession, envelope: EventEnvelope) -> int:
    raise NotImplementedError
