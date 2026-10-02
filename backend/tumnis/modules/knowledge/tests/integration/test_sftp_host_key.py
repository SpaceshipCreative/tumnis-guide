"""SFTP host-key pinning (P3-14, FR-15.7): the server's key is shown on the first connect
and pinned only when the user confirms its exact fingerprint; a later connection that
meets another key is refused, the location is marked `host_key_changed`, the change is
audited and put in the review queue, and nothing is written until the user re-pins."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests._sftp import (
    fingerprint,
    make_root,
    raw_sftp,
    server_files,
    sftp_storage,
)
from tumnis.modules.knowledge.tests.integration._locations import (
    SELF_HOSTED,
    pinned_sftp_location,
    sftp_location_in,
)
from tumnis.modules.knowledge.tests.integration.test_locations import _chunks, _project

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests._services import SftpEndpoint
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

WRONG = "SHA256:" + "A" * 43  # well-formed, and no key's fingerprint


def _rows(db: DbUrls, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(sql, params).fetchall()


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P3-14")
async def test_host_key_pinned_on_confirmed_first_connect(
    db: DbUrls, knowledge_ws: WorkspaceHandle, sftp_server: SftpEndpoint
) -> None:
    """T-P3-14-09
    Saving an SFTP location probes the server: it is kept `pending_host_key` with the
    server key's SHA256 fingerprint shown and no key pinned. Confirming a wrong fingerprint
    is refused (422 `fingerprint_mismatch`) and pins nothing; the exact fingerprint pins
    the key, the location comes online, and files can be written and read through it.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.tests.contract.storage_contract import (  # noqa: PLC0415
        chunks,
        read_all,
    )

    ws = knowledge_ws
    root = await make_root(sftp_server)
    expected = fingerprint(sftp_server.host_key)
    async with tenant_session(ws.ctx) as s:
        pending = await knowledge.create_location(
            s, sftp_location_in(sftp_server, root), net=SELF_HOSTED
        )
    assert pending.kind == "sftp"
    assert pending.status == "pending_host_key"
    assert pending.pending_host_key_sha256 == expected
    assert pending.host_key_sha256 is None

    with pytest.raises(ProblemError) as wrong:
        async with tenant_session(ws.ctx) as s:
            await knowledge.confirm_host_key(s, pending.id, WRONG, net=SELF_HOSTED)
    assert (wrong.value.status, wrong.value.code) == (422, "fingerprint_mismatch")
    async with tenant_session(ws.ctx) as s:
        (still,) = [loc for loc in await knowledge.list_locations(s) if loc.id == pending.id]
    assert (still.status, still.host_key_sha256) == ("pending_host_key", None)

    async with tenant_session(ws.ctx) as s:
        pinned = await knowledge.confirm_host_key(s, pending.id, expected, net=SELF_HOSTED)
    assert pinned.status == "online"
    assert pinned.host_key_sha256 == expected
    assert pinned.pending_host_key_sha256 is None
    (stored,) = _rows(
        db, "SELECT host_key_pinned FROM storage_locations WHERE id = %s", pending.id
    )[0]
    assert stored == sftp_server.host_key

    async with (
        tenant_session(ws.ctx) as s,
        knowledge.open_backend(s, pending.id, net=SELF_HOSTED) as backend,
    ):
        await backend.write("hello.txt", chunks(b"hello"), if_match=None)
        assert await read_all(backend, "hello.txt") == b"hello"
    assert await server_files(sftp_server, root) == ["hello.txt"]


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P3-14")
async def test_changed_host_key_refused(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock, sftp_server: SftpEndpoint
) -> None:
    """T-P3-14-10
    The server comes back with a new host key: the adapter refuses to connect
    (`HostKeyChanged`), a connection test marks the location `host_key_changed` (the old
    pin kept, the new key shown), writes audit `storage.host_key_mismatch` and one
    `storage_host_key_changed` review item, and nothing is written: a note save queues and
    a file write is refused. Testing again never re-pins. Re-pinning is the confirm flow
    with a reason; then files flow again.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.sftp import HostKeyChanged  # noqa: PLC0415

    ws = knowledge_ws
    location, root = await pinned_sftp_location(ws, sftp_server, default=True)
    old = fingerprint(sftp_server.host_key)
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        folder = await knowledge.assign_project_folder(s, project_id)
        note_id = await knowledge.put_text_document(
            s, project_id, title="Plan", body_md="# Plan\n", role=None
        )
    assert folder is not None
    assert folder.location_id == location.id
    before = await server_files(sftp_server, root)

    async with sftp_server.rotate_host_key() as new_key:
        new = fingerprint(new_key)
        with pytest.raises(HostKeyChanged):
            await sftp_storage(sftp_server, root).stat("anything.txt")

        for _ in range(2):  # a second test never re-pins
            async with tenant_session(ws.ctx) as s:
                changed = await knowledge.check_location(s, location.id, net=SELF_HOSTED)
            assert changed.status == "host_key_changed"
            assert changed.host_key_sha256 == old
            assert changed.pending_host_key_sha256 == new
        assert (
            _rows(
                db,
                "SELECT count(*) FROM audit_log WHERE action = 'storage.host_key_mismatch'",
            )[0][0]
            >= 1
        )
        assert (
            _rows(
                db,
                "SELECT count(*) FROM review_items WHERE kind = 'storage_host_key_changed'"
                " AND decided_at IS NULL AND deleted_at IS NULL",
            )[0][0]
            == 1
        )

        async with tenant_session(ws.ctx) as s:
            saved = await knowledge.save_note(s, note_id, net=SELF_HOSTED)
        assert saved.status == "queued"
        with pytest.raises(ProblemError) as refused:
            async with tenant_session(ws.ctx) as s:
                await knowledge.write_project_file(
                    s, project_id, "uploads/a.pdf", _chunks(b"%PDF"), net=SELF_HOSTED
                )
        assert refused.value.status == 409
        assert await server_files(sftp_server, root) == before

        with pytest.raises(ProblemError) as no_reason:
            async with tenant_session(ws.ctx) as s:
                await knowledge.confirm_host_key(s, location.id, new, net=SELF_HOSTED)
        assert (no_reason.value.status, no_reason.value.code) == (422, "reason_required")
        async with tenant_session(ws.ctx) as s:
            repinned = await knowledge.confirm_host_key(
                s, location.id, new, reason="server rebuilt", net=SELF_HOSTED
            )
        assert (repinned.status, repinned.host_key_sha256) == ("online", new)
        assert (
            _rows(
                db,
                "SELECT reason FROM audit_log WHERE action = 'storage.host_key_pinned'"
                " ORDER BY seq DESC LIMIT 1",
            )[0][0]
            == "server rebuilt"
        )
        async with raw_sftp(sftp_server, host_key=new_key) as sftp:
            assert await sftp.exists(root)
