"""A project keeps its code in one place (P0-17, FR-2.1): a path on the agent server or
a repository URL, never both; paths are absolute without `..`, URLs carry no
credentials."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P0-17")
@pytest.mark.xfail(strict=True, reason="spec:P0-17")
def test_path_xor_url() -> None:
    """T-P0-17-13
    Both a code path and a repo URL raise `CodeLocationConflict` (answered 422
    `code_location_conflict`); either alone or neither passes; relative paths, `..`
    segments and URLs with credentials are refused.
    """
    from tumnis.modules.projects.rules import (  # noqa: PLC0415
        CodeLocationConflict,
        InvalidCodeLocation,
        validate_code_location,
    )

    with pytest.raises(CodeLocationConflict) as conflict:
        validate_code_location("/home/tumnis-agent/acme", "https://example.com/acme.git")
    assert conflict.value.code == "code_location_conflict"

    validate_code_location(None, None)
    validate_code_location("/home/tumnis-agent/acme", None)
    validate_code_location(None, "https://example.com/acme.git")
    validate_code_location(None, "git@example.com:studio/acme.git")
    validate_code_location(None, "ssh://git@example.com/studio/acme.git")

    for path in ("projects/acme", "/home/tumnis-agent/../etc", "/srv/acme/..", ""):
        with pytest.raises(InvalidCodeLocation):
            validate_code_location(path, None)
    for url in (
        "https://user:pass@example.com/acme.git",
        "https://token@example.com/acme.git",
        "http://example.com/acme.git",
        "ftp://example.com/acme.git",
        "acme.git",
    ):
        with pytest.raises(InvalidCodeLocation):
            validate_code_location(None, url)
