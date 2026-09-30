"""Which calendar events belong to a project (P1-12, FR-2.6).

`event_matches_project(event, links)` is deterministic: an event belongs to the project when
any attendee's address equals one of its person links, or any attendee's domain equals one
of its domain links. Addresses and domains compare case-insensitively; other link kinds
(repo, coolify_app) never match. The rules are imported inside the test, so this file
collects before they exist.
"""

from __future__ import annotations

from typing import Any

import pytest

LINKS: list[tuple[str, str]] = [
    ("person", "avery@example.com"),
    ("domain", "acme.example.org"),
    ("repo", "https://git.example.com/acme/site"),
]

CASES: list[Any] = [
    pytest.param(["avery@example.com"], LINKS, True, id="person_match"),
    pytest.param(["blake@acme.example.org"], LINKS, True, id="domain_match"),
    pytest.param(["Avery@Example.COM"], LINKS, True, id="case_insensitive_email"),
    pytest.param(["casey@ACME.example.org"], LINKS, True, id="case_insensitive_domain"),
    pytest.param(["drew@example.net", "avery@example.com"], LINKS, True, id="any_attendee_matches"),
    pytest.param(["drew@example.net"], LINKS, False, id="no_match"),
    pytest.param(["drew@sub.acme.example.org"], LINKS, False, id="subdomain_is_not_domain"),
    pytest.param(["avery@example.com.evil.example"], LINKS, False, id="prefix_is_not_person"),
    pytest.param([], LINKS, False, id="no_attendees"),
    pytest.param(["avery@example.com"], [], False, id="no_links"),
    pytest.param(
        ["git.example.com@example.net"], [("repo", "git.example.com")], False, id="repo_never"
    ),
]


@pytest.mark.req("FR-2.6")
@pytest.mark.wp("P1-12")
@pytest.mark.xfail(strict=True, reason="spec:P1-12")
@pytest.mark.parametrize(("attendees", "links", "expected"), CASES)
def test_table(attendees: list[str], links: list[tuple[str, str]], expected: bool) -> None:
    """T-P1-12-07
    Person match, domain match, case-insensitive emails, no match (and the edges around
    them: any attendee, subdomains, prefixes, no attendees, no links, other link kinds).
    """
    from tumnis.modules.planning.rules import (  # noqa: PLC0415
        EventDTO,
        ProjectLink,
        event_matches_project,
    )

    event = EventDTO(attendees=attendees)
    project_links = [ProjectLink(kind=kind, value=value) for kind, value in links]
    assert event_matches_project(event, project_links) is expected
