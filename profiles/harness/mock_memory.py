"""The memory mock for skill cases (P2-12): long-term memory with `retain` and `recall`,
like the user's Hindsight setup, recording every call (server `memory`) and storing
nothing.

`recall` answers with the case's scripted memories (the case's `mock.recall`), so a
digest case can hand the skill its stored cursor; `retain` answers `ok`. The tools are
the `memory` entry of harness.mock_worker_tools, which the hostile suite serves with
canned answers.

    python -m harness.mock_memory --script case-script.json --record calls.jsonl   # stdio
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import anyio
from mcp.server import Server

from harness.mock_mcp_min import Recorder, build_server, serve_stdio
from harness.mock_worker_tools import WORKER_TOOLS

MEMORY: Final = "memory"


def memory_responder(recall: Sequence[str]) -> Any:
    """What the mock answers: the scripted memories for `recall`, `ok` for `retain`."""
    memories = [{"content": text} for text in recall]

    def respond(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        del arguments
        if tool == "recall":
            return {"memories": list(memories)}
        return {"ok": True}

    return respond


def build_memory_server(recorder: Recorder, recall: Sequence[str] = ()) -> Server[Any]:
    return build_server(
        WORKER_TOOLS[MEMORY], recorder, name=MEMORY, respond=memory_responder(recall)
    )


def load_recall(path: Path | None) -> list[str]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(text) for text in data.get("recall") or []]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m harness.mock_memory", description=__doc__)
    parser.add_argument("--record", type=Path, help="append every call here as a JSON line")
    parser.add_argument("--script", type=Path, help="the case's script (its `recall` list)")
    args = parser.parse_args(argv)
    server = build_memory_server(Recorder(args.record), load_recall(args.script))
    anyio.run(serve_stdio, server)


if __name__ == "__main__":
    main(sys.argv[1:])
