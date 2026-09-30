"""Archived blobs and their helpers (P2-18, FR-5.10): manifests, zstd, and the
`archived_blobs` table (revision core_0009_archived_blobs).

A module archiving a project moves rows it owns out of their table into one or more
compressed blobs, and puts them back byte for byte on unarchive:

- `snapshot_rows` reads rows as JSON (`to_jsonb`, every column, ids and times included)
  and `restore_rows` inserts them again with `jsonb_populate_recordset`, so no module
  spells out its columns twice. Generated columns are left for Postgres to compute.
- `put_blob` stores one compressed blob (a replayed step writes nothing new: `ON CONFLICT
  DO NOTHING` on `(workspace, module, kind, project, ref)`); `blobs` reads them back,
  checking each one's sha256; `delete_blobs` drops them once restored or purged.

`Manifest` is the one manifest shape of the archive (the daemon keeps a copy of the same
canonical form): `(relative path, size, sha256)` per entry, sorted by path, and a digest
over its canonical JSON. Compression uses python-zstandard
(https://python-zstandard.readthedocs.io/): one-shot `ZstdCompressor.compress`, which
writes the content size into the frame, so `ZstdDecompressor.decompress` needs no size.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

import zstandard
from sqlalchemy import BigInteger, Column, LargeBinary, MetaData, Table, Text, delete, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

LEVEL: Final = 10  # zstd level for rows: small blobs, written once
CODEC: Final = "zstd"

_metadata = MetaData()
# Mirrors revision core_0009_archived_blobs (the migration creates it; this is for queries).
archived_blobs = Table(
    "archived_blobs",
    _metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")),
    Column("workspace_id", PG_UUID(as_uuid=True)),
    Column("deleted_at", Text),
    Column("module", Text),
    Column("kind", Text),
    Column("project_id", PG_UUID(as_uuid=True)),
    Column("ref", Text),
    Column("codec", Text),
    Column("raw_size", BigInteger),
    Column("stored_size", BigInteger),
    Column("sha256", Text),
    Column("data", LargeBinary),
)
_t = archived_blobs


class BlobCorrupt(RuntimeError):  # noqa: N818  # the archive's word
    """A stored blob whose bytes no longer match its sha256."""


@dataclass(frozen=True)
class Manifest:
    """(relative path, size, sha256) per entry, sorted by path."""

    entries: tuple[tuple[str, int, str], ...]

    def canonical(self) -> bytes:
        return json.dumps(
            [[path, size, sha] for path, size, sha in self.entries],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def digest(self) -> str:
        """sha256 of the canonical JSON: `[[path, size, sha256], ...]`, no spaces."""
        return hashlib.sha256(self.canonical()).hexdigest()

    @classmethod
    def of(cls, entries: Iterable[tuple[str, int, str]]) -> "Manifest":
        return cls(tuple(sorted(entries)))

    @classmethod
    def of_files(cls, files: dict[str, bytes]) -> "Manifest":
        return cls.of(
            (path, len(data), hashlib.sha256(data).hexdigest()) for path, data in files.items()
        )


def compress(raw: bytes, level: int = LEVEL) -> bytes:
    return zstandard.ZstdCompressor(level=level).compress(raw)


def decompress(stored: bytes) -> bytes:
    return zstandard.ZstdDecompressor().decompress(stored)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- rows as JSON ----------------------------------------------------------------------------


async def _columns(s: AsyncSession, table: str) -> list[str]:
    """The table's columns an INSERT may set (generated ones are Postgres's)."""
    names = await s.scalars(
        text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public'"
            " AND table_name = :t AND is_generated = 'NEVER' ORDER BY ordinal_position"
        ),
        {"t": table},
    )
    found = list(names)
    if not found:
        raise ValueError(f"no such table {table!r}")
    return found


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"not an identifier: {name!r}")
    return f'"{name}"'


async def snapshot_rows(
    s: AsyncSession, table: str, where: str, params: dict[str, Any], *, order_by: str = "id"
) -> list[dict[str, Any]]:
    """Every column of the matching rows of `table` (a module's own table, named by the
    module, with its own `where` over alias `t`), as JSON objects in `order_by` order."""
    rows = await s.scalars(
        text(
            f"SELECT to_jsonb(t) FROM {_ident(table)} t WHERE {where}"  # noqa: S608  # module constants
            f" ORDER BY t.{_ident(order_by)}"
        ),
        params,
    )
    return list(rows)


async def restore_rows(s: AsyncSession, table: str, rows: Sequence[dict[str, Any]]) -> int:
    """Insert `rows` (from `snapshot_rows`) into `table` as they were; rows whose id is
    back already are skipped (a replayed restore). The number inserted."""
    if not rows:
        return 0
    cols = ", ".join(_ident(c) for c in await _columns(s, table))
    result = await s.execute(
        text(
            f"INSERT INTO {_ident(table)} ({cols}) OVERRIDING SYSTEM VALUE"  # noqa: S608
            f" SELECT {cols} FROM jsonb_populate_recordset(NULL::{_ident(table)},"
            " CAST(:rows AS jsonb)) ON CONFLICT DO NOTHING"
        ),
        {"rows": json.dumps(list(rows))},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def encode_rows(rows: Sequence[dict[str, Any]]) -> bytes:
    return json.dumps(list(rows), separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_rows(raw: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(raw)
    return rows


# --- the table ---------------------------------------------------------------------------------


async def put_blob(
    s: AsyncSession,
    *,
    module: str,
    kind: str,
    project_id: UUID,
    ref: str,
    raw: bytes,
    level: int = LEVEL,
) -> None:
    """Store `raw` compressed; a blob by that name already there is kept as it is."""
    stored = compress(raw, level)
    await s.execute(
        insert(_t)
        .values(
            module=module,
            kind=kind,
            project_id=project_id,
            ref=ref,
            codec=CODEC,
            raw_size=len(raw),
            stored_size=len(stored),
            sha256=sha256_hex(raw),
            data=stored,
        )
        .on_conflict_do_nothing(
            index_elements=["workspace_id", "module", "kind", "project_id", "ref"]
        )
    )


def _which(module: str, kind: str | None, project_id: UUID) -> list[Any]:
    where = [_t.c.module == module, _t.c.project_id == project_id, _t.c.deleted_at.is_(None)]
    if kind is not None:
        where.append(_t.c.kind == kind)
    return where


async def blobs(
    s: AsyncSession, *, module: str, kind: str, project_id: UUID
) -> list[tuple[str, bytes]]:
    """(ref, raw bytes) of the project's blobs of this kind, by ref; BlobCorrupt when one's
    bytes do not match its sha256."""
    rows = await s.execute(
        select(_t.c.ref, _t.c.data, _t.c.sha256)
        .where(*_which(module, kind, project_id))
        .order_by(_t.c.ref)
    )
    out = []
    for ref, data, sha in rows:
        raw = decompress(data)
        if sha256_hex(raw) != sha:
            raise BlobCorrupt(f"{module}/{kind}/{ref} does not match its sha256")
        out.append((ref, raw))
    return out


async def has_blob(s: AsyncSession, *, module: str, kind: str, project_id: UUID, ref: str) -> bool:
    found = await s.scalar(
        select(_t.c.id).where(*_which(module, kind, project_id), _t.c.ref == ref).limit(1)
    )
    return found is not None


async def delete_blobs(
    s: AsyncSession,
    *,
    module: str,
    project_id: UUID,
    kind: str | None = None,
    ref: str | None = None,
) -> int:
    """Drop the project's blobs of `module` (of `kind`, and that `ref`, when given)."""
    where = _which(module, kind, project_id)
    if ref is not None:
        where.append(_t.c.ref == ref)
    result = await s.execute(delete(_t).where(*where))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def delete_project_blobs(s: AsyncSession, project_id: UUID) -> int:
    """Every blob of the project, whatever its module (a purge)."""
    result = await s.execute(delete(_t).where(_t.c.project_id == project_id))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
