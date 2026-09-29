"""The daemon's entry point (P1-04). Stub: implemented after the spec tests."""

import os

from websockets.asyncio.client import connect

from tumnis_daemon.config import DaemonConfig

__all__ = ["EX_CONFIG", "connect", "main", "os", "refuse_root"]

EX_CONFIG = 78  # sysexits: configuration error (running as root)


def refuse_root() -> None:
    raise NotImplementedError("P1-04")


async def main(cfg: DaemonConfig) -> None:
    raise NotImplementedError(f"P1-04 {cfg}")
