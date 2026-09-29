"""structlog JSON logging with trace IDs and redaction (P0-16 redaction, P0-27 trace IDs
and the stdlib bridge). Stubs until the P0-27 implementation lands."""

from collections.abc import MutableMapping
from typing import IO, Any

import structlog


def add_trace_ids(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    raise NotImplementedError


def redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    raise NotImplementedError


SHARED: list[structlog.typing.Processor] = []


def configure_logging(*, stream: IO[str] | None = None, level: str = "INFO") -> None:
    raise NotImplementedError
