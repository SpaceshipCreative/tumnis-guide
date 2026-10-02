"""Locations for P3-14's integration tests: an SFTP location on the session's container
(created, probed and pinned through `knowledge.api`, as the Settings screen does it) and a
share on a temp dir. No assertions about the product live here.

The SFTP container is reached on this host's LAN address, as a self-hosted Tumnis would
reach a NAS: the SSRF guard always refuses loopback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tumnis.core.net import NetPolicy
from tumnis.modules.knowledge.tests._sftp import fingerprint, make_root, private_key_pem

if TYPE_CHECKING:
    from pathlib import Path

    from tests._services import SftpEndpoint
    from tests.fixtures import WorkspaceHandle

SELF_HOSTED = NetPolicy(mode="self-hosted")
MARKER = ".tumnis-root"


def lan_host() -> str:
    from tumnis.modules.knowledge.tests.integration.test_locations import (  # noqa: PLC0415
        _lan_ip,
    )

    return _lan_ip()


def sftp_location_in(
    endpoint: SftpEndpoint, root: str, *, name: str = "nas", default: bool = False
) -> Any:
    """The body the Settings form sends for an SFTP location on the container."""
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    return knowledge.LocationIn(
        name=name,
        kind="sftp",
        root=root,
        is_default=default,
        sftp=knowledge.SftpConfigIn(
            host=lan_host(),
            port=endpoint.port,
            username=endpoint.user,
            private_key=private_key_pem(endpoint).decode(),
        ),
    )


async def pinned_sftp_location(
    ws: WorkspaceHandle,
    endpoint: SftpEndpoint,
    *,
    name: str = "nas",
    default: bool = False,
    root: str | None = None,
) -> tuple[Any, str]:
    """(the location, online with the container's host key pinned, its root)."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    root = root or await make_root(endpoint)
    async with tenant_session(ws.ctx) as s:
        pending = await knowledge.create_location(
            s, sftp_location_in(endpoint, root, name=name, default=default), net=SELF_HOSTED
        )
    async with tenant_session(ws.ctx) as s:
        pinned = await knowledge.confirm_host_key(
            s, pending.id, fingerprint(endpoint.host_key), net=SELF_HOSTED
        )
    return pinned, root


async def share_location(
    ws: WorkspaceHandle, root: Path, *, name: str = "share", default: bool = True
) -> Any:
    """A share (`kind="share"`) on `root`, which must hold the marker."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    async with tenant_session(ws.ctx) as s:
        return await knowledge.create_location(
            s,
            knowledge.LocationIn(name=name, kind="share", root=str(root), is_default=default),
            net=SELF_HOSTED,
        )
