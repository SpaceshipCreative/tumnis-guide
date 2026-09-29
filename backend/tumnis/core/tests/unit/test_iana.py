"""The workspace timezone is an IANA region/city name (P0-08, REL-6, FR-4.7)."""

from __future__ import annotations

import zoneinfo

import pytest


@pytest.mark.req("REL-6")
@pytest.mark.wp("P0-08")
def test_timezone_accepts_only_iana_names() -> None:
    """T-P0-08-12
    Accepts America/New_York, Australia/Sydney and UTC; rejects the legacy abbreviations
    EST and PST8PDT, an unknown zone, a padded name and the empty string.
    """
    from tumnis.modules.auth.rules import is_iana_zone  # noqa: PLC0415

    available = frozenset(zoneinfo.available_timezones())
    for name in ("America/New_York", "Australia/Sydney", "UTC"):
        assert is_iana_zone(name, available), name
    for name in ("EST", "PST8PDT", "Mars/Olympus", " America/New_York", ""):
        assert not is_iana_zone(name, available), name
