"""`GitHubStatusApi`: the read-only GitHub REST client (P2-13, FR-12.1) over the core
SSRF-guarded HTTP client (`tumnis.core.net.guarded_client`).

- Only `https://api.github.com` (v1), only GET (`_get` is the one place a request is made),
  with the workspace's own read-only fine-grained token, `Accept:
  application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28` and `If-None-Match` when
  the caller holds an ETag. Lists ask for `per_page=100`; a resource with more than 100
  entries is read as its first 100.
- 304 is `Fetched(None, etag)`. 429, 5xx and 403 with `x-ratelimit-remaining: 0` are
  `AdapterUnavailable` (`retry_after_s` from `Retry-After`); any other non-2xx is
  `AdapterRejected`. Neither the token nor GitHub's message reaches an error.

It runs inside DBOS steps, so it makes one attempt per call (`RetryPolicy(max_attempts=1)`)
and lets the workflow retry.
"""

from collections.abc import Callable, Mapping
from typing import Any, Final
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError

from tumnis.core.adapters.base import Adapter, AdapterRejected, AdapterUnavailable, CallPolicy
from tumnis.core.adapters.registry import Health
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.github.adapters.port import Fetched
from tumnis.modules.github.rules import CheckRunView, PullView, ReviewView, StatusView

API_BASE: Final = "https://api.github.com"
API_VERSION: Final = "2022-11-28"
TIMEOUT_S: Final = 15.0  # plan default for one GitHub call
PER_PAGE: Final = "100"
_OK: Final = 200
_NOT_MODIFIED: Final = 304
_FORBIDDEN: Final = 403
_TOO_MANY: Final = 429
_SERVER_ERROR: Final = 500
_REVIEWS: Final = TypeAdapter(list[ReviewView])


class GitHubStatusApi(Adapter):
    name = "github.status"

    def __init__(
        self,
        *,
        token: str,
        policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        """`policy` defaults to the hosted policy (public addresses only), which
        api.github.com meets; `transport` replaces the network (the recorded replay)."""
        super().__init__(
            policy=CallPolicy(timeout_s=TIMEOUT_S, retry=RetryPolicy(max_attempts=1)),
            clock=clock or SystemClock(),
        )
        self._token = token
        self._net = policy or NetPolicy(mode="hosted")
        self._resolver = resolver
        self._transport = transport

    async def get_pull(
        self, owner: str, repo: str, number: int, etag: str | None
    ) -> Fetched[PullView]:
        return await self._read(
            "get_pull", f"{_repo(owner, repo)}/pulls/{number}", None, etag, PullView.model_validate
        )

    async def get_combined_status(
        self, owner: str, repo: str, ref: str, etag: str | None
    ) -> Fetched[list[StatusView]]:
        def parse(body: Any) -> list[StatusView]:
            return [StatusView.model_validate(item) for item in body["statuses"]]

        path = f"{_repo(owner, repo)}/commits/{quote(ref, safe='')}/status"
        return await self._read("get_combined_status", path, {"per_page": PER_PAGE}, etag, parse)

    async def list_check_runs(
        self, owner: str, repo: str, ref: str, etag: str | None
    ) -> Fetched[list[CheckRunView]]:
        def parse(body: Any) -> list[CheckRunView]:
            return [CheckRunView.model_validate(item) for item in body["check_runs"]]

        path = f"{_repo(owner, repo)}/commits/{quote(ref, safe='')}/check-runs"
        return await self._read("list_check_runs", path, {"per_page": PER_PAGE}, etag, parse)

    async def list_reviews(
        self, owner: str, repo: str, number: int, etag: str | None
    ) -> Fetched[list[ReviewView]]:
        path = f"{_repo(owner, repo)}/pulls/{number}/reviews"
        return await self._read(
            "list_reviews", path, {"per_page": PER_PAGE}, etag, _REVIEWS.validate_python
        )

    async def health(self) -> Health:
        """Degraded while the circuit breaker is open; no request is made."""
        return self.health_state()

    # --- HTTP -------------------------------------------------------------------------------

    async def _read[T](
        self,
        op: str,
        path: str,
        params: Mapping[str, str] | None,
        etag: str | None,
        parse: Callable[[Any], T],
    ) -> Fetched[T]:
        async def fetch() -> Fetched[T]:
            response = await self._get(op, path, params, etag)
            if response.status_code == _NOT_MODIFIED:
                return Fetched(None, response.headers.get("etag") or etag)
            try:
                return Fetched(parse(response.json()), response.headers.get("etag"))
            except (ValueError, KeyError, TypeError, ValidationError):
                raise AdapterRejected(self.name, op, "unexpected answer") from None

        return await self.call(op, fetch, idempotent=True)

    async def _get(
        self, op: str, path: str, params: Mapping[str, str] | None, etag: str | None
    ) -> httpx.Response:
        """The only request this client makes: a GET under api.github.com."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "tumnis-guide",
        }
        if etag is not None:
            headers["If-None-Match"] = etag
        async with guarded_client(
            self._net, timeout=TIMEOUT_S, resolver=self._resolver, inner=self._transport
        ) as http:
            try:
                response = await http.get(f"{API_BASE}{path}", params=params, headers=headers)
            except httpx.TransportError as exc:
                raise AdapterUnavailable(self.name, op, type(exc).__name__) from None
        self._check(op, response)
        return response

    def _check(self, op: str, response: httpx.Response) -> None:
        status = response.status_code
        if status in {_OK, _NOT_MODIFIED}:
            return
        limited = (
            status == _TOO_MANY
            or status >= _SERVER_ERROR
            or (status == _FORBIDDEN and response.headers.get("x-ratelimit-remaining") == "0")
        )
        if limited:
            raise AdapterUnavailable(
                self.name, op, str(status), retry_after_s=_retry_after(response)
            )
        raise AdapterRejected(self.name, op, str(status))


def _repo(owner: str, repo: str) -> str:
    return f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    return float(value) if value is not None and value.isdigit() else None
