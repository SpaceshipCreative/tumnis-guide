"""Profile health rules (P2-10, SAF-2, SAF-3): drift between a profile's MCP servers and
its project's allowlist, token reach, and the health they add up to."""

import pytest

ALLOWLIST = ("tumnis", "jev", "github", "coolify")


@pytest.mark.req("SAF-2")
@pytest.mark.wp("P2-10")
@pytest.mark.xfail(strict=True, reason="spec:P2-10")
def test_allowlist_drift_table() -> None:
    """T-P2-10-02
    Extra (present, not allowed), missing (allowed, not present), both, and none: extra
    and missing are exact sets whatever the order or repetition of the report.
    """
    from tumnis.modules.agents.rules import Drift, allowlist_drift  # noqa: PLC0415

    table: list[tuple[list[str], Drift]] = [
        (["tumnis", "jev", "github", "coolify"], Drift(frozenset(), frozenset())),
        (["coolify", "jev", "tumnis", "github", "jev"], Drift(frozenset(), frozenset())),
        (
            ["tumnis", "jev", "github", "coolify", "shell"],
            Drift(extra=frozenset({"shell"}), missing=frozenset()),
        ),
        (
            ["tumnis", "jev"],
            Drift(extra=frozenset(), missing=frozenset({"github", "coolify"})),
        ),
        (
            ["tumnis", "shell", "browser"],
            Drift(
                extra=frozenset({"shell", "browser"}),
                missing=frozenset({"jev", "github", "coolify"}),
            ),
        ),
        ([], Drift(extra=frozenset(), missing=frozenset(ALLOWLIST))),
    ]
    for reported, expected in table:
        assert allowlist_drift(reported, ALLOWLIST) == expected, reported

    assert allowlist_drift(["tumnis"], []) == Drift(frozenset({"tumnis"}), frozenset())
