"""Runner protocol 1 against its committed JSON Schemas (P1-04, FR-5.11, R-25).

The golden examples are the schema fixtures `make gen` checks too
(`backend/tests/contract/fixtures/runner/<type>/v1.json`): each validates against
`schemas/runner/v1/<type>.json` and parses into its model; broken copies of them are
refused by both the schema and the model.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    ValidationError,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.contract]

DAEMON_TYPES = ("register", "heartbeat", "result", "health_report", "ack")
SERVER_TYPES = ("registered", "run", "health_check", "ack")
MESSAGE_TYPES = (
    "register",
    "registered",
    "heartbeat",
    "run",
    "result",
    "health_check",
    "health_report",
    "ack",
)


def _example(repo_root: Path, type_: str) -> dict[str, Any]:
    path = repo_root / "backend/tests/contract/fixtures/runner" / type_ / "v1.json"
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _schema(repo_root: Path, type_: str) -> Draft202012Validator:
    schema = json.loads((repo_root / "schemas/runner/v1" / f"{type_}.json").read_text())
    return Draft202012Validator(schema)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
@pytest.mark.parametrize("type_", MESSAGE_TYPES)
def test_messages_validate(type_: str, repo_root: Path) -> None:
    """T-P1-04-01
    The golden example of each protocol 1 message validates against
    schemas/runner/v1/<type>.json and parses, on the side that reads it, into the model
    of that type; dumping the model gives the example back.
    """
    from tumnis.modules.agents.protocol import parse_daemon, parse_server  # noqa: PLC0415

    example = _example(repo_root, type_)
    _schema(repo_root, type_).validate(example)
    frame = json.dumps(example)
    parsed: list[Any] = []
    if type_ in DAEMON_TYPES:
        parsed.append(parse_daemon(frame))
    if type_ in SERVER_TYPES:
        parsed.append(parse_server(frame))
    assert parsed
    for message in parsed:
        assert message.type == type_
        assert json.loads(message.model_dump_json()) == example


# (case id) -> (message type, fields to set, fields to drop, expected error code)
INVALID: dict[str, tuple[str, dict[str, Any], tuple[str, ...], str]] = {
    "profile_parent_dir": ("run", {"profile": "../x"}, (), "invalid_message"),
    "profile_space": ("run", {"profile": "a b"}, (), "invalid_message"),
    "profile_64_chars": ("run", {"profile": "a" * 64}, (), "invalid_message"),
    "register_runner_name_parent_dir": (
        "register",
        {"runner_name": "../x"},
        (),
        "invalid_message",
    ),
    "health_report_profile_space": (
        "health_report",
        {"profile": "a b"},
        (),
        "invalid_message",
    ),
    "missing_message_id": ("heartbeat", {}, ("message_id",), "invalid_message"),
    "wrong_schema_version": ("heartbeat", {"schema_version": 2}, (), "unsupported_schema_version"),
    "oversized_text": ("result", {"text": "x" * 65_537}, (), "invalid_message"),
}


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
@pytest.mark.parametrize("case", sorted(INVALID))
def test_invalid_messages_rejected(case: str, repo_root: Path) -> None:
    """T-P1-04-02
    A bad profile name (`../x`, `a b`, 64 characters), a missing `message_id`, a wrong
    `schema_version` and an oversized `text` are refused by the committed schema and by
    the model: parsing raises InvalidMessage with the protocol error code
    (`unsupported_schema_version` for the version, `invalid_message` otherwise).
    """
    from tumnis.modules.agents.protocol import (  # noqa: PLC0415
        InvalidMessage,
        parse_daemon,
        parse_server,
    )

    type_, overrides, dropped, code = INVALID[case]
    message = copy.deepcopy(_example(repo_root, type_))
    message.update(overrides)
    for field in dropped:
        del message[field]

    with pytest.raises(ValidationError):
        _schema(repo_root, type_).validate(message)
    parse = parse_server if type_ == "run" else parse_daemon
    with pytest.raises(InvalidMessage) as refused:
        parse(json.dumps(message))
    assert refused.value.code == code
