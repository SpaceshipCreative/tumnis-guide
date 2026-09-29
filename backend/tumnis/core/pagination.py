"""Cursor pagination by keyset (P0-10, PERF-1).

Lists order by `(keys..., id)` and continue after the last row returned, never by OFFSET, so
a page never repeats or skips a row that existed when the walk started, however many rows
are inserted meanwhile. With uuidv7 ids the default order `(id)` is creation order.

The cursor is base64url(JSON {"v": 1, "k": [key values...], "id": "<uuid>"}); a cursor that
does not decode, has another version or the wrong number of keys is 400 `invalid_cursor`.
Routes take `page: Annotated[PageParams, Depends(page_params)]` (`cursor`, `limit` 1 to
200, default 50) and return `Page[Model]` with `RoutePolicy(paginated=True)`.
"""

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, func, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.errors import ProblemError

LIMIT_DEFAULT: Final = 50  # plan defaults
LIMIT_MAX: Final = 200
CURSOR_VERSION: Final = 1


class Page[T: BaseModel](BaseModel):
    items: list[T]
    next_cursor: str | None


@dataclass(frozen=True)
class PageParams:
    cursor: str | None
    limit: int


def page_params(
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=LIMIT_MAX)] = LIMIT_DEFAULT,
) -> PageParams:
    return PageParams(cursor, limit)


@dataclass(frozen=True)
class SortKey:
    column: ColumnElement[Any]
    nulls_last_sentinel: Any | None = None  # e.g. date.max for due_on: NULLs sort last

    def expression(self) -> ColumnElement[Any]:
        if self.nulls_last_sentinel is None:
            return self.column
        return func.coalesce(self.column, literal(self.nulls_last_sentinel, self.column.type))

    def python_type(self) -> type[Any]:
        try:
            kind: type[Any] = self.column.type.python_type
        except NotImplementedError:
            return str
        return kind


def invalid_cursor() -> ProblemError:
    # detail == code: the audit API's clients (P0-15) read `detail`.
    return ProblemError(400, "invalid_cursor", "invalid_cursor")


def _dump(value: Any) -> Any:
    if isinstance(value, date | datetime):  # datetime is a date
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _load(value: Any, kind: type[Any]) -> Any:
    if value is None:
        raise ValueError("a cursor key is never null")
    if kind is datetime:
        return datetime.fromisoformat(value)
    if kind is date:
        return date.fromisoformat(value)
    if kind is UUID:
        return UUID(value)
    if kind in (int, float, str, bool):
        if not isinstance(value, kind):
            raise TypeError(value)
        return value
    return kind(value)


@dataclass(frozen=True)
class Cursor:
    keys: tuple[Any, ...]
    id: UUID

    def encode(self) -> str:
        raw = {"v": CURSOR_VERSION, "k": [_dump(k) for k in self.keys], "id": str(self.id)}
        text = json.dumps(raw, separators=(",", ":"))
        return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, cursor: str, kinds: Sequence[type[Any]]) -> "Cursor":
        """The cursor's keys as `kinds`; ProblemError 400 `invalid_cursor` otherwise."""
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("v") != CURSOR_VERSION:
                raise ValueError("cursor version")
            keys = value["k"]
            if not isinstance(keys, list) or len(keys) != len(kinds):
                raise ValueError("cursor keys")
            return cls(
                tuple(_load(k, kind) for k, kind in zip(keys, kinds, strict=True)),
                UUID(value["id"]),
            )
        except (binascii.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise invalid_cursor() from exc


async def paginate[T: BaseModel](
    session: AsyncSession,
    stmt: Select[Any],
    *,
    keys: Sequence[SortKey],
    id_col: ColumnElement[UUID],
    cursor: str | None,
    limit: int,
    model: type[T],
    descending: bool = False,
) -> Page[T]:
    """Keyset pagination on (keys..., id), ascending (or all descending). The cursor is
    base64url(JSON {"v": 1, "k": [...], "id": "..."}). A malformed cursor raises
    ProblemError(400, "invalid_cursor")."""
    exprs = [key.expression() for key in keys]
    ordered = [*exprs, id_col]
    if cursor is not None:
        after = Cursor.decode(cursor, [key.python_type() for key in keys])
        bound = [literal(v, e.type) for v, e in zip(after.keys, exprs, strict=True)]
        position = tuple_(*ordered)
        start = tuple_(*bound, literal(after.id, id_col.type))
        stmt = stmt.where(position < start if descending else position > start)
    labelled = [expr.label(f"_page_k{i}") for i, expr in enumerate(exprs)]
    stmt = stmt.add_columns(*labelled, id_col.label("_page_id"))
    stmt = stmt.order_by(*(c.desc() if descending else c.asc() for c in ordered))
    rows = (await session.execute(stmt.limit(limit + 1))).mappings().all()
    items = [model.model_validate(dict(row)) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        values = tuple(last[f"_page_k{i}"] for i in range(len(exprs)))
        next_cursor = Cursor(values, last["_page_id"]).encode()
    return Page[model](items=items, next_cursor=next_cursor)  # type: ignore[valid-type]
