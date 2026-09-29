"""ETags and 304s for /v1 reads (P0-22, PERF-2).

A pure ASGI middleware. For a GET or HEAD under /v1 that answers 200 with a JSON body, it
buffers the body, tags it with a strong ETag (a hash of the bytes, so it covers the row
`version` and whole lists alike) and adds `Cache-Control: private, no-cache`, so the
browser always revalidates. A request whose `If-None-Match` matches (weak comparison, a
list of tags, or `*`) gets 304 with the tag and no body. The body is still built; the
saving is bytes on the phone link. Anything else passes through untouched.
"""

import hashlib
from http import HTTPStatus
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PREFIX: Final = "/v1/"
CACHE_CONTROL: Final = "private, no-cache"


def strong_etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def _opaque(tag: str) -> str:
    tag = tag.strip()
    return tag[2:] if tag.startswith("W/") else tag


def matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 weak comparison of `If-None-Match` against one tag."""
    candidates = [part.strip() for part in if_none_match.split(",")]
    return "*" in candidates or _opaque(etag) in {_opaque(c) for c in candidates if c}


class ETagMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in ("GET", "HEAD")
            or not scope["path"].startswith(PREFIX)
        ):
            await self.app(scope, receive, send)
            return

        if_none_match = Headers(scope=scope).get("if-none-match")
        start: Message | None = None
        chunks: list[bytes] = []

        async def tagged_send(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                kind = Headers(raw=message.get("headers", [])).get("content-type", "")
                if message["status"] == HTTPStatus.OK and kind.startswith("application/json"):
                    start = message  # hold it until the whole body is known
                    return
                await send(message)
                return
            if start is None or message["type"] != "http.response.body":
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            body = b"".join(chunks)
            etag = strong_etag(body)
            headers = MutableHeaders(raw=list(start.get("headers", [])))
            headers["ETag"] = etag
            headers.setdefault("Cache-Control", CACHE_CONTROL)
            if if_none_match is not None and matches(if_none_match, etag):
                for name in ("content-length", "content-type"):
                    del headers[name]
                await send({**start, "status": 304, "headers": headers.raw})
                await send({"type": "http.response.body", "body": b""})
                return
            await send({**start, "headers": headers.raw})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, tagged_send)
