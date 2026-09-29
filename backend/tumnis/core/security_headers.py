"""Strict security headers on every response (P0-16, SEC-4).

A pure ASGI middleware that wraps `send` and sets HEADERS on every `http.response.start`,
replacing any value the app set. `install(app)` puts it outside everything else, the
server error middleware included, so 404s, 405s, 413s, 500s and the static shell carry the
same headers as any 200. FastAPI's `add_middleware` would put it inside Starlette's
`ServerErrorMiddleware`, and the 500 that middleware sends would go out bare.

The CSP allows only same-origin script, style, fonts and connections: no inline script, no
eval, no plugins, no framing. Styles set through the DOM (`element.style`) are not governed
by `style-src`; a library that injects `<style>` tags gets a build-time CSS extract, never
a looser policy.
"""

from collections.abc import Callable
from typing import Final

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CSP: Final = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "worker-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "upgrade-insecure-requests",
    ]
)
HEADERS: Final[tuple[tuple[bytes, bytes], ...]] = (
    (b"content-security-policy", CSP.encode()),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),  # plan default: 2 y
    (b"x-content-type-options", b"nosniff"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), geolocation=(), microphone=(self)"),
)
_NAMES: Final = frozenset(name for name, _ in HEADERS)


class SecurityHeadersMiddleware:
    """Pure ASGI: wraps `send` and appends HEADERS to every http.response.start, replacing
    duplicates. Installed outermost so 404s, 500s and StaticFiles responses get them too."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in _NAMES
                ]
                message = {**message, "headers": [*kept, *HEADERS]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def install(app: FastAPI) -> None:
    """Wrap the app's whole middleware stack (built lazily on the first request) in
    SecurityHeadersMiddleware, outside Starlette's ServerErrorMiddleware and anything that
    wraps the stack before this call (OpenTelemetry's instrumentation does)."""
    build: Callable[[], ASGIApp] = app.build_middleware_stack

    def build_with_headers() -> ASGIApp:
        return SecurityHeadersMiddleware(build())

    app.build_middleware_stack = build_with_headers  # type: ignore[method-assign]
