"""auth pure rules: no I/O, `now` and `tz` passed in."""


def is_iana_zone(name: str, available: frozenset[str]) -> bool:
    """True when `name` is in the tz database and is a region/city style name (or UTC)."""
    raise NotImplementedError
