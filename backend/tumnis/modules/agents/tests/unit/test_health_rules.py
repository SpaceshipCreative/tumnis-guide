"""Profile health rules (P2-10, SAF-2, SAF-3): drift between a profile's MCP servers and
its project's allowlist, token reach, and the health they add up to."""

from uuid import UUID

import pytest

from tumnis.modules.agents.rules import (
    Drift,
    ReachVerdict,
    TokenReach,
    github_repo,
    profile_health,
    reach_targets,
    reach_verdict,
)

ALLOWLIST = ("tumnis", "jev", "github", "coolify")


@pytest.mark.req("SAF-2")
@pytest.mark.wp("P2-10")
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


CLEAN = TokenReach(token_present=True, own_reachable={"acme/site": True})
NO_DRIFT = Drift(frozenset(), frozenset())


@pytest.mark.req("SAF-3")
@pytest.mark.wp("P2-10")
def test_reach_verdict_levels() -> None:
    """Foreign reach is degraded (and names the target); a missing token, an own target it
    cannot reach, or a failed probe is a warning; nothing probed or all clean is ok."""
    assert reach_verdict(None, None) == ReachVerdict("ok")
    assert reach_verdict(CLEAN, None).level == "ok"
    broad = CLEAN.model_copy(update={"foreign_reachable": ["beta/app"]})
    assert reach_verdict(broad, None) == ReachVerdict("degraded", ("github:beta/app",))
    coolify = TokenReach(token_present=True, own_reachable={}, foreign_reachable=["app-b"])
    assert reach_verdict(CLEAN, coolify).foreign == ("coolify:app-b",)
    missing = TokenReach(token_present=False)
    assert reach_verdict(missing, None) == ReachVerdict(
        "warning", (), ("github: no token in the profile",)
    )
    short = TokenReach(token_present=True, own_reachable={"acme/site": False})
    assert reach_verdict(None, short).reasons == ("coolify: cannot reach its own acme/site",)
    failed = TokenReach(token_present=True, errors=["timeout on beta/app"])
    assert reach_verdict(failed, None).level == "warning"


@pytest.mark.req("SAF-2", "SAF-3", "FR-5.9")
@pytest.mark.wp("P2-10")
def test_profile_health_status() -> None:
    """offline beats degraded beats warning beats ok."""
    ok, warn = ReachVerdict("ok"), ReachVerdict("warning", (), ("x",))
    bad = ReachVerdict("degraded", ("github:beta/app",))
    extra = Drift(frozenset({"shell"}), frozenset())
    missing = Drift(frozenset(), frozenset({"jev"}))
    assert profile_health(False, True, extra, bad) == "offline"
    assert profile_health(True, True, extra, ok) == "degraded"
    assert profile_health(True, True, NO_DRIFT, bad) == "degraded"
    assert profile_health(True, True, missing, ok) == "warning"
    assert profile_health(True, False, NO_DRIFT, ok) == "warning"
    assert profile_health(True, True, NO_DRIFT, warn) == "warning"
    assert profile_health(True, None, NO_DRIFT, ok) == "ok"
    assert profile_health(True, True, NO_DRIFT, ok) == "ok"


@pytest.mark.req("SAF-3")
@pytest.mark.wp("P2-10")
def test_github_repo_forms() -> None:
    """owner/name, https and ssh github.com URLs (with or without .git) name the same
    repo; other hosts and paths name none."""
    for value in (
        "Acme/Site",
        "https://github.com/acme/site",
        "https://github.com/acme/site.git",
        "git@github.com:acme/site.git",
        "ssh://git@github.com/acme/site",
        " acme/site/ ",
    ):
        assert github_repo(value) == "acme/site", value
    for value in ("https://gitlab.com/acme/site", "acme", "acme/site/tree/main", "../x", "a/.."):
        assert github_repo(value) is None, value


@pytest.mark.req("SAF-3")
@pytest.mark.wp("P2-10")
def test_reach_targets_split_own_and_foreign() -> None:
    """The profile's project owns its repos and apps; every other project's are foreign,
    deduplicated, a shared one counts as own, and each list stops at the limit."""
    acme, beta, gamma = (UUID(int=n) for n in (1, 2, 3))
    repos = [
        (acme, "acme/site"),
        (beta, "https://github.com/beta/app.git"),
        (gamma, "beta/app"),
        (gamma, "acme/site"),
        (beta, "https://gitlab.com/beta/x"),
    ]
    apps = [(acme, "app-acme"), (beta, "app-beta"), (beta, " ")]
    found = reach_targets(acme, repos, apps)
    assert found.own_repos == ("acme/site",)
    assert found.foreign_repos == ("beta/app",)
    assert found.own_apps == ("app-acme",)
    assert found.foreign_apps == ("app-beta",)
    assert ("github:beta/app", beta) in found.owners
    assert ("coolify:app-beta", beta) in found.owners

    master = reach_targets(None, repos, apps)
    assert master.own_repos == ()
    assert master.foreign_repos == ("acme/site", "beta/app")

    many = [(beta, f"beta/r{n}") for n in range(60)]
    assert len(reach_targets(acme, many, [], limit=50).foreign_repos) == 50
