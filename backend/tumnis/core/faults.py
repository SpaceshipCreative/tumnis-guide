"""Kill points for kill-and-resume tests (P0-07, ADR-0002).

`killpoint(name)` ends the process with exit code 137 (as a SIGKILL would) when the worker
armed that name at startup from TUMNIS_KILLPOINT; otherwise it does nothing. Only
`tumnis.worker.main` arms, so the api, the CLI and in-process tests never die at a kill
point, and a production deployment refuses to start with one set.
"""

import os

KILLED_EXIT = 137
ENV_VAR = "TUMNIS_KILLPOINT"

_armed: str | None = None


class KillpointRefused(RuntimeError):  # noqa: N818  # plan name
    """TUMNIS_KILLPOINT is set in a production deployment."""


def arm(deployment_env: str) -> str | None:
    """Arm the kill point named by TUMNIS_KILLPOINT, if any, and return its name. Raises
    KillpointRefused, arming nothing, when the deployment is `prod`."""
    global _armed  # noqa: PLW0603  # process-wide: one worker process, one kill point
    name = os.environ.get(ENV_VAR) or None
    if name is not None and deployment_env == "prod":
        raise KillpointRefused(f"{ENV_VAR}={name} is set in a prod deployment; refusing to start")
    _armed = name
    return name


def armed() -> str | None:
    return _armed


def killpoint(name: str) -> None:
    if _armed is not None and _armed == name:
        os._exit(KILLED_EXIT)
