"""Harness self tests for the fake runner's scripts (P1-08), unit layer: no app, no socket.

A run answers with the script that was in place when the run arrived. A test that sees the
run in `runs()` and scripts the next answer (test_enrich's relabel test scripts the
follow-up's answer that way) must not change the answer of the run it saw: the reader
records the run, acks it and only then answers, and a script set in between used to be
the one it answered with (the enrichment relabel flake in CI).
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tests.fakes.fake_runner import FakeRunner
    from tumnis.core.clock import FixedClock

PROFILE, SKILL = "acme-site", "enrich"
FIRST: dict[str, Any] = {"first_action": "first answer"}
NEXT: dict[str, Any] = {"first_action": "next answer"}


class _Socket:
    """Hands the reader one frame, then reports the socket closed; keeps what is sent."""

    def __init__(self, frames: list[str]) -> None:
        self.frames = frames
        self.sent: list[str] = []

    def receive_text(self) -> str:
        if not self.frames:
            raise RuntimeError("closed")
        return self.frames.pop(0)

    def send_text(self, text: str) -> None:
        self.sent.append(text)


def _run_frame() -> str:
    from tumnis.modules.agents.protocol import Run, SchemaRef  # noqa: PLC0415

    run_id = uuid.uuid4()
    return Run(
        message_id=uuid.uuid4(),
        correlation_id=f"run:{run_id}",
        sent_at=datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
        run_id=run_id,
        profile=PROFILE,
        skill=SKILL,
        packet={"body": {}},
        output_schema=SchemaRef(family="enrichment", name="result", version=1),
        timeout_s=60,
    ).model_dump_json()


def _runner(clock: FixedClock, socket: _Socket) -> FakeRunner:
    from tests.fakes.fake_runner import FakeRunner  # noqa: PLC0415

    runner = FakeRunner(
        None,  # type: ignore[arg-type]  # no app: the reader is fed by hand
        "token",
        uuid.uuid4(),
        name="runner-1",
        profiles=[PROFILE],
        clock=clock,
    )
    runner._ws = socket  # type: ignore[assignment]
    return runner


def _results(runner: FakeRunner) -> list[dict[str, Any] | None]:
    from tumnis.modules.agents.protocol import Result  # noqa: PLC0415

    return [m.output_json for m in runner.sent if isinstance(m, Result)]


@pytest.mark.req("FR-4.4")
@pytest.mark.wp("P1-08")
def test_run_answers_with_the_script_it_arrived_under(
    clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gated script is in place when the run arrives; the test scripts the next answer
    while the run is being acked. The run waits for its gate and answers with the first
    script, and nothing is sent before the gate opens."""
    socket = _Socket([_run_frame()])
    runner = _runner(clock, socket)
    gate = threading.Event()
    runner.script(PROFILE, SKILL, FIRST, gate=gate)

    def ack_while_the_test_scripts_the_next_answer(_message: Any) -> None:
        runner.script(PROFILE, SKILL, NEXT)

    monkeypatch.setattr(runner, "ack", ack_while_the_test_scripts_the_next_answer)
    runner._read(socket)  # type: ignore[arg-type]
    sent_before_gate = _results(runner)
    gate.set()
    deadline = time.monotonic() + 5
    while not _results(runner) and time.monotonic() < deadline:
        time.sleep(0.01)  # the gated answer is sent from its own thread

    assert len(runner.runs()) == 1
    assert sent_before_gate == []
    assert _results(runner) == [FIRST]
    assert runner.errors == []
