"""Runner protocol 2 against its committed JSON Schemas, and version skew (P2-07, FR-5.11,
REL-4, R-25).

Protocol 2 adds `stream`, `status`, `cancel`, `upload_artifact`, `archive` and `nack` (new
message types at version 1), batched acks (`ack` version 2) and version-2 `run` and
`result`. The golden examples are the schema fixtures under
`backend/tests/contract/fixtures/runner/<type>/v<n>.json`. The previous daemon release
speaks protocol 1 only: its recorded frames are still accepted and its sessions stay on
protocol 1.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = [pytest.mark.contract]

# case -> (message type, schema version, the side that parses it)
CASES: dict[str, tuple[str, int, str]] = {
    "stream": ("stream", 1, "daemon"),
    "status": ("status", 1, "daemon"),
    "cancel": ("cancel", 1, "server"),
    "upload_artifact": ("upload_artifact", 1, "daemon"),
    "archive": ("archive", 1, "server"),
    "ack": ("ack", 2, "both"),
    "nack": ("nack", 1, "server"),
    "run_v2": ("run", 2, "server"),
    "result_v2": ("result", 2, "daemon"),
}
RECORDED_V1 = ("register", "heartbeat", "result")


def _example(repo_root: Path, type_: str, version: int) -> dict[str, Any]:
    path = repo_root / "backend/tests/contract/fixtures/runner" / type_ / f"v{version}.json"
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _validator(repo_root: Path, type_: str, version: int) -> Draft202012Validator:
    schema = json.loads((repo_root / f"schemas/runner/v{version}/{type_}.json").read_text())
    return Draft202012Validator(schema)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@pytest.mark.parametrize("case", list(CASES))
def test_protocol2_messages_validate(case: str, repo_root: Path) -> None:
    """T-P2-07-01
    The golden example of each protocol-2 message validates against its committed schema
    (`schemas/runner/v<n>/<type>.json`) and carries an integer `schema_version` equal to
    that version; it parses on the side that reads it into the model of that type and
    version, and dumping the model gives the example back.
    """
    from tumnis.modules.agents.protocol import parse_daemon, parse_server  # noqa: PLC0415

    type_, version, side = CASES[case]
    example = _example(repo_root, type_, version)
    _validator(repo_root, type_, version).validate(example)
    assert type(example["schema_version"]) is int
    assert example["schema_version"] == version
    both: list[Callable[[str], Any]] = [parse_daemon, parse_server]
    parsers = {"daemon": both[:1], "server": both[1:]}.get(side, both)
    for parse in parsers:
        message = parse(json.dumps(example))
        assert message.type == type_
        assert message.schema_version == version
        assert json.loads(message.model_dump_json()) == example


@pytest.mark.req("REL-4")
@pytest.mark.wp("P2-07")
def test_previous_release_daemon_accepted(repo_root: Path) -> None:
    """T-P2-07-11
    The recorded `register`, `heartbeat` and `result` frames of the previous daemon release
    (protocol 1) still validate against `schemas/runner/v1/` and parse; although the server
    now speaks protocols 1 and 2, it picks protocol 1 for that daemon, and protocol 2 for a
    daemon that lists 2 with the protocol-2 capabilities.
    """
    from tumnis.modules.agents.protocol import (  # noqa: PLC0415
        SERVER_PROTOCOL_VERSIONS,
        Register,
        negotiate,
        parse_daemon,
    )

    recordings = repo_root / "backend/tumnis/modules/agents/tests/recordings/runner_v1"
    parsed = {}
    for type_ in RECORDED_V1:
        frame = json.loads((recordings / f"{type_}.json").read_text())
        _validator(repo_root, type_, 1).validate(frame)
        message = parse_daemon(json.dumps(frame))
        assert message.type == type_
        assert message.schema_version == 1
        parsed[type_] = message

    assert tuple(SERVER_PROTOCOL_VERSIONS) == (1, 2)
    old = parsed["register"]
    assert isinstance(old, Register)
    assert negotiate(old.protocol_versions, old.capabilities) == 1

    new = _example(repo_root, "register", 1)
    new["protocol_versions"] = [1, 2]
    new["capabilities"] = ["run", "health", "stream", "cancel", "upload_artifact", "worktree"]
    _validator(repo_root, "register", 1).validate(new)
    current = parse_daemon(json.dumps(new))
    assert isinstance(current, Register)
    assert negotiate(current.protocol_versions, current.capabilities) == 2
