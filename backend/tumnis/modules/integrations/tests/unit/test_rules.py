"""integrations pure rules: taint propagation (P0-12, FR-14.2, SAF-1 groundwork)."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-14.2")
@pytest.mark.wp("P0-12")
@pytest.mark.parametrize(
    ("inputs", "tainted"),
    [
        ((), False),
        ((False,), False),
        ((False, False, False), False),
        ((True,), True),
        ((True, True), True),
        ((False, True), True),
        ((True, False, False), True),
    ],
)
def test_propagate_taint(inputs: tuple[bool, ...], tainted: bool) -> None:
    """T-P0-12-13
    Table test: any tainted input gives tainted output; trusted-only inputs (and no inputs)
    stay trusted.
    """
    from tumnis.modules.integrations.rules import propagate_taint  # noqa: PLC0415

    assert propagate_taint(*inputs) is tainted


@pytest.mark.req("FR-14.1")
@pytest.mark.wp("P0-12")
def test_record_owner_names_each_types_module() -> None:
    """Integrations owns people, threads, messages, notes and artifacts; calendar owns
    events and knowledge documents; an unknown type is refused."""
    from tumnis.modules.integrations.rules import record_owner  # noqa: PLC0415

    owners = {t: record_owner(t) for t in ("person", "thread", "message", "note", "artifact")}
    assert set(owners.values()) == {"integrations"}
    assert (record_owner("event"), record_owner("document")) == ("calendar", "knowledge")
    with pytest.raises(ValueError, match="unknown"):
        record_owner("invoice")


# --- P3-02: backfill, cadence and connection status -------------------------------------------


@pytest.mark.req("Data flow rule 3")
@pytest.mark.wp("P3-02")
def test_backfill_defaults_to_30_days_and_respects_provider_cap() -> None:
    """T-P3-02-06
    The first sync reaches back 30 days by default; a connection set to 90 reaches back
    90; a provider that only keeps 30 days (a cap) wins over a longer setting.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from tumnis.modules.integrations.rules import (  # noqa: PLC0415
        ConnectionSettings,
        backfill_start,
    )

    now = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    assert ConnectionSettings().backfill_days == 30
    assert backfill_start(now, ConnectionSettings(), None) == now - timedelta(days=30)
    ninety = ConnectionSettings(backfill_days=90)
    assert backfill_start(now, ninety, None) == now - timedelta(days=90)
    assert backfill_start(now, ninety, 30) == now - timedelta(days=30)
    assert backfill_start(now, ConnectionSettings(backfill_days=7), 30) == now - timedelta(days=7)


# Every (status, outcome) pair and where it lands (the plan's table, T-P3-02-08).
STATUS_TABLE: dict[tuple[str, str], str] = {
    ("pending_auth", "success"): "ok",
    ("pending_auth", "transient_error"): "pending_auth",
    ("pending_auth", "auth_error"): "auth_required",
    ("ok", "success"): "ok",
    ("ok", "transient_error"): "degraded",
    ("ok", "auth_error"): "auth_required",
    ("syncing", "success"): "ok",
    ("syncing", "transient_error"): "degraded",
    ("syncing", "auth_error"): "auth_required",
    ("degraded", "success"): "ok",
    ("degraded", "transient_error"): "degraded",
    ("degraded", "auth_error"): "auth_required",
    ("auth_required", "success"): "ok",
    ("auth_required", "transient_error"): "auth_required",
    ("auth_required", "auth_error"): "auth_required",
    ("disabled", "success"): "disabled",
    ("disabled", "transient_error"): "disabled",
    ("disabled", "auth_error"): "disabled",
}


@pytest.mark.req("REL-3")
@pytest.mark.wp("P3-02")
def test_status_transitions_table() -> None:
    """T-P3-02-08
    Every (status, outcome) pair maps as the table says: success brings a connection back
    to ok (after a reconnect too), a transient error degrades it, an auth error always
    asks for a new sign-in, and a disabled connection stays disabled. Five transient
    errors in a row keep it degraded while `next_sync_at` backs off, capped at 60 minutes.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from tumnis.modules.integrations.rules import (  # noqa: PLC0415
        ConnectionStatus,
        SyncOutcome,
        next_sync_at,
        status_after,
    )

    pairs = {
        (status.value, outcome.value) for status in ConnectionStatus for outcome in SyncOutcome
    }
    assert pairs == set(STATUS_TABLE)
    for (status, outcome), expected in STATUS_TABLE.items():
        got = status_after(ConnectionStatus(status), SyncOutcome(outcome))
        assert got == ConnectionStatus(expected), (status, outcome)

    state = ConnectionStatus.ok
    for _ in range(5):
        state = status_after(state, SyncOutcome.transient_error)
    assert state == ConnectionStatus.degraded

    now = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    last = now - timedelta(minutes=2)
    assert next_sync_at(now, last, 5, 0) == last + timedelta(minutes=5)
    assert next_sync_at(now, None, 5, 0) == now + timedelta(minutes=5)
    assert next_sync_at(now, last, 5, 1) == now + timedelta(minutes=10)
    assert next_sync_at(now, last, 5, 2) == now + timedelta(minutes=20)
    assert next_sync_at(now, last, 5, 5) == now + timedelta(minutes=60)
    assert next_sync_at(now, last, 5, 30) == now + timedelta(minutes=60)
