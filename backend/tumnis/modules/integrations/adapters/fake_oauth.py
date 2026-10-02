"""`FakeOAuthServer`: the OAuth port in memory (P3-02). Spec stub."""

from typing import Any


class FakeOAuthServer:
    authorization_endpoint: str = ""
    clients: dict[str, Any]
    calls: list[tuple[Any, ...]]
    refreshes: int = 0

    async def discover(self, server_url: str) -> Any:
        raise NotImplementedError

    async def register(self, server: Any, metadata: Any) -> Any:
        raise NotImplementedError

    async def exchange(
        self, server: Any, client: Any, *, code: str, code_verifier: str, redirect_uri: str
    ) -> Any:
        raise NotImplementedError

    async def refresh(self, server: Any, client: Any, *, refresh_token: str) -> Any:
        raise NotImplementedError

    def issue(self, client_id: str, *, expires_in: int) -> Any:
        raise NotImplementedError

    def approve(self, authorize_url: str, *, code: str) -> str:
        raise NotImplementedError

    def refresh_token_live(self, token: str) -> bool:
        raise NotImplementedError
