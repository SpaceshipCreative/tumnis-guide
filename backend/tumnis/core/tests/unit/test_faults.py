"""Kill points never arm in production (P0-07, ADR-0002)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[4]
START_WORKER = (
    "from tumnis.settings import Settings; from tumnis.worker import main; main(Settings())"
)


@pytest.mark.req("ADR-0002")
@pytest.mark.wp("P0-07")
@pytest.mark.xfail(strict=True, reason="spec:P0-07")
def test_killpoint_refuses_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P0-07-17
    With DEPLOYMENT_ENV=prod and TUMNIS_KILLPOINT set, worker startup raises instead of
    arming: `faults.arm` refuses, and `tumnis.worker.main` (run in a subprocess, so no DBOS
    or signal handler leaks into the test process) exits on KillpointRefused before it
    opens a connection.
    """
    from tumnis.core import faults  # noqa: PLC0415

    monkeypatch.setenv("TUMNIS_KILLPOINT", "relay.after_enqueue")
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")
    with pytest.raises(faults.KillpointRefused, match=r"relay\.after_enqueue"):
        faults.arm("prod")
    assert faults.armed() is None

    url = "postgresql+psycopg://tumnis_app:x@db.invalid/tumnis"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_DIRECT_URL", url)
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", START_WORKER],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode != 0
    assert "KillpointRefused" in done.stderr, done.stderr
    assert "relay.after_enqueue" in done.stderr, done.stderr
