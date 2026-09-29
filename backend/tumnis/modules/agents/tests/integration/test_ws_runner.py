"""`/ws/runner` (P1-04, FR-5.11, R-25): device-token authentication before accept, the
register handshake, protocol negotiation and per-message acks (protocol 1).

Starlette's TestClient drives the socket (httpx's ASGI transport speaks no WebSocket);
refusing before `accept()` reaches it as a disconnect with code 1008.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER
from tests.fakes.fake_runner import (
    create_runner,
    make_test_client,
    rotate_runner_token,
    runner_headers,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.testclient import WebSocketTestSession

    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

POLICY_VIOLATION = 1008
T = "2026-03-09T12:00:00Z"


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


def _refused_code(client: Any, headers: dict[str, str]) -> int:
    """The close code of a handshake the server refuses before accepting."""
    from starlette.websockets import WebSocketDisconnect  # noqa: PLC0415

    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect("/ws/runner", headers=headers),
    ):
        pass
    return int(closed.value.code)


def _frames_until_closed(ws: WebSocketTestSession, seconds: float = 5.0) -> tuple[list[Any], int]:
    """Every frame the server sends until it closes (and the close code); TimeoutError if it
    stays open for `seconds`."""
    from starlette.websockets import WebSocketDisconnect  # noqa: PLC0415

    frames: list[Any] = []
    box: list[int] = []
    done = threading.Event()

    def read() -> None:
        try:
            while True:
                frames.append(json.loads(ws.receive_text()))
        except WebSocketDisconnect as closed:
            box.append(closed.code)
        except Exception:  # the stream went away: no close code
            box.append(-1)
        done.set()

    threading.Thread(target=read, daemon=True).start()
    if not done.wait(seconds):
        raise TimeoutError(f"the socket stayed open for {seconds} s")
    return frames, box[0]


def _message(type_: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "message_id": str(uuid.uuid4()),
        "correlation_id": "runner:homelab-hermes",
        "sent_at": T,
        "type": type_,
        **fields,
    }


def _register(protocol_versions: list[int], name: str = "homelab-hermes") -> dict[str, Any]:
    return _message(
        "register",
        protocol_versions=protocol_versions,
        runner_name=name,
        host="hermes.example.org",
        os="linux",
        daemon_version="0.1.0",
        hermes_version="0.9.0",
        profiles=[{"name": "acme-site"}],
        capabilities=["run", "health"],
    )


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
def test_missing_token_rejected(app: FastAPI, workspace: WorkspaceHandle, db: DbUrls) -> None:
    """T-P1-04-06
    A handshake without `Authorization`, or with a header that is no bearer device token,
    is closed with 1008 before accept, and an `auth.failed` audit row with
    actor_type `device` is written in the (self-hosted) workspace.
    """
    with make_test_client(app) as client:
        assert _refused_code(client, {}) == POLICY_VIOLATION
        assert _refused_code(client, {"Authorization": "Basic cnVubmVyOnB3"}) == POLICY_VIOLATION

    rows = _owner(
        db,
        "SELECT actor_type, details->>'reason' FROM audit_log"
        " WHERE workspace_id = %s AND action = 'auth.failed' ORDER BY seq",
        (workspace.id,),
    )
    assert rows, "no auth.failed audit row"
    assert {actor_type for actor_type, _ in rows} == {"device"}
    assert "missing_token" in {reason for _, reason in rows}


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
def test_wrong_or_revoked_token_rejected(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls
) -> None:
    """T-P1-04-07
    A random device token, a real prefix with a wrong secret and a token rotated away are
    all refused with 1008 before accept; the rotated-in token is accepted.
    """
    runner_id, old = create_runner(workspace, clock, "homelab-hermes")
    new = rotate_runner_token(workspace, clock, runner_id)
    prefix, _, secret = old.removeprefix("tmd_").partition("_")
    wrong_secret = f"tmd_{prefix}_{'A' * len(secret)}"
    random_token = f"tmd_{'b' * len(prefix)}_{'C' * len(secret)}"

    with make_test_client(app) as client:
        for token in (random_token, wrong_secret, old):
            assert _refused_code(client, runner_headers(token)) == POLICY_VIOLATION, token
        with client.websocket_connect("/ws/runner", headers=runner_headers(new)) as ws:
            ws.send_text(json.dumps(_register([1])))
            registered = json.loads(ws.receive_text())
            assert registered["type"] == "registered"
            assert registered["runner_id"] == str(runner_id)

    rows = _owner(
        db,
        "SELECT count(*) FROM audit_log WHERE workspace_id = %s AND action = 'auth.failed'"
        " AND actor_type = 'device'",
        (workspace.id,),
    )
    assert rows[0][0] >= 3


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
def test_register_first_or_closed(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P1-04-08
    A heartbeat before register closes the socket (1008, after an `error` with code
    `not_registered`); so does silence past `register_timeout_s` (10 s in production, 0.5 s
    here).
    """
    _runner_id, token = create_runner(workspace, clock, "homelab-hermes")
    app.state.runner_register_timeout_s = 0.5

    with make_test_client(app) as client:
        with client.websocket_connect("/ws/runner", headers=runner_headers(token)) as ws:
            ws.send_text(json.dumps(_message("heartbeat", seq=1, running_run_ids=[])))
            frames, code = _frames_until_closed(ws)
        assert code == POLICY_VIOLATION
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["code"] == "not_registered"

        with client.websocket_connect("/ws/runner", headers=runner_headers(token)) as ws:
            started = time.monotonic()
            frames, code = _frames_until_closed(ws, seconds=5.0)
            waited = time.monotonic() - started
        assert code == POLICY_VIOLATION
        assert frames == []
        assert 0.4 <= waited < 3.0


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_protocol_negotiation_and_per_message_ack(
    app: FastAPI,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    fake_runner: FakeRunnerFactory,
) -> None:
    """T-P1-04-20
    `protocol_versions: [1, 2]` gets `registered.protocol_version == 1`; `[2]` gets
    `error{unsupported_protocol_version}` and a close. Every daemon message (register,
    heartbeat) gets its own `ack{ack_of}`. A server `run` leaves its mailbox row `sent`
    until the runner acks it, then the row turns `acked`.
    """
    from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
        DaemonTransport,
        HermesAgent,
    )
    from tumnis.modules.agents.tests.contract.base import make_packet  # noqa: PLC0415

    # [2] only: refused with an error, then closed.
    _other_id, other_token = create_runner(workspace, clock, "old-daemon")
    client = fake_runner.client
    with client.websocket_connect("/ws/runner", headers=runner_headers(other_token)) as ws:
        ws.send_text(json.dumps(_register([2], name="old-daemon")))
        frames, code = _frames_until_closed(ws)
    assert [f["type"] for f in frames] == ["error"]
    assert frames[0]["code"] == "unsupported_protocol_version"
    assert code == POLICY_VIOLATION

    # [1, 2]: the highest shared version, 1.
    runner = fake_runner(profiles=["acme-site"], connect=False)
    registered = runner.connect(protocol_versions=[1, 2])
    assert registered.protocol_version == 1

    # Every daemon message gets its own ack.
    runner.heartbeat()
    runner.heartbeat()
    ours = {m.message_id for m in runner.sent if m.type in ("register", "heartbeat")}
    assert len(ours) == 3
    runner.wait_for(lambda r: ours <= r.acked)

    # A server message stays `sent` until the runner acks it.
    runner.ack_server_messages = False
    profile_id = fake_runner.register_profile("acme-site", runner=runner)
    agent = HermesAgent(profile_id, DaemonTransport(workspace.ctx, clock))
    packet = make_packet(profile_id)
    await agent.dispatch(packet)
    runner.wait_for(lambda r: any(run.run_id == packet.run_id for run in r.runs()))
    run = next(m for m in runner.runs() if m.run_id == packet.run_id)

    def mailbox_status() -> str:
        rows = _owner(
            db, "SELECT status FROM runner_messages WHERE message_id = %s", (run.message_id,)
        )
        assert len(rows) == 1
        return str(rows[0][0])

    await asyncio.sleep(0.3)
    assert mailbox_status() == "sent"
    runner.ack_server_messages = True
    runner.ack(run)  # the ack the runner held back
    deadline = time.monotonic() + 5
    while mailbox_status() != "acked":
        assert time.monotonic() < deadline, "the mailbox row never turned acked"
        await asyncio.sleep(0.05)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_result_for_a_run_on_another_runner_is_dropped(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    fake_runner: FakeRunnerFactory,
) -> None:
    """A runner's `result` counts only for a run dispatched to that runner: a second runner
    in the workspace that sends a result for it is acked, and nothing is recorded or handed
    to the run's workflow."""
    from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
        DaemonTransport,
        HermesAgent,
    )
    from tumnis.modules.agents.protocol import Result  # noqa: PLC0415
    from tumnis.modules.agents.tests.contract.base import make_packet  # noqa: PLC0415

    owner = fake_runner(profiles=["acme-site"], name="homelab-hermes")
    intruder = fake_runner(profiles=["other-site"], name="other-hermes")
    profile_id = fake_runner.register_profile("acme-site", runner=owner)
    packet = make_packet(profile_id)
    await HermesAgent(profile_id, DaemonTransport(workspace.ctx, clock)).dispatch(packet)
    owner.wait_for(lambda r: any(run.run_id == packet.run_id for run in r.runs()))

    forged = Result(
        message_id=uuid.uuid4(),
        correlation_id=packet.correlation_id,
        sent_at=clock.now(),
        run_id=packet.run_id,
        status="succeeded",
        exit_code=0,
        output_json={"forged": True},
        text="forged",
        error=None,
        duration_ms=1,
        tokens={"input": 1, "output": 1},
        hermes_session_id="forged",
    )
    intruder.send(forged)
    intruder.wait_for(lambda r: forged.message_id in r.acked)

    rows = _owner(
        db,
        "SELECT count(*) FROM run_events WHERE run_id = %s AND kind = 'result'",
        (packet.run_id,),
    )
    assert rows[0][0] == 0


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
async def test_result_acks_its_run_message(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    fake_runner: FakeRunnerFactory,
) -> None:
    """A result from the run's runner shows the runner got the `run`: its mailbox row turns
    `acked` even when the runner's ack of it was lost, so the server never resends a run
    whose result it already has (the daemon would execute the skill again)."""
    from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
        DaemonTransport,
        HermesAgent,
    )
    from tumnis.modules.agents.tests.contract.base import SKILL, make_packet  # noqa: PLC0415

    runner = fake_runner(profiles=["acme-site"])
    runner.ack_server_messages = False  # the runner's ack of the run is lost
    runner.script("acme-site", SKILL, {"first_action": "Call Acme"})
    profile_id = fake_runner.register_profile("acme-site", runner=runner)
    packet = make_packet(profile_id)
    await HermesAgent(profile_id, DaemonTransport(workspace.ctx, clock)).dispatch(packet)
    runner.wait_for(lambda r: any(m.type == "result" and m.message_id in r.acked for m in r.sent))
    run = next(m for m in runner.runs() if m.run_id == packet.run_id)
    rows = _owner(db, "SELECT status FROM runner_messages WHERE message_id = %s", (run.message_id,))
    assert rows == [("acked",)]
