"""Helpers for the upload and extraction integration tests (P1-16). No assertions live
here: they upload through the real route, wait for the pipeline and read rows."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

from tests._pg import OWNER

if TYPE_CHECKING:
    import httpx

    from tests._pg import DbUrls

TERMINAL = frozenset({"ready", "quarantined", "failed"})


async def upload(
    http: httpx.AsyncClient,
    project_id: object | None,
    name: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
    title: str | None = None,
) -> httpx.Response:
    """`POST /v1/knowledge/documents` with `data` as `name` (the client's Content-Type is
    whatever the caller claims; the server ignores it)."""
    fields: dict[str, str] = {}
    if project_id is not None:
        fields["project_id"] = str(project_id)
    if title is not None:
        fields["title"] = title
    return await http.post(
        "/v1/knowledge/documents", data=fields, files={"file": (name, data, content_type)}
    )


async def settled(
    http: httpx.AsyncClient, document_id: str, *, timeout_s: float = 60
) -> dict[str, Any]:
    """The document once the pipeline has finished with it (ready, quarantined, failed)."""
    deadline = time.monotonic() + timeout_s
    while True:
        response = await http.get(f"/v1/knowledge/documents/{document_id}")
        response.raise_for_status()
        doc: dict[str, Any] = response.json()
        if doc["status"] in TERMINAL:
            return doc
        if time.monotonic() > deadline:
            raise TimeoutError(f"{document_id} still {doc['status']} after {timeout_s} s")
        await asyncio.sleep(0.1)


def rows(db: DbUrls, sql: str, *params: object) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(sql, params).fetchall()


def scalar(db: DbUrls, sql: str, *params: object) -> Any:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


def chunk_rows(db: DbUrls, document_id: str) -> list[dict[str, Any]]:
    """The chunks of a document's current version, in order."""
    return rows(
        db,
        "SELECT c.ordinal, c.text, c.heading_path, c.page_from, c.page_to, c.extractor "
        "FROM chunks c JOIN documents d ON d.current_version_id = c.document_version_id "
        "WHERE d.id = %s AND c.deleted_at IS NULL ORDER BY c.ordinal",
        document_id,
    )
