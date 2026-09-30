"""SFTP locations log in with a key only (P3-14, FR-15.7): against a server that offers
password logins only, the connection fails without Tumnis ever sending a password (or
answering a keyboard-interactive prompt, or asking an ssh-agent)."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P3-14")
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
async def test_password_auth_never_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P3-14-11
    An in-process SSH server on loopback (throwaway host and client keys made now) accepts
    only passwords and keyboard-interactive logins and records every attempt. The SFTP
    adapter, pinned to that server's host key, fails with `AdapterRejected`; the server
    saw no password and no keyboard-interactive answer.
    """
    import asyncssh  # noqa: PLC0415

    from tumnis.core.adapters.errors import AdapterRejected  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.sftp import SftpStorage  # noqa: PLC0415

    monkeypatch.setenv("SSH_AUTH_SOCK", "/nonexistent/agent.sock")  # never consulted
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    seen: dict[str, list[Any]] = {"password": [], "kbdint": [], "public_key": []}

    class PasswordOnly(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

        def public_key_auth_supported(self) -> bool:
            return False

        def password_auth_supported(self) -> bool:
            return True

        def kbdint_auth_supported(self) -> bool:
            return True

        def validate_password(self, username: str, password: str) -> bool:
            seen["password"].append(password)
            return False

        def get_kbdint_challenge(self, username: str, lang: str, submethods: str) -> Any:
            return "", "", "", [("Password: ", False)]

        def validate_kbdint_response(self, username: str, responses: Any) -> bool:
            seen["kbdint"].append(responses)
            return False

        def validate_public_key(self, username: str, key: Any) -> bool:
            seen["public_key"].append(key)
            return False

    server = await asyncssh.create_server(PasswordOnly, "127.0.0.1", 0, server_host_keys=[host_key])
    port = server.sockets[0].getsockname()[1]
    storage = SftpStorage(
        host="127.0.0.1",
        port=port,
        username="tumnis",
        private_key_pem=client_key.export_private_key(),
        pinned_host_key=host_key.export_public_key().decode().strip(),
        root="data",
    )
    try:
        with pytest.raises(AdapterRejected):
            await storage.stat("a.txt")
    finally:
        await storage.aclose()
        server.close()
        await server.wait_closed()
    assert seen["password"] == []
    assert seen["kbdint"] == []
