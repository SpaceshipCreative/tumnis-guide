"""The OpenAI-compatible HTTP client the decisions adapters share (P1-03; `VllmDecisions`
in P1-02 reuses it): chat completions over the core SSRF-guarded client, with the
provider's answers translated into the four adapter errors.

The client is `tumnis.core.net.guarded_client` (resolve, check, pin), with the endpoint's
own port allowed on top of the default web ports (vLLM serves on 8000). Callers run the
request inside `Adapter.call`, which owns the timeout ceiling, the breaker and retries.
"""

from dataclasses import replace
from typing import Any, Final

import httpx

from tumnis.core.adapters.errors import AdapterRejected, AdapterTimeout, AdapterUnavailable
from tumnis.core.net import SCHEME_PORTS, NetPolicy, Resolver, guarded_client, system_resolver

__all__ = ["ChatEndpoint", "message_text"]

CHAT_PATH: Final = "/v1/chat/completions"
_TOO_MANY: Final = 429
_REQUEST_TIMEOUT: Final = 408
_SERVER_ERROR: Final = 500
_CLIENT_ERROR: Final = 400


def _chat_url(base_url: str, path: str = CHAT_PATH) -> httpx.URL:
    """`http://host:8000`, `http://host:8000/` and `http://host:8000/v1` all name the same
    API root."""
    root = base_url.rstrip("/").removesuffix("/v1")
    return httpx.URL(root + path)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


class ChatEndpoint:
    """One OpenAI-compatible endpoint: `post(body)` sends a request (a chat completion by
    default; `path` names another route, such as P3-10's `/v1/embeddings`, and `bearer` a
    hosted provider's key) and
    returns the decoded answer, or raises AdapterTimeout, AdapterUnavailable (connection,
    408, 429, 5xx) or AdapterRejected (other 4xx, a body that is not JSON, a blocked
    destination: SsrfBlocked)."""

    def __init__(
        self,
        base_url: str,
        *,
        adapter: str,
        op: str,
        net_policy: NetPolicy,
        timeout_s: float,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,  # MockTransport in tests
        path: str = CHAT_PATH,
        bearer: str | None = None,
    ) -> None:
        self.url = _chat_url(base_url, path)
        self._adapter, self._op = adapter, op
        self._headers = {} if bearer is None else {"Authorization": f"Bearer {bearer}"}
        port = self.url.port or SCHEME_PORTS.get(self.url.scheme, 443)
        policy = replace(net_policy, ports=net_policy.ports | {port})
        self._client = guarded_client(policy, timeout=timeout_s, resolver=resolver, inner=transport)

    async def post(self, body: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        try:
            response = await self._client.post(
                self.url, json=body, timeout=timeout_s, headers=self._headers
            )
        except httpx.TimeoutException:
            raise AdapterTimeout(self._adapter, self._op) from None
        except httpx.TransportError as exc:
            raise AdapterUnavailable(
                self._adapter, self._op, f"connection failed ({type(exc).__name__})"
            ) from None
        status = response.status_code
        if status == _TOO_MANY:
            raise AdapterUnavailable(
                self._adapter, self._op, "rate limited (429)", retry_after_s=_retry_after(response)
            )
        if status >= _SERVER_ERROR or status == _REQUEST_TIMEOUT:
            raise AdapterUnavailable(self._adapter, self._op, f"server error ({status})")
        if status >= _CLIENT_ERROR:
            raise AdapterRejected(self._adapter, self._op, f"rejected ({status})")
        try:
            payload = response.json()
        except ValueError:
            raise AdapterRejected(self._adapter, self._op, "answer is not JSON") from None
        if not isinstance(payload, dict):
            raise AdapterRejected(self._adapter, self._op, "answer is not a JSON object")
        return payload

    async def aclose(self) -> None:
        await self._client.aclose()


def message_text(payload: dict[str, Any], *, adapter: str, op: str) -> str:
    """The first choice's message content; AdapterRejected when the answer has none."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AdapterRejected(adapter, op, "answer has no message content") from None
    if not isinstance(content, str):
        raise AdapterRejected(adapter, op, "answer has no message content")
    return content
