"""SFTP hosts pass the SSRF guard (P3-14, SEC-5): link-local and cloud-metadata addresses
are refused when a location is probed and whenever it connects, and nothing is sent."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.core.net import NetPolicy, ScriptedResolver
from tumnis.modules.knowledge.tests._sftp import fingerprint, make_root, private_key_pem
from tumnis.modules.knowledge.tests.integration._locations import SELF_HOSTED, lan_host
from tumnis.modules.knowledge.tests.integration.test_locations import _count

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests._services import SftpEndpoint
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

HOSTED = NetPolicy(mode="hosted")
BLOCKED = ("169.254.169.254", "169.254.10.1", "fd00:ec2::254", "100.100.100.200")


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P3-14")
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
async def test_sftp_host_through_ssrf_guard(
    db: DbUrls, knowledge_ws: WorkspaceHandle, sftp_server: SftpEndpoint
) -> None:
    """T-P3-14-18
    Saving an SFTP location whose host is (or resolves to) a link-local or metadata
    address is refused with 422 `ssrf_blocked` and saves nothing; so is a private host in
    hosted mode. The probe and the adapter refuse such a host before connecting. A pinned
    location whose name later resolves to the metadata address is refused on its next
    connection test.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.net import SsrfBlocked  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.sftp import SftpStorage, probe_host_key  # noqa: PLC0415

    ws = knowledge_ws
    key = private_key_pem(sftp_server).decode()

    def body(host: str, name: str) -> knowledge.LocationIn:
        return knowledge.LocationIn(
            name=name,
            kind="sftp",
            root="upload",
            sftp=knowledge.SftpConfigIn(
                host=host, port=22, username=sftp_server.user, private_key=key
            ),
        )

    cases: list[tuple[str, ScriptedResolver, NetPolicy]] = [
        (address, ScriptedResolver([[address]]), SELF_HOSTED) for address in BLOCKED
    ]
    cases.append(("metadata.example.org", ScriptedResolver([["169.254.169.254"]]), SELF_HOSTED))
    cases.append(("nas.example.org", ScriptedResolver([["10.0.0.5"]]), HOSTED))
    for n, (host, resolver, policy) in enumerate(cases):
        with pytest.raises(ProblemError) as refused:
            async with tenant_session(ws.ctx) as s:
                await knowledge.create_location(
                    s, body(host, f"nas-{n}"), net=policy, resolver=resolver
                )
        assert (refused.value.status, refused.value.code) == (422, "ssrf_blocked"), host
        with pytest.raises(SsrfBlocked):
            await probe_host_key(host, 22, net_policy=policy, resolver=resolver)
        storage = SftpStorage(
            host=host,
            port=22,
            username=sftp_server.user,
            private_key_pem=key.encode(),
            pinned_host_key=sftp_server.host_key,
            root="upload",
            net_policy=policy,
            resolver=resolver,
        )
        try:
            with pytest.raises(SsrfBlocked):
                await storage.stat("a.txt")
        finally:
            await storage.aclose()
    assert _count(db, "SELECT count(*) FROM storage_locations") == 0

    root = await make_root(sftp_server)
    lan = ScriptedResolver([[lan_host()]])
    named = knowledge.LocationIn(
        name="nas",
        kind="sftp",
        root=root,
        sftp=knowledge.SftpConfigIn(
            host="nas.example.org", port=sftp_server.port, username=sftp_server.user,
            private_key=key,
        ),
    )  # fmt: skip
    async with tenant_session(ws.ctx) as s:
        pending = await knowledge.create_location(s, named, net=SELF_HOSTED, resolver=lan)
    async with tenant_session(ws.ctx) as s:
        pinned = await knowledge.confirm_host_key(
            s, pending.id, fingerprint(sftp_server.host_key), net=SELF_HOSTED, resolver=lan
        )
    assert pinned.status == "online"
    moved = ScriptedResolver([["169.254.169.254"]])
    with pytest.raises(ProblemError) as later:
        async with tenant_session(ws.ctx) as s:
            await knowledge.check_location(s, pending.id, net=SELF_HOSTED, resolver=moved)
    assert (later.value.status, later.value.code) == (422, "ssrf_blocked")
