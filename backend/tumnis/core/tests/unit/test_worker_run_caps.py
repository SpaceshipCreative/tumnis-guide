"""The run time caps from settings (P2-04, SAF-5, R-29, R-30): the worker hands
`AGENTS__RUN_ACTIVE_CAP_SECONDS` and `AGENTS__RUN_WALL_CLOCK_CEILING_SECONDS` to
`agents.api.configure_runs`; they shorten the caps on the real clock, so only a fakes
deployment may set them. Unset, the plan defaults stand (the project's max_run_minutes
and 24 hours). Housekeeping: `reconcile_runs` is scheduled hourly on the maintenance
queue."""

from __future__ import annotations

from typing import Any

import pytest

from tumnis import worker
from tumnis.core import workflows_ops
from tumnis.settings import Settings, SettingsError

DSN = "postgresql+psycopg://tumnis_app:pw@127.0.0.1:5432/tumnis"


def _settings(**values: Any) -> Settings:
    return Settings(database_url=DSN, database_direct_url=DSN, **values)


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
def test_worker_configures_the_run_caps_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """With fakes the worker passes the configured caps on; without them it puts the plan
    defaults back."""
    agents = worker._agents().api
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(agents, "configure_runs", lambda **kw: seen.append(kw))

    short = _settings(
        tumnis_adapters="fake",
        agents={"run_active_cap_seconds": 2, "run_wall_clock_ceiling_seconds": 3},
    )
    worker.configure_agents(short)
    worker.configure_agents(_settings())

    assert seen == [
        {"active_cap_seconds": 2.0, "wall_clock_ceiling_seconds": 3.0},
        {"active_cap_seconds": None, "wall_clock_ceiling_seconds": None},
    ]


@pytest.mark.req("SAF-5")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
def test_run_caps_are_refused_without_fakes() -> None:
    """A deployment with real adapters cannot shorten a run's caps; a cap is positive."""
    for field in ("run_active_cap_seconds", "run_wall_clock_ceiling_seconds"):
        with pytest.raises(SettingsError) as raised:
            _settings(agents={field: 5})
        assert raised.value.code == "run_caps_need_fakes", field
        with pytest.raises(ValueError):  # noqa: PT011  # pydantic's ValidationError
            _settings(tumnis_adapters="fake", agents={field: 0})


@pytest.mark.req("NFR Reliability")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
def test_reconcile_runs_is_scheduled_hourly_on_maintenance() -> None:
    """`reconcile_runs` is one of the agents module's schedules: hourly, on the maintenance
    queue (plan)."""
    agents = worker._agents()
    [entry] = [s for s in agents.schedules() if s["schedule_name"] == "reconcile-runs"]
    assert entry["workflow_fn"] is agents.reconcile_runs
    assert entry["queue_name"] == workflows_ops.MAINTENANCE_QUEUE
    assert entry["schedule"] == "0 * * * *"
