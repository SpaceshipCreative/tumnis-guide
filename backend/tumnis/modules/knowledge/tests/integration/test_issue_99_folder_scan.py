"""Issue #99 (SEC-10, FR-15.12): files that reach a project folder through P1-15's paths
(an upload Tumnis places, a file the folder sync finds) start `pending_scan`, go through
P1-16's scan and extraction with `source = "storage"`, and are served only once `ready`.
An EICAR file ends `quarantined` and is never served."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.core.net import NetPolicy
from tumnis.modules.knowledge.tests._samples import eicar, fixture_bytes
from tumnis.modules.knowledge.tests.integration._upload import rows, scalar, settled, upload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SELF_HOSTED = NetPolicy(mode="self-hosted")
TERMINAL = frozenset({"ready", "quarantined", "failed"})
SCANNED = ["read", "scan", "sniff", "convert", "vlm", "store", "chunk", "index", "emit"]


async def _one(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _place(env: ExtractEnv, name: str, data: bytes) -> uuid.UUID:
    """`place_upload`, as P1-15's upload places a file in `uploads/`; the document's id."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(env.ws.ctx) as s:
        placed = await knowledge.place_upload(s, env.project_id, name, _one(data), net=SELF_HOSTED)
    return placed.document_id


async def _outside(env: ExtractEnv, path: str, data: bytes | None = None, *, to: str = "") -> None:
    """A change made outside Tumnis in the project folder: write `data` at `path` (replacing
    what is there), or with `to`, rename `path`."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    full = f"{env.folder}/{path}"
    async with (
        tenant_session(env.ws.ctx) as s,
        knowledge.open_backend(s, env.location_id, net=SELF_HOSTED) as backend,
    ):
        if to:
            await backend.move(full, f"{env.folder}/{to}")
            return
        current = await backend.stat(full)
        await backend.write(full, _one(data or b""), current.etag if current else None)


async def _sync(env: ExtractEnv) -> Any:
    """One `folder_sync` of the location, as the worker runs it, with the default
    extraction hook."""
    from dbos import SetWorkflowID  # noqa: PLC0415

    from tumnis.modules.knowledge import sync, workflows  # noqa: PLC0415

    sync.configure(net=SELF_HOSTED)
    try:
        with SetWorkflowID(f"issue-99-sync-{uuid.uuid4()}"):
            return await workflows.folder_sync(str(env.ws.id), str(env.location_id))
    finally:
        sync.configure(net=None)


def _doc_at(db: DbUrls, env: ExtractEnv, path: str) -> uuid.UUID:
    found = rows(
        db,
        "SELECT d.id FROM folder_files f JOIN documents d ON d.id = f.document_id"
        " WHERE f.location_id = %s AND f.path = %s AND f.deleted_at IS NULL",
        env.location_id,
        f"{env.folder}/{path}",
    )
    assert len(found) == 1, (path, found)
    return uuid.UUID(str(found[0]["id"]))


def _statuses(db: DbUrls, document_id: uuid.UUID) -> tuple[str, list[str]]:
    """The document's status and each version's, oldest first."""
    doc = scalar(db, "SELECT status FROM documents WHERE id = %s", document_id)
    versions = rows(
        db,
        "SELECT status FROM document_versions WHERE document_id = %s ORDER BY version_no",
        document_id,
    )
    return str(doc), [str(v["status"]) for v in versions]


async def _settled(
    db: DbUrls, document_id: uuid.UUID, *, versions: int = 1, timeout_s: float = 60
) -> list[str]:
    """Each version's status once all `versions` of the document are terminal."""
    deadline = time.monotonic() + timeout_s
    while True:
        _doc, found = _statuses(db, document_id)
        if len(found) >= versions and all(s in TERMINAL for s in found):
            return found
        if time.monotonic() > deadline:
            raise TimeoutError(f"{document_id} versions still {found}")
        await asyncio.sleep(0.1)


def _version_id(db: DbUrls, document_id: uuid.UUID) -> uuid.UUID:
    return uuid.UUID(
        str(
            scalar(
                db,
                "SELECT id FROM document_versions WHERE document_id = %s"
                " ORDER BY version_no DESC LIMIT 1",
                document_id,
            )
        )
    )


async def _refused(http: SessionClient, document_id: uuid.UUID) -> None:
    served = await http.get(f"/v1/files/{document_id}")
    assert served.status_code == 409, served.text
    assert served.json()["code"] == "not_available"


@pytest.mark.req("SEC-10", "FR-15.12")
@pytest.mark.wp("P1-15")
async def test_issue_99_placed_upload_is_scanned_before_it_is_served(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """An upload placed in the folder (`place_upload`) starts `pending_scan` (document and
    version) and is not served; its extraction runs with `source = "storage"` (scanned
    where it lies, never placed again) and only then is it `ready` and served, bytes
    unchanged."""
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    env = extract_env
    data = b"Kickoff notes from the client.\n"
    document_id = await _place(env, "kickoff.txt", data)

    assert _statuses(db, document_id) == ("pending_scan", ["pending_scan"])
    await _refused(session_client, document_id)

    await knowledge.enqueue_extract(env.ws.ctx, _version_id(db, document_id), "storage")
    assert await _settled(db, document_id) == ["ready"]
    assert env.steps == SCANNED
    served = await session_client.get(f"/v1/files/{document_id}")
    assert served.status_code == 200, served.text
    assert served.content == data


@pytest.mark.req("SEC-10", "FR-15.12")
@pytest.mark.wp("P1-15")
async def test_issue_99_placed_eicar_is_quarantined_and_never_served(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """An EICAR file placed through the folder path is `pending_scan`, then `quarantined`
    by the scan, and never served."""
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    env = extract_env
    document_id = await _place(env, "invoice.txt", eicar())

    assert _statuses(db, document_id) == ("pending_scan", ["pending_scan"])
    await _refused(session_client, document_id)

    await knowledge.enqueue_extract(env.ws.ctx, _version_id(db, document_id), "storage")
    assert await _settled(db, document_id) == ["quarantined"]
    assert env.steps == ["read", "scan", "quarantine"]
    assert _statuses(db, document_id)[0] == "quarantined"
    await _refused(session_client, document_id)


@pytest.mark.req("SEC-10", "FR-15.12")
@pytest.mark.wp("P1-15")
async def test_issue_99_folder_sync_scans_files_found_outside(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """Files dropped in the folder from outside are handed to the pipeline by the folder
    sync itself: a clean one ends `ready` and is served (its path relative to the folder),
    an EICAR one ends `quarantined`, stays where it is and is never served. The scanned
    text file is not taken for a note afterwards: the next sync writes nothing and raises
    no review."""
    env = extract_env
    clean = b"Rates: design 100, build 120.\n"
    await _outside(env, "uploads/rates.txt", clean)
    await _outside(env, "uploads/dropped.txt", eicar())

    await _sync(env)
    good, bad = _doc_at(db, env, "uploads/rates.txt"), _doc_at(db, env, "uploads/dropped.txt")

    assert await _settled(db, good) == ["ready"]
    assert await _settled(db, bad) == ["quarantined"]
    assert "scan" in env.steps
    assert "place" not in env.steps
    assert scalar(db, "SELECT path FROM documents WHERE id = %s", good) == "uploads/rates.txt"
    served = await session_client.get(f"/v1/files/{good}")
    assert served.status_code == 200, served.text
    assert served.content == clean
    await _refused(session_client, bad)

    count = "SELECT count(*) FROM documents WHERE project_id = %s"
    before = scalar(db, count, env.project_id)
    applied = await _sync(env)
    assert applied == {"status": "online", "applied": 0}
    assert scalar(db, "SELECT count(*) FROM review_items") == 0
    assert scalar(db, count, env.project_id) == before


@pytest.mark.req("SEC-10", "FR-15.12")
@pytest.mark.wp("P1-15")
async def test_issue_99_outside_edit_is_scanned_before_it_is_served(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """A served folder file renamed outside keeps its document (path followed), and an
    outside edit that turns it into EICAR makes a version the scan quarantines: the file
    now in the folder is never served."""
    env = extract_env
    await _outside(env, "uploads/terms.txt", b"Net 30 days.\n")
    await _sync(env)
    document_id = _doc_at(db, env, "uploads/terms.txt")
    assert await _settled(db, document_id) == ["ready"]

    await _outside(env, "uploads/terms.txt", to="uploads/contract terms.txt")
    await _sync(env)
    assert _doc_at(db, env, "uploads/contract terms.txt") == document_id
    path = scalar(db, "SELECT path FROM documents WHERE id = %s", document_id)
    assert path == "uploads/contract terms.txt"

    await _outside(env, "uploads/contract terms.txt", eicar())
    await _sync(env)
    assert await _settled(db, document_id, versions=2) == ["ready", "quarantined"]
    await _refused(session_client, document_id)


UPLOADED_FILES = "SELECT count(*) FROM documents WHERE project_id = %s AND source <> 'text'"


@pytest.mark.req("SEC-10", "FR-15.12")
@pytest.mark.wp("P1-15")
async def test_issue_99_placed_upload_is_recorded_so_sync_keeps_one_document(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """An upload the pipeline placed in `uploads/` is recorded as Tumnis's own file: the
    next folder sync links it to the upload's document and makes no second one."""
    env = extract_env
    response = await upload(
        session_client, env.project_id, "brief.docx", fixture_bytes("brief.docx")
    )
    doc = await settled(session_client, response.json()["id"])
    assert doc["status"] == "ready"

    await _sync(env)

    assert _doc_at(db, env, doc["path"]) == uuid.UUID(doc["id"])
    assert scalar(db, UPLOADED_FILES, env.project_id) == 1
    assert scalar(db, "SELECT count(*) FROM review_items") == 0


@pytest.mark.req("SEC-10", "FR-15.12")
@pytest.mark.wp("P1-15")
async def test_issue_99_sniffed_text_upload_is_not_a_note(
    extract_env: ExtractEnv, dbos: type[DBOS], session_client: SessionClient, db: DbUrls
) -> None:
    """A `.txt` upload is sniffed as kind `text`, yet it is a file, not a note: it cannot
    be edited as a text entry (409 `not_text`), and the folder sync neither writes it out
    as a note nor raises a conflict for it."""
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    env = extract_env
    data = b"Call Sam on Friday about the launch.\n"
    response = await upload(session_client, env.project_id, "call.txt", data)
    doc = await settled(session_client, response.json()["id"])
    assert (doc["status"], doc["kind"]) == ("ready", "text")

    with pytest.raises(ProblemError) as refused:
        async with tenant_session(env.ws.ctx) as s:
            await knowledge.update_text_document(
                s, uuid.UUID(doc["id"]), body_md="edited", version=doc["version"]
            )
    assert refused.value.code == "not_text"

    await _sync(env)
    await _sync(env)
    assert scalar(db, UPLOADED_FILES, env.project_id) == 1
    assert scalar(db, "SELECT count(*) FROM review_items") == 0
    written = rows(db, "SELECT path FROM folder_files WHERE document_id = %s", doc["id"])
    assert [r["path"] for r in written] == [f"{env.folder}/{doc['path']}"]
    served = await session_client.get(f"/v1/files/{doc['id']}")
    assert served.content == data
