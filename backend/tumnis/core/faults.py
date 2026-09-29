"""Kill points for kill-and-resume tests (P0-07, ADR-0002)."""


class KillpointRefused(RuntimeError):  # noqa: N818  # plan name
    """TUMNIS_KILLPOINT is set in a production deployment."""


def arm(deployment_env: str) -> str | None:
    """Not yet (P0-07): never arms."""
    return None


def armed() -> str | None:
    return None


def killpoint(name: str) -> None:
    """Not yet (P0-07): inert."""
