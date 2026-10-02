"""Helpers for the SFTP tests (P3-14): a fresh root per test on the session's `sftp_server`
container, the adapter built on it, and a raw client for looking behind Tumnis's back. No
assertions live here.

The container's user is chrooted with an `upload/` folder it owns; every test works in its
own `upload/t-<hex>` root, so tests sharing the container never see each other's files.
Keys are the fixture's, made at test time (`tests._services.make_ed25519_keypair`).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests._services import SftpEndpoint


def private_key_pem(endpoint: SftpEndpoint) -> bytes:
    return endpoint.private_key_path.read_bytes()


def fingerprint(openssh_key: str) -> str:
    """'SHA256:…' of a public key as known_hosts has it, the form `ssh-keygen -lf` shows."""
    import asyncssh  # noqa: PLC0415

    return str(asyncssh.import_public_key(openssh_key).get_fingerprint("sha256"))


@asynccontextmanager
async def raw_sftp(endpoint: SftpEndpoint, host_key: str | None = None) -> AsyncIterator[Any]:
    """An asyncssh SFTP client as the container's user, trusting only `host_key` (the
    fixture's pinned key by default)."""
    import asyncssh  # noqa: PLC0415

    trusted = asyncssh.import_public_key(host_key or endpoint.host_key)
    async with (
        asyncssh.connect(
            endpoint.host,
            endpoint.port,
            username=endpoint.user,
            client_keys=[asyncssh.import_private_key(private_key_pem(endpoint))],
            known_hosts=([trusted], [], []),
            agent_path=None,
            config=None,
        ) as conn,
        conn.start_sftp_client() as sftp,
    ):
        yield sftp


async def make_root(endpoint: SftpEndpoint) -> str:
    """A new empty folder under `upload/` for one test; its path as the user sees it."""
    root = f"upload/t-{uuid.uuid4().hex[:12]}"
    async with raw_sftp(endpoint) as sftp:
        await sftp.mkdir(root)
    return root


def sftp_storage(endpoint: SftpEndpoint, root: str, **kwargs: Any) -> Any:
    """`SftpStorage` on the container, pinned to the fixture's host key; `kwargs` go to
    the constructor (a net policy and resolver, another pinned key)."""
    from tumnis.modules.knowledge.adapters.sftp import SftpStorage  # noqa: PLC0415

    options: dict[str, Any] = {
        "host": endpoint.host,
        "port": endpoint.port,
        "username": endpoint.user,
        "private_key_pem": private_key_pem(endpoint),
        "pinned_host_key": endpoint.host_key,
        "root": root,
    }
    return SftpStorage(**(options | kwargs))


async def server_files(endpoint: SftpEndpoint, root: str) -> list[str]:
    """Every regular file under `root` as the server holds it (found as root inside the
    container, so a changed host key does not hide anything), relative to `root`."""
    import asyncio  # noqa: PLC0415

    base = endpoint.server_path(root)
    out = await asyncio.to_thread(
        endpoint.exec, f"cd {base} 2>/dev/null && find . -type f | sort || true"
    )
    return [line.removeprefix("./") for line in out.splitlines() if line.strip()]
