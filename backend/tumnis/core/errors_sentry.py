"""GlitchTip through the Sentry SDK (P0-27, REL-5, SEC-6). Stubs until the P0-27
implementation lands."""

from typing import Any


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    raise NotImplementedError


def init_sentry(dsn: str | None, *, environment: str, release: str | None = None) -> bool:
    raise NotImplementedError
