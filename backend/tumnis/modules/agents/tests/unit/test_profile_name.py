"""Project profile names (P1-06, FR-2.1): a project's Hermes profile is named after the
project, safe for the runner protocol, and never collides with a taken or reserved name."""

import re

import pytest

from tumnis.modules.agents.rules import NAME_RE

CUT = "northwind-traders-quarterly-marketing"
SIXTY = "Northwind Traders Quarterly Marketing Website Redesign Phase2"  # 61 characters

CASES: list[tuple[str, set[str], str]] = [
    ("Acme Site", set(), "acme-site"),
    ("Acme Site", {"acme-site"}, "acme-site-2"),
    ("Acme Site", {"acme-site", "acme-site-2"}, "acme-site-3"),
    ("Ünïcode Co.", set(), "unicode-co"),
    ("  --Acme__Site!!  ", set(), "acme-site"),
    ("🚀🎉", set(), "project"),
    ("", set(), "project"),
    ("🚀", {"project"}, "project-2"),
    (SIXTY, set(), f"{CUT}-we"),
    ("Northwind Traders Quarterly Marketing W Site", set(), f"{CUT}-w"),
    (SIXTY, {f"{CUT}-we"}, f"{CUT}-2"),
    ("Default", set(), "default-2"),
    ("Tumnis Master", set(), "tumnis-master-2"),
]


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P1-06")
@pytest.mark.xfail(strict=True, reason="spec:P1-06")
@pytest.mark.parametrize(("project_name", "taken", "expected"), CASES)
def test_profile_name_for(project_name: str, taken: set[str], expected: str) -> None:
    """T-P1-06-08
    Lower-case, ASCII-folded, only [a-z0-9-], dashes collapsed and trimmed, at most 40
    characters without a trailing dash; '-2', '-3' … on a collision with a taken or
    reserved name (the suffix fits in the 40); nothing left means 'project'.
    """
    from tumnis.modules.agents.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        PROFILE_NAME_MAX,
        RESERVED_PROFILE_NAMES,
        profile_name_for,
    )

    name = profile_name_for(project_name, taken)
    assert name == expected
    assert re.fullmatch(NAME_RE, name)
    assert len(name) <= PROFILE_NAME_MAX == 40
    assert not name.endswith("-")
    assert name not in taken
    assert name not in RESERVED_PROFILE_NAMES
    assert name != "tumnis-master"  # the master's own name
