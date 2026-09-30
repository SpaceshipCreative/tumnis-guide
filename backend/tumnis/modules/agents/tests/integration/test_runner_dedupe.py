"""Runner protocol 2 on the server (P2-07, FR-5.11, REL-4): dedupe by `message_id` across
connections, unacked commands resent on register, artifact checks with `nack` codes, acks in
the session's protocol style, cancel on an older daemon, and the profile version from a
`status` message.

The fake runner speaks protocol 2 when it registers with `[1, 2]` and the protocol-2
capabilities; without them the session stays on protocol 1 (an older daemon).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.agents.api import RunHandle, TaskPacket

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

T = "2026-03-09T12:00:00Z"
V2_CAPABILITIES = ["run", "health", "stream", "cancel", "upload_artifact", "worktree"]
ARTIFACT_MAX = 256 * 1024


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


def _frame(type_: str, run_id: uuid.UUID | None = None, **fields: Any) -> dict[str, Any]:
    """A protocol-2 daemon message (new types are schema version 1)."""
    return {
        "schema_version": 1,
        "message_id": str(uuid.uuid4()),
        "correlation_id": f"run:{run_id}" if run_id else "runner:homelab-hermes",
        "sent_at": T,
        "type": type_,
        **({"run_id": str(run_id)} if run_id is not None else {}),
        **fields,
    }


def _stream(run_id: uuid.UUID, seq: int, text: str, kind: str = "log") -> dict[str, Any]:
    return _frame("stream", run_id, seq=seq, kind=kind, text=text, ts=T)


def _v2_runner(fake_runner: FakeRunnerFactory, name: str = "homelab-hermes") -> FakeRunner:
    runner = fake_runner(profiles=["acme-site"], name=name, connect=False)
    registered = runner.connect(protocol_versions=[1, 2], capabilities=V2_CAPABILITIES)
    assert registered.protocol_version == 2
    return runner


async def _dispatch(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    fake_runner: FakeRunnerFactory,
    runner: FakeRunner,
    *,
    wait: bool = True,
) -> tuple[TaskPacket, RunHandle, uuid.UUID]:
    from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
        DaemonTransport,
        HermesAgent,
    )
    from tumnis.modules.agents.tests.contract.base import make_packet  # noqa: PLC0415

    profile_id = fake_runner.register_profile("acme-site", runner=runner)
    agent = HermesAgent(profile_id, DaemonTransport(workspace.ctx, clock))
    packet = make_packet(profile_id)
    handle = await agent.dispatch(packet)
    if wait:
        runner.wait_for(lambda r: any(run.run_id == packet.run_id for run in r.runs()))
    return packet, handle, profile_id


def _send(runner: FakeRunner, frame: dict[str, Any]) -> uuid.UUID:
    runner.send_raw(json.dumps(frame))
    return uuid.UUID(frame["message_id"])


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
async def test_artifact_text_only_and_size_capped(
    workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls, fake_runner: FakeRunnerFactory
) -> None:
    """T-P2-07-02
    An `upload_artifact` that is binary, not UTF-8, over 256 KiB, whose sha256 does not
    match, or for a run that is not this runner's, is refused with `nack` and its code
    (`bad_media_type`, `not_utf8`, `too_large`, `sha_mismatch`, `unknown_run`) and nothing
    is stored; a small text artifact is stored as one `artifact` run event and acked.
    """
    runner = _v2_runner(fake_runner)
    packet, _handle, _profile = await _dispatch(workspace, clock, fake_runner, runner)

    def artifact(
        content: str,
        *,
        media_type: str = "text/plain",
        run_id: uuid.UUID | None = None,
        sha256: str | None = None,
    ) -> dict[str, Any]:
        raw = content.encode("utf-8", "surrogatepass")
        return _frame(
            "upload_artifact",
            run_id or packet.run_id,
            name="out.txt",
            media_type=media_type,
            size=len(raw),
            sha256=sha256 or hashlib.sha256(raw).hexdigest(),
            content=content,
        )

    refused = {
        "bad_media_type": artifact("\x89PNG\r\n\x1a\n", media_type="image/png"),
        "binary": artifact("MZ\x00\x00\x90\x00"),
        "not_utf8": artifact("caf\udce9 au lait"),
        "too_large": artifact("x" * (ARTIFACT_MAX + 1)),
        "sha_mismatch": artifact("hello", sha256="0" * 64),
        "unknown_run": artifact("hello", run_id=uuid.uuid4()),
    }
    codes = {
        "bad_media_type": "bad_media_type",
        "binary": "not_utf8",
        "not_utf8": "not_utf8",
        "too_large": "too_large",
        "sha_mismatch": "sha_mismatch",
        "unknown_run": "unknown_run",
    }
    sent = {case: _send(runner, frame) for case, frame in refused.items()}
    runner.wait_for(lambda r: set(sent.values()) <= set(r.nacked))
    assert {case: runner.nacked[mid] for case, mid in sent.items()} == codes
    assert not set(sent.values()) & runner.acked

    good = artifact("# Notes\n\nThe March invoice is ready.\n", media_type="text/markdown")
    good_id = _send(runner, good)
    runner.wait_for(lambda r: good_id in r.acked)
    rows = _owner(
        db,
        "SELECT message_id, payload FROM run_events WHERE run_id = %s AND kind = 'artifact'",
        (packet.run_id,),
    )
    assert [row[0] for row in rows] == [good_id]
    stored = rows[0][1]
    assert stored["name"] == "out.txt"
    assert stored["media_type"] == "text/markdown"
    assert stored["sha256"] == good["sha256"]
    assert stored["content"] == good["content"]
    ids = {str(mid) for mid in sent.values()}
    leaked = _owner(
        db, "SELECT count(*) FROM run_events WHERE message_id::text = ANY(%s)", (list(ids),)
    )
    assert leaked == [(0,)]


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
async def test_replayed_messages_stored_once(
    workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls, fake_runner: FakeRunnerFactory
) -> None:
    """T-P2-07-04
    The same `stream` message (same `message_id`) sent over two connections is stored as
    one `run_events` row, and acked both times (the daemon replays until acked).
    """
    runner = _v2_runner(fake_runner)
    packet, _handle, _profile = await _dispatch(workspace, clock, fake_runner, runner)
    frame = _stream(packet.run_id, 1, "Reading the packet for the enrich skill.")
    message_id = _send(runner, frame)
    runner.wait_for(lambda r: message_id in r.acked)

    runner.reconnect()
    assert runner.protocol_version == 2
    _send(runner, frame)
    runner.wait_for(lambda r: r.ack_log.count(message_id) >= 2)

    rows = _owner(db, "SELECT kind, payload FROM run_events WHERE message_id = %s", (message_id,))
    assert len(rows) == 1
    assert rows[0][0] == "log"
    assert rows[0][1]["seq"] == 1
    assert rows[0][1]["text"] == "Reading the packet for the enrich skill."


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
async def test_unacked_commands_resent_on_register(
    workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls, fake_runner: FakeRunnerFactory
) -> None:
    """T-P2-07-05
    A `run` queued while the daemon was away is delivered on its next register, as a
    version-2 `run` on a protocol-2 session, once: after the daemon's batched ack the
    mailbox row is `acked` and a further reconnect delivers nothing again.
    """
    runner = _v2_runner(fake_runner)
    runner.disconnect()
    packet, _handle, _profile = await _dispatch(workspace, clock, fake_runner, runner, wait=False)
    mailbox = _owner(
        db,
        "SELECT status FROM runner_messages WHERE type = 'run' AND payload->>'run_id' = %s",
        (str(packet.run_id),),
    )
    assert mailbox == [("queued",)]

    runner.connect()
    runner.wait_for(lambda r: any(run.run_id == packet.run_id for run in r.runs()))
    delivered = [run for run in runner.runs() if run.run_id == packet.run_id]
    assert len(delivered) == 1
    assert int(delivered[0].schema_version) == 2

    deadline = time.monotonic() + 5
    while _owner(
        db,
        "SELECT status FROM runner_messages WHERE message_id = %s",
        (delivered[0].message_id,),
    ) != [("acked",)]:
        assert time.monotonic() < deadline, "the run's mailbox row never turned acked"
        await asyncio.sleep(0.05)

    runner.reconnect()
    await asyncio.sleep(0.5)
    assert len([run for run in runner.runs() if run.run_id == packet.run_id]) == 1


@pytest.mark.req("REL-4")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
async def test_cancel_on_v1_daemon_falls_back(
    workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls, fake_runner: FakeRunnerFactory
) -> None:
    """T-P2-07-12
    A protocol-1 runner gets no `cancel` (it would not understand one): the server marks
    the run `cancelled` itself ("stop requested, runner is an older version"), and the
    result the old daemon sends when its run ends is acked and ignored.
    """
    from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
        DaemonTransport,
        HermesAgent,
    )
    from tumnis.modules.agents.protocol import Result  # noqa: PLC0415

    runner = fake_runner(profiles=["acme-site"])  # protocol 1, nothing scripted
    assert runner.protocol_version == 1
    packet, handle, profile_id = await _dispatch(workspace, clock, fake_runner, runner)
    await HermesAgent(profile_id, DaemonTransport(workspace.ctx, clock)).cancel(handle)

    rows = _owner(db, "SELECT status, error FROM runs WHERE id = %s", (packet.run_id,))
    assert rows[0][0] == "cancelled"
    assert "older version" in (rows[0][1] or "")
    await asyncio.sleep(0.3)
    assert not [m for m in runner.received if str(m.type) == "cancel"]

    late = Result(
        message_id=uuid.uuid4(),
        correlation_id=packet.correlation_id,
        sent_at=clock.now(),
        run_id=packet.run_id,
        status="succeeded",
        exit_code=0,
        output_json={"estimate_minutes": 20},
        text='{"estimate_minutes": 20}',
        duration_ms=1000,
    )
    runner.send(late)
    runner.wait_for(lambda r: late.message_id in r.acked)
    assert _owner(db, "SELECT status FROM runs WHERE id = %s", (packet.run_id,)) == [("cancelled",)]
    results = _owner(
        db,
        "SELECT count(*) FROM run_events WHERE run_id = %s AND kind = 'result'",
        (packet.run_id,),
    )
    assert results == [(0,)]


@pytest.mark.req("REL-4")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
@pytest.mark.parametrize("protocol", [1, 2])
async def test_ack_style_follows_session_protocol(
    protocol: int, workspace: WorkspaceHandle, clock: FixedClock, fake_runner: FakeRunnerFactory
) -> None:
    """T-P2-07-16
    A protocol-1 session (an older daemon) gets exactly one `ack{ack_of}` per message; a
    protocol-2 session gets batched `ack{message_ids}` (version 2, at most 50 ids each),
    so five quick messages take fewer than five ack frames.
    """
    runner = fake_runner(profiles=["acme-site"], connect=False)
    if protocol == 1:
        registered = runner.connect(protocol_versions=[1, 2])
    else:
        registered = runner.connect(protocol_versions=[1, 2], capabilities=V2_CAPABILITIES)
    assert registered.protocol_version == protocol
    runner.wait_for(lambda r: bool(r.acked))  # the register's ack
    runner.ack_frames.clear()

    for _ in range(5):
        runner.heartbeat()
    ours = {m.message_id for m in runner.sent if m.type == "heartbeat"}
    runner.wait_for(lambda r: ours <= r.acked)
    frames = [f for f in runner.ack_frames if (set(_ids(f)) & ours)]
    if protocol == 1:
        assert all(f.schema_version == 1 for f in frames)
        assert sorted(f.ack_of for f in frames) == sorted(ours)
    else:
        assert all(f.schema_version == 2 for f in frames)
        assert all(1 <= len(f.message_ids) <= 50 for f in frames)
        assert set().union(*(set(f.message_ids) for f in frames)) >= ours
        assert len(frames) < 5


def _ids(frame: Any) -> list[uuid.UUID]:
    return [frame.ack_of] if hasattr(frame, "ack_of") else list(frame.message_ids)


@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
async def test_status_message_records_profile_version(
    workspace: WorkspaceHandle, clock: FixedClock, db: DbUrls, fake_runner: FakeRunnerFactory
) -> None:
    """T-P2-07-17
    A `status` message (state `started`) carrying the profile's `VERSION` sets
    `runs.profile_version` for its run and the profile's own `profile_version`.
    """
    runner = _v2_runner(fake_runner)
    packet, _handle, profile_id = await _dispatch(workspace, clock, fake_runner, runner)
    status = _frame(
        "status",
        packet.run_id,
        profile="acme-site",
        profile_version="1.4.0",
        state="started",
        detail=None,
    )
    message_id = _send(runner, status)
    runner.wait_for(lambda r: message_id in r.acked)

    assert _owner(db, "SELECT profile_version FROM runs WHERE id = %s", (packet.run_id,)) == [
        ("1.4.0",)
    ]
    assert _owner(
        db, "SELECT profile_version FROM agent_profiles WHERE id = %s", (profile_id,)
    ) == [("1.4.0",)]
