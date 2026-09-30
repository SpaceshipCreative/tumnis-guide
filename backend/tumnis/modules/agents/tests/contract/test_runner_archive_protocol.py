"""The runner's archive messages against their committed JSON Schemas (P2-18, FR-5.10).

`restore` and `purge_archive` (server to daemon) and `archive_done` and `restore_done`
(daemon to server) are new protocol-2 types at version 1. Their golden examples are the
schema fixtures under `backend/tests/contract/fixtures/runner/<type>/v1.json`; each
validates, parses on the side that reads it and dumps back unchanged, and the socket never
renders an archive command for a protocol-1 daemon.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.contract]

# message type -> the side that parses it
CASES: dict[str, str] = {
    "archive": "server",
    "restore": "server",
    "purge_archive": "server",
    "archive_done": "daemon",
    "restore_done": "daemon",
}


def _example(repo_root: Path, type_: str) -> dict[str, Any]:
    path = repo_root / "backend/tests/contract/fixtures/runner" / type_ / "v1.json"
    data: dict[str, Any] = json.loads(path.read_text())
    return data


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
@pytest.mark.parametrize("type_", list(CASES))
def test_archive_messages_validate_and_round_trip(type_: str, repo_root: Path) -> None:
    from tumnis.modules.agents.protocol import parse_daemon, parse_server  # noqa: PLC0415

    example = _example(repo_root, type_)
    schema = json.loads((repo_root / f"schemas/runner/v1/{type_}.json").read_text())
    Draft202012Validator(schema).validate(example)
    parse = parse_server if CASES[type_] == "server" else parse_daemon
    message = parse(json.dumps(example))
    assert message.type == type_
    assert message.schema_version == 1
    assert json.loads(message.model_dump_json()) == example


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P2-18")
def test_archive_commands_are_protocol_2_only() -> None:
    from tumnis.modules.agents.ws import PROTOCOL_2_COMMANDS  # noqa: PLC0415

    assert {"archive", "restore", "purge_archive"} <= PROTOCOL_2_COMMANDS
