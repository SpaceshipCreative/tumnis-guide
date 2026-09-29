"""Request body limit: 413 `body_too_large` before the handler (P0-10, SEC-5).

Pure ASGI middleware. The limit is the route's `RoutePolicy.max_body_bytes` once routing has
set `request.state.policy` (TumnisRoute does before anything reads the body), else the
global default. It checks `Content-Length` when the body is first read and counts the bytes
of a chunked body as they arrive; past the limit the read raises, whatever the app answers
is dropped, and the client gets the 413 problem instead.
"""

from typing import Any, Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tumnis.core.errors import MEDIA_TYPE, problem, problem_bytes

DEFAULT_MAX_BODY_BYTES: Final = 1_048_576  # plan default 1 MiB


class BodyTooLarge(Exception):  # noqa: N818  # the condition, not an error class name
    pass


def _limit(scope: Scope, default: int) -> int:
    policy: Any = scope.get("state", {}).get("policy")
    limit = getattr(policy, "max_body_bytes", None)
    return limit if isinstance(limit, int) else default


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, default_max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.default_max_bytes = default_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})
        received = 0
        checked = False
        too_large = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, checked, too_large
            limit = _limit(scope, self.default_max_bytes)
            if not checked:
                checked = True
                declared = _content_length(scope)
                if declared is not None and declared > limit:
                    too_large = True
                    raise BodyTooLarge
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    raise BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if too_large:
                return  # the app's own answer to the failed read is replaced below
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not too_large or started:
                raise
        if too_large and not started:
            await _send_413(send)


async def _send_413(send: Send) -> None:
    body = problem_bytes(
        problem(413, "body_too_large", "The request body is larger than this route accepts")
    )
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", MEDIA_TYPE.encode()),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
