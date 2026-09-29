"""auth pure rules: no I/O, `now` and `tz` passed in."""

import re

_ZONE = re.compile(r"^(UTC|[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+)$")


def is_iana_zone(name: str, available: frozenset[str]) -> bool:
    """True when `name` is in the tz database and is a region/city style name (or UTC).
    Legacy abbreviations such as EST or PST8PDT are rejected. `available` is
    `zoneinfo.available_timezones()` at the call site, which keeps this rule pure."""
    return name in available and _ZONE.fullmatch(name) is not None
