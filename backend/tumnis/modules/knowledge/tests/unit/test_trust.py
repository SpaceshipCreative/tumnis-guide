"""Trust defaults by origin (P1-17, FR-15.5, SAF-1): what a new knowledge item is trusted
with, and whether it is tainted, before anyone reviews it."""

from __future__ import annotations

import pytest

# origin -> (trust, tainted), the FR-15.5 table
TABLE: dict[str, tuple[str, bool]] = {
    "user_text": ("trusted", False),
    "upload": ("untrusted", True),
    "folder_external": ("untrusted", True),
    "agent": ("untrusted", False),
    "link": ("trusted", False),
}


@pytest.mark.req("FR-15.5")
@pytest.mark.wp("P1-17")
@pytest.mark.parametrize("origin", list(TABLE))
def test_default_trust(origin: str) -> None:
    """T-P1-17-06
    Text a person writes is trusted and untainted; uploads and files found in an outside
    folder are untrusted and tainted; agent-written items are untrusted (until a person
    marks them trusted) but untainted; a link is trusted and its content is never fetched.
    """
    from tumnis.modules.knowledge.rules import default_trust  # noqa: PLC0415

    assert default_trust(origin) == TABLE[origin]  # type: ignore[arg-type]
