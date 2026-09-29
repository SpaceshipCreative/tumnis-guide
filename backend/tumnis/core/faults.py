"""Kill points for kill-and-resume tests (P0-07, ADR-0002).

`killpoint(name)` ends the process with exit code 137 (as a SIGKILL would) when the worker
armed that name at startup from TUMNIS_KILLPOINT; otherwise it does nothing. Only
`tumnis.worker.main` arms, so the api, the CLI and in-process tests never die at a kill
point, and a production deployment refuses to start with one set.
"""

import os

KILLED_EXIT = 137

_armed: str | None = None


class KillpointRefused(RuntimeError):  # noqa: N818  # plan name
    """TUMNIS_KILLPOINT is set in a production deployment."""


def arm(deployment_env: str) -> str | None:
    """Not yet (P0-07): never arms."""
    return None


def armed() -> str | None:
    return _armed


def killpoint(name: str) -> None:
    if _armed is not None and _armed == name:
        os._exit(KILLED_EXIT)
