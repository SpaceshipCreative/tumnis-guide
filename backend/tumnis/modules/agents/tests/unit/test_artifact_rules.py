"""The artifact rule (P2-07, FR-5.11): which `nack` code an `upload_artifact` gets."""

from __future__ import annotations

import hashlib

import pytest

from tumnis.modules.agents.rules import ARTIFACT_MAX_BYTES, artifact_bytes, artifact_refusal


def _check(content: str, media_type: str = "text/plain", **overrides: object) -> str | None:
    raw = artifact_bytes(content)
    sha = hashlib.sha256(raw).hexdigest() if raw is not None else None
    size = len(raw) if raw is not None else len(content)
    fields: dict[str, object] = {"size": size, "sha256": sha or "0" * 64}
    fields.update(overrides)
    return artifact_refusal(
        media_type,
        content,
        int(fields["size"]),  # type: ignore[call-overload]
        str(fields["sha256"]),
        sha,
    )


CASES: dict[str, tuple[str, str, dict[str, object], str | None]] = {
    "markdown": ("# Notes\n", "text/markdown", {}, None),
    "diff": ("--- a\n+++ b\n", "text/x-diff", {}, None),
    "json": ('{"a": 1}', "application/json", {}, None),
    "image": ("\x89PNG", "image/png", {}, "bad_media_type"),
    "html": ("<p>x</p>", "text/html", {}, "bad_media_type"),
    "nul_byte": ("MZ\x00\x00", "text/plain", {}, "not_utf8"),
    "lone_surrogate": ("caf\udce9", "text/plain", {}, "not_utf8"),
    "too_large": ("x" * (ARTIFACT_MAX_BYTES + 1), "text/plain", {}, "too_large"),
    "declared_too_large": ("x", "text/plain", {"size": ARTIFACT_MAX_BYTES + 1}, "too_large"),
    "exactly_max": ("x" * ARTIFACT_MAX_BYTES, "text/plain", {}, None),
    "wrong_sha": ("hello", "text/plain", {"sha256": "0" * 64}, "sha_mismatch"),
    "wrong_size": ("hello", "text/plain", {"size": 4}, "sha_mismatch"),
    "upper_case_sha": (
        "hello",
        "text/plain",
        {"sha256": hashlib.sha256(b"hello").hexdigest().upper()},
        None,
    ),
    "multibyte": ("café ☕", "text/plain", {}, None),
}


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@pytest.mark.parametrize("case", sorted(CASES))
def test_artifact_refusal(case: str) -> None:
    """Media type first, then UTF-8 (a NUL byte counts as binary), then the 256 KiB cap
    (declared or actual), then the declared size and sha256."""
    content, media_type, overrides, expected = CASES[case]
    assert _check(content, media_type, **overrides) == expected
