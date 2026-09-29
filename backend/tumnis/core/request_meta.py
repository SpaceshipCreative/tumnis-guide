"""Request metadata for the audit log (P0-15, SEC-3): source address, user agent and
correlation ID, in a context variable for the length of one request.

`RequestMetaMiddleware` (pure ASGI, so the variable reaches the endpoint and a streamed
body) sets it for every HTTP request and echoes the correlation ID as `X-Request-ID`:

- correlation ID: the caller's `X-Request-ID` when it is a plausible ID (1 to 128
  characters of letters, digits and `-_.:`), otherwise a new uuid7;
- source address: the connection's peer. Behind the configured proxy (Coolify's Traefik)
  uvicorn's `--proxy-headers` with `FORWARDED_ALLOW_IPS` has already replaced it with the
  first `X-Forwarded-For` hop, and never does for any other peer; this middleware does not
  read forwarding headers itself;
- user agent: the header, cut to 512 characters.

Workflow steps and the CLI run outside a request: every field is None there, and the
actor comes from the transaction's workspace context (`system` for workflow steps).
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tumnis.core.ids import uuid7

REQUEST_ID_HEADER: Final = "x-request-id"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
MAX_USER_AGENT: Final = 512


@dataclass(frozen=True)
class RequestMeta:
    correlation_id: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None


_NONE: Final = RequestMeta()
_meta: ContextVar[RequestMeta | None] = ContextVar("tumnis_request_meta", default=None)


def current() -> RequestMeta:
    """This request's metadata; all fields None outside a request."""
    return _meta.get() or _NONE


@contextmanager
def use(meta: RequestMeta) -> Iterator[None]:
    token = _meta.set(meta)
    try:
        yield
    finally:
        _meta.reset(token)


def from_scope(scope: Scope) -> RequestMeta:
    headers = {key.decode("latin-1").lower(): value for key, value in scope.get("headers", [])}
    given = headers.get(REQUEST_ID_HEADER, b"").decode("latin-1")
    correlation_id = given if _REQUEST_ID.match(given) else str(uuid7())
    client = scope.get("client")
    agent = headers.get("user-agent")
    return RequestMeta(
        correlation_id=correlation_id,
        source_ip=client[0] if client else None,
        user_agent=agent.decode("latin-1")[:MAX_USER_AGENT] if agent is not None else None,
    )


class RequestMetaMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        meta = from_scope(scope)
        header = (REQUEST_ID_HEADER.encode(), (meta.correlation_id or "").encode())

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), header]
            await send(message)

        with use(meta):
            await self.app(scope, receive, send_with_id)
