"""Storage locations (P1-14, FR-15.7, FR-15.12, SEC-5): the marker takes a share offline
instead of filling the server's disk, offline note writes queue and land once, a project
folder follows the workspace default until it holds a file, S3 endpoints pass the SSRF
guard, and credentials are sealed."""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.core.net import NetPolicy

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from dbos import DBOS, WorkflowHandleAsync

    from tests._pg import DbUrls
    from tests._services import S3Endpoint
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SELF_HOSTED = NetPolicy(mode="self-hosted")
HOSTED = NetPolicy(mode="hosted")
MARKER = ".tumnis-root"
FOLDER_SUBSCRIBER = "knowledge.assign_project_folder"


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _files(root: Path) -> list[str]:
    """Every regular file under `root`, relative, the marker left out."""
    found = []
    for folder, _dirs, names in os.walk(root):
        for name in names:
            rel = os.path.relpath(os.path.join(folder, name), root)
            if rel != MARKER:
                found.append(rel)
    return sorted(found)


def _is_empty(folder: Path) -> bool:
    return not any(folder.iterdir())


def _make_root(root: Path) -> Path:
    root.mkdir()
    (root / MARKER).write_text("tumnis\n")
    return root


def _count(db: DbUrls, sql: str, *params: object) -> int:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row[0])


async def _project(ws: WorkspaceHandle, clock: FixedClock, name: str = "Acme") -> UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    async with tenant_session(ws.ctx) as s:
        project = await projects.create_project(
            s, ws.ctx.actor, projects.ProjectCreate(name=name), now=clock.now()
        )
    return project.id


async def _server_path_location(
    ws: WorkspaceHandle, root: Path, name: str, *, default: bool = True
) -> UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(ws.ctx) as s:
        loc = await knowledge.create_location(
            s,
            knowledge.LocationIn(name=name, kind="server_path", root=str(root), is_default=default),
            net=SELF_HOSTED,
        )
    assert loc.status == "online"
    return loc.id


def _lan_ip() -> str:
    """This host's own non-loopback address (a UDP connect sends nothing). The MinIO
    container publishes its port on every interface, so it answers there too."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere
        address: str = probe.getsockname()[0]
    if address.startswith("127."):
        pytest.skip("no non-loopback address to reach the MinIO container on")
    return address


def _lan_endpoint(minio: S3Endpoint) -> str:
    return f"http://{_lan_ip()}:{urlsplit(minio.url).port}"


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-14")
async def test_missing_marker_takes_location_offline_and_queues_writes(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_location: Path
) -> None:
    """T-P1-14-08
    Remove the marker: health is degraded and the location goes `offline`
    (`marker_missing`); a note save queues one `pending_writes` row and writes no file
    under the root. Restore the marker: the location comes back online, the queued write
    lands once, and a second check writes nothing more.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage  # noqa: PLC0415

    ws = knowledge_ws
    location_id = await _server_path_location(ws, tmp_location, "disk")
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        await knowledge.assign_project_folder(s, project_id)
        note_id = await knowledge.put_text_document(
            s, project_id, title="Plan", body_md="# Plan\n", role=None
        )

    (tmp_location / MARKER).unlink()
    assert (await ServerPathStorage(tmp_location).health()).status == "degraded"
    async with tenant_session(ws.ctx) as s:
        offline = await knowledge.check_location(s, location_id, net=SELF_HOSTED)
    assert (offline.status, offline.status_reason) == ("offline", "marker_missing")

    async with tenant_session(ws.ctx) as s:
        queued = await knowledge.save_note(s, note_id, net=SELF_HOSTED)
    assert queued.status == "queued"
    assert _count(db, "SELECT count(*) FROM pending_writes") == 1
    assert _files(tmp_location) == []

    (tmp_location / MARKER).write_text("tumnis\n")
    async with tenant_session(ws.ctx) as s:
        online = await knowledge.check_location(s, location_id, net=SELF_HOSTED)
    assert (online.status, online.status_reason) == ("online", None)
    assert _count(db, "SELECT count(*) FROM pending_writes") == 0
    assert _files(tmp_location) == [queued.path]
    landed = tmp_location / queued.path
    assert landed.read_bytes() == b"# Plan\n"
    mtime = landed.stat().st_mtime_ns

    async with tenant_session(ws.ctx) as s:
        await knowledge.check_location(s, location_id, net=SELF_HOSTED)
    assert _files(tmp_location) == [queued.path]
    assert landed.stat().st_mtime_ns == mtime


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-14")
async def test_upload_to_offline_location_refused(
    db: DbUrls, knowledge_ws: WorkspaceHandle, clock: FixedClock, tmp_location: Path
) -> None:
    """T-P1-14-09
    A file written to a project whose location is offline is refused with 409
    `location_offline`, whether the location was already marked offline or its marker
    vanished since the last check, and nothing reaches the disk.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws
    location_id = await _server_path_location(ws, tmp_location, "disk")
    project_id = await _project(ws, clock)
    async with tenant_session(ws.ctx) as s:
        await knowledge.assign_project_folder(s, project_id)

    (tmp_location / MARKER).unlink()
    # Not yet checked: the write checks the marker itself.
    with pytest.raises(ProblemError) as unchecked:
        async with tenant_session(ws.ctx) as s:
            await knowledge.write_project_file(
                s, project_id, "uploads/a.pdf", _chunks(b"%PDF"), net=SELF_HOSTED
            )
    assert (unchecked.value.status, unchecked.value.code) == (409, "location_offline")

    async with tenant_session(ws.ctx) as s:
        await knowledge.check_location(s, location_id, net=SELF_HOSTED)
    with pytest.raises(ProblemError) as marked:
        async with tenant_session(ws.ctx) as s:
            await knowledge.write_project_file(
                s, project_id, "uploads/b.pdf", _chunks(b"%PDF"), net=SELF_HOSTED
            )
    assert (marked.value.status, marked.value.code) == (409, "location_offline")
    assert _files(tmp_location) == []
    assert _is_empty(tmp_location)


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
async def test_default_and_project_override(
    db: DbUrls,
    dbos: type[DBOS],
    knowledge_ws: WorkspaceHandle,
    clock: FixedClock,
    tmp_path: Path,
) -> None:
    """T-P1-14-12
    A new project gets its folder on the workspace default location (the
    `project.created` subscriber); moving it to another location succeeds while the
    folder is empty, and is refused with 409 `folder_not_empty` once a file exists.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.events import delivery_id, relay_once  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws
    roots = {name: _make_root(tmp_path / name) for name in ("first", "second")}
    first = await _server_path_location(ws, roots["first"], "first")
    second = await _server_path_location(ws, roots["second"], "second", default=False)

    project_id = await _project(ws, clock)
    assert await relay_once() >= 1
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute("SELECT event_id FROM outbox WHERE name = 'project.created'").fetchone()
    assert row is not None
    delivery: WorkflowHandleAsync[str] = await dbos.retrieve_workflow_async(
        delivery_id(row[0], FOLDER_SUBSCRIBER)
    )
    assert await delivery.get_result() == "delivered"

    async with tenant_session(ws.ctx) as s:
        folder = await knowledge.get_project_folder(s, project_id)
    assert (folder.location_id, folder.mode) == (first, "tumnis_made")

    async with tenant_session(ws.ctx) as s:
        moved = await knowledge.set_project_location(s, project_id, second, net=SELF_HOSTED)
    assert moved.location_id == second

    async with tenant_session(ws.ctx) as s:
        written = await knowledge.write_project_file(
            s, project_id, "notes/a.md", _chunks(b"a"), net=SELF_HOSTED
        )
    assert written.size == 1
    assert len(_files(roots["second"])) == 1
    assert _files(roots["first"]) == []

    with pytest.raises(ProblemError) as refused:
        async with tenant_session(ws.ctx) as s:
            await knowledge.set_project_location(s, project_id, first, net=SELF_HOSTED)
    assert (refused.value.status, refused.value.code) == (409, "folder_not_empty")
    async with tenant_session(ws.ctx) as s:
        assert (await knowledge.get_project_folder(s, project_id)).location_id == second


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
async def test_s3_endpoint_passes_ssrf_guard(
    db: DbUrls, knowledge_ws: WorkspaceHandle, minio: S3Endpoint
) -> None:
    """T-P1-14-13
    Hosted mode refuses a private-range endpoint and 169.254.169.254 (422
    `ssrf_blocked`, nothing saved); self-hosted mode refuses the metadata address too, and
    accepts the MinIO container on this host's LAN address, which comes up online.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.tests.contract.test_storage_s3 import (  # noqa: PLC0415
        make_bucket,
    )

    ws = knowledge_ws

    def s3_location(endpoint: str, root: str = "bucket/tumnis") -> knowledge.LocationIn:
        return knowledge.LocationIn(
            name=f"s3 {endpoint}",
            kind="s3",
            root=root,
            s3=knowledge.S3ConfigIn(
                endpoint=endpoint,
                region=minio.region,
                access_key=minio.access_key,
                secret_key=minio.secret_key,
            ),
        )

    refused = [
        (HOSTED, "http://10.20.30.40:9000"),
        (HOSTED, "https://192.168.1.10"),
        (HOSTED, "http://169.254.169.254"),
        (SELF_HOSTED, "http://169.254.169.254"),
        (SELF_HOSTED, "http://localhost:9000"),
    ]
    for policy, endpoint in refused:
        with pytest.raises(ProblemError) as e:
            async with tenant_session(ws.ctx) as s:
                await knowledge.create_location(s, s3_location(endpoint), net=policy)
        assert (e.value.status, e.value.code) == (422, "ssrf_blocked"), endpoint
    assert _count(db, "SELECT count(*) FROM storage_locations") == 0

    bucket = await make_bucket(minio)
    async with tenant_session(ws.ctx) as s:
        loc = await knowledge.create_location(
            s, s3_location(_lan_endpoint(minio), f"{bucket}/tumnis"), net=SELF_HOSTED
        )
    assert loc.status == "online"
    assert loc.capabilities["conditional_put"] is True


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
async def test_location_credentials_encrypted(
    db: DbUrls, knowledge_ws: WorkspaceHandle, minio: S3Endpoint
) -> None:
    """T-P1-14-14
    The raw `config_enc` of an S3 location holds neither the access key nor the secret
    (sealed with the workspace data key), and no read of the location returns the secret.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.tests.contract.test_storage_s3 import (  # noqa: PLC0415
        make_bucket,
    )

    ws = knowledge_ws
    bucket = await make_bucket(minio)
    body = knowledge.LocationIn(
        name="minio",
        kind="s3",
        root=f"{bucket}/tumnis",
        s3=knowledge.S3ConfigIn(
            endpoint=_lan_endpoint(minio),
            region=minio.region,
            access_key=minio.access_key,
            secret_key=minio.secret_key,
        ),
    )
    async with tenant_session(ws.ctx) as s:
        created = await knowledge.create_location(s, body, net=SELF_HOSTED)
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(
            "SELECT config_enc FROM storage_locations WHERE id = %s", (created.id,)
        ).fetchone()
    assert row is not None
    assert row[0]
    raw = bytes(row[0])
    for secret in (minio.access_key, minio.secret_key):
        assert secret.encode() not in raw
        assert secret.encode().hex().encode() not in raw

    async with tenant_session(ws.ctx) as s:
        listed = await knowledge.list_locations(s)
    dumped = "".join(loc.model_dump_json() for loc in listed) + created.model_dump_json()
    assert minio.secret_key not in dumped
    assert created.id in {loc.id for loc in listed}
    # The sealed config still opens: the location works.
    async with tenant_session(ws.ctx) as s:
        checked = await knowledge.check_location(s, created.id, net=SELF_HOSTED)
    assert checked.status == "online"


@pytest.mark.req("FR-15.7", "SEC-5")
@pytest.mark.wp("P1-14")
async def test_pr52_hosted_mode_refuses_server_path_locations(
    db: DbUrls, knowledge_ws: WorkspaceHandle, tmp_location: Path
) -> None:
    """Hosted mode offers S3 and SFTP only (FR-15.7): saving a server path is refused (422
    `invalid_location`, nothing saved, nothing written under the root), and a server path
    saved in self-hosted mode is not opened when the deployment runs hosted, so no
    workspace can point a location at another workspace's folder on the hosted server."""
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws
    body = knowledge.LocationIn(name="disk", kind="server_path", root=str(tmp_location))
    with pytest.raises(ProblemError) as e:
        async with tenant_session(ws.ctx) as s:
            await knowledge.create_location(s, body, net=HOSTED)
    assert (e.value.status, e.value.code) == (422, "invalid_location")
    assert _count(db, "SELECT count(*) FROM storage_locations") == 0

    location_id = await _server_path_location(ws, tmp_location, "disk")
    with pytest.raises(ProblemError) as e:
        async with tenant_session(ws.ctx) as s:
            await knowledge.check_location(s, location_id, net=HOSTED)
    assert (e.value.status, e.value.code) == (422, "invalid_location")
    assert _files(tmp_location) == []


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
async def test_pr52_hosted_mode_refuses_plain_http_s3_endpoints(
    db: DbUrls, knowledge_ws: WorkspaceHandle, fakes: Fakes
) -> None:
    """Hosted mode refuses an `http://` S3 endpoint even on a public address (422
    `invalid_location`, nothing saved): keys, SigV4 requests and file bodies never cross the
    internet in clear. Self-hosted mode keeps http for a LAN MinIO, and hosted takes https."""
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.net import ScriptedResolver  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws

    def s3_location(name: str, endpoint: str) -> knowledge.LocationIn:
        return knowledge.LocationIn(
            name=name,
            kind="s3",
            root="bucket/tumnis",
            s3=knowledge.S3ConfigIn(
                endpoint=endpoint, region="us-east-1", access_key="AKIA", secret_key="secret"
            ),
        )

    public = ScriptedResolver([["93.184.216.34"]])
    with pytest.raises(ProblemError) as e:
        async with tenant_session(ws.ctx) as s:
            await knowledge.create_location(
                s, s3_location("clear", "http://s3.example.com"), net=HOSTED, resolver=public
            )
    assert (e.value.status, e.value.code) == (422, "invalid_location")
    assert _count(db, "SELECT count(*) FROM storage_locations") == 0

    async with tenant_session(ws.ctx) as s:
        await knowledge.create_location(
            s, s3_location("tls", "https://s3.example.com"), net=HOSTED, resolver=public
        )
        lan = ScriptedResolver([["192.168.1.10"]])
        await knowledge.create_location(
            s, s3_location("lan", "http://minio.lan:9000"), net=SELF_HOSTED, resolver=lan
        )
    assert _count(db, "SELECT count(*) FROM storage_locations") == 2
