"""Renames pair within one project folder (P1-15): a file with the same bytes in another
project's folder neither steals the pair nor makes it ambiguous."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from tumnis.modules.knowledge.storage import FileStat
from tumnis.modules.knowledge.sync import _listed_entries, _Scan

WHEN = datetime(2026, 3, 9, tzinfo=UTC)
HASH = "ab" * 32
ACME, SITE = uuid.uuid4(), uuid.uuid4()
ROOTS = {ACME: "Acme", SITE: "Site"}


def _record(path: str) -> dict[str, Any]:
    return {
        "path": path,
        "size": 3,
        "mtime": WHEN,
        "etag": HASH,
        "content_hash": HASH,
        "origin": "external",
        "document_id": uuid.uuid4(),
        "synced_version": 1,
        "delete_confirmed": False,
    }


def _scan(records: list[str], files: dict[str, uuid.UUID]) -> _Scan:
    scan = _Scan()
    for path, project in files.items():
        scan.files[path] = FileStat(path=path, size=3, mtime=WHEN, etag=HASH)
        scan.project_of[path] = project
        scan.hashes[path] = HASH
    scan.records = {path: _record(path) for path in records}  # type: ignore[misc]
    return scan


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
def test_same_bytes_in_another_folder_do_not_block_the_rename() -> None:
    scan = _scan(["Acme/uploads/x.pdf"], {"Acme/uploads/y.pdf": ACME, "Site/uploads/z.pdf": SITE})
    renamed = {e[0]: e[4] for e in _listed_entries(scan, ROOTS) if e[4] is not None}
    assert renamed == {"Acme/uploads/y.pdf": "Acme/uploads/x.pdf"}


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
def test_a_file_never_pairs_across_folders() -> None:
    scan = _scan(["Acme/uploads/x.pdf"], {"Site/uploads/x.pdf": SITE})
    entries = _listed_entries(scan, ROOTS)
    assert all(e[4] is None for e in entries)
    assert {e[0] for e in entries} == {"Acme/uploads/x.pdf", "Site/uploads/x.pdf"}
