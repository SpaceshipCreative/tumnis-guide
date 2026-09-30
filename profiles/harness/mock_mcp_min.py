"""The minimal recording mock of the Tumnis MCP server (P2-11, SAF-6). Name stubs until the
TDD steps fill them in."""

from pathlib import Path
from typing import Any

from mcp.server import Server

from harness.judge import RecordedCall


class Recorder:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.calls: list[RecordedCall] = []


def load_catalogue(path: Path | None = None) -> list[dict[str, Any]]:
    raise NotImplementedError


def build_server(catalogue: list[dict[str, Any]], recorder: Recorder) -> Server[Any]:
    raise NotImplementedError
