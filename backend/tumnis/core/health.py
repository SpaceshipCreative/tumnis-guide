"""Health registry and the /health/live and /health/ready routes (P0-04)."""

from collections.abc import Awaitable, Callable
from typing import Literal

Status = Literal["ok", "degraded", "down"]
HealthCheck = Callable[[], Awaitable[Status]]


def register_health(name: str, check: HealthCheck, *, critical: bool) -> None:
    raise NotImplementedError
