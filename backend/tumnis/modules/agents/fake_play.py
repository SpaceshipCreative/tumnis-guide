"""The scripted fake runner's playback (P2-04, R-37; fakes mode only).

compose.test has no runner daemon: with fake adapters, a profile whose runner never
connected is served by the in-memory FakeAgent in the worker (`api.adapter_for`). When the
fake-script store is enabled, each dispatch comes here (`dispatched`):

- the packet is kept as the fake runner's last one, with its live task token, and counted
  (`GET /v1/test/fakes/runner/last-packet`); the token is redacted when the run ends
  (`api.finish_run_in`, `api.run_ended`);
- the script stored for the task's title (`fakes.runner.script(title, runs)`) gives this
  run's steps (the n-th run of the task plays `runs[n]`; a run past the list plays
  nothing and waits to be stopped), played in the background after the dispatch returns,
  as a daemon would: a stream line or an artifact becomes a run event; a question goes
  through `api.ask_human` as the run's agent, and the playback waits until it is answered
  and the run runs again (Scott decision 55); the result goes through
  `api.accept_result`, the one result path (never a direct DBOS send), with `{answer}`
  in its summary replaced by the last answer.

Pacing: the steps follow one another STEP_PAUSE_S apart, and a result waits RESULT_HOLD_S
first, so the run view shows a running run (its lines, its ticking clock, its Stop
button) before the result ends it. The hold is sized for A2.1, which checks those within
Playwright's 5 s expect window and then waits at most 5 s for In review.

Phase 1 (SEED, R-37): a `run_skill` dispatch (plan, enrich) to a profile the fake serves
(`api.fake_served`) comes to `dispatch_skill` instead of the daemon transport. It writes the
run's `runs` row and `dispatched` event as a daemon dispatch does (`record_dispatch`, no
mailbox row), then plays the script stored for `<profile>/<skill>`
(`fakes.runner.script(profile, skill, result)`): after the script's delay, the named
recording, fitted to the packet (`scripted_output`), is the run's `result` event and is
sent to the waiting workflow, as the api hands over a daemon's result. A test reset that
removes the run while the script's delay runs makes the fake report its runner lost, so the
workflow ends at once instead of holding its queue slot into the next test (T-SEED-23,
T-SEED-24). Without a script for the skill the fake answers at once with a failed result,
error `no_script: <profile>/<skill>` (Scott decision 74, T-SEED-20), instead of staying
silent for the run timeout.
Phase 1 dispatches never reach the phase 2 hook (`dispatched`): they are not `run` packets.

The playback is not durable: a worker that dies mid-script loses the rest (a fake).
"""

import asyncio
import contextvars
import hashlib
import logging
from typing import Any, Final
from uuid import UUID, uuid5

from dbos import DBOS
from pydantic import ValidationError
from sqlalchemy import Table, select

from tumnis.core import fake_scripts
from tumnis.core.clock import SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.tenancy import WorkspaceContext, current, tenant_session
from tumnis.core.types import ActorRef
from tumnis.core.versioning import NotFound
from tumnis.modules.agents import api
from tumnis.modules.agents.adapters.fake import (
    Phase1Script,
    TaskScript,
    on_dispatch,
    phase_1_key,
    recorded_reply,
    scripted_output,
    task_script_key,
)
from tumnis.modules.agents.adapters.hermes import record_dispatch
from tumnis.modules.agents.adapters.port import AgentUnavailable
from tumnis.modules.agents.models import AgentProfile, RunRow
from tumnis.modules.agents.packet_builder import TaskPacket
from tumnis.modules.agents.rules import artifact_refusal
from tumnis.modules.tasks import api as tasks

_log = logging.getLogger(__name__)

STEP_PAUSE_S: float = 0.1
RESULT_HOLD_S: float = 4.0
QUESTION_POLL_S: float = 0.5  # how often a question's playback re-reads the answer
DELAY_POLL_S: float = 0.5  # how often a phase 1 run checks it still exists while it waits
# The fake runner speaks as a device, as a real runner's handler does (`device:<runner>`);
# it has no runner row, so the nil id.
FAKE_RUNNER_ACTOR: Final = ActorRef(f"device:{UUID(int=0)}")

_OPEN: Final = frozenset({"running", "waiting_on_human"})  # a run that may still ask
_playing: set["asyncio.Task[None]"] = set()  # strong references until each playback ends
_profiles: Table = AgentProfile.__table__  # type: ignore[assignment]
_runs: Table = RunRow.__table__  # type: ignore[assignment]


async def dispatched(packet: TaskPacket) -> None:
    """Record the packet and start playing its task's script, if any (see the module)."""
    await fake_scripts.record_run_packet(packet.model_dump(mode="json"))
    ctx = current()
    title = await _task_title(ctx, packet) if ctx is not None else None
    if ctx is None or title is None:
        return
    steps = await _next_run_steps(title)
    if not steps:
        return
    playback = asyncio.get_running_loop().create_task(
        _play(WorkspaceContext(ctx.workspace_id, FAKE_RUNNER_ACTOR), packet.run_id, steps)
    )
    _playing.add(playback)
    playback.add_done_callback(_done)


def _done(playback: "asyncio.Task[None]") -> None:
    _playing.discard(playback)
    if not playback.cancelled() and playback.exception() is not None:
        _log.error("the fake runner's playback failed", exc_info=playback.exception())


async def _task_title(ctx: WorkspaceContext, packet: TaskPacket) -> str | None:
    """The title of the packet's task, which keys its script. The packet carries the task's
    id but not its bare title (the rendered text block may add acceptance criteria)."""
    task = packet.body.get("task")
    task_id = task.get("id") if isinstance(task, dict) else None
    if not isinstance(task_id, str):
        return None
    try:
        async with tenant_session(ctx) as s:
            return (await tasks.get_task(s, UUID(task_id))).title
    except NotFound:
        return None


async def _next_run_steps(title: str) -> list[dict[str, Any]]:
    """This run's steps from the task's script, counting the run as played (atomically, so
    two dispatches at once play two different runs)."""
    claimed = await fake_scripts.claim_play(fake_scripts.RUNNER, task_script_key(title))
    if claimed is None or "runs" not in claimed[0]:
        return []
    stored, played = claimed
    script = TaskScript.model_validate({k: v for k, v in stored.items() if k != "played"})
    if played >= len(script.runs):
        return []
    return [step.model_dump(mode="json", exclude_none=True) for step in script.runs[played]]


async def _play(ctx: WorkspaceContext, run_id: UUID, steps: list[dict[str, Any]]) -> None:
    seq = 0
    answer: str | None = None
    for index, step in enumerate(steps):
        message_id = uuid5(run_id, f"fake-step:{index}")
        if "result" in step:
            await asyncio.sleep(RESULT_HOLD_S)
            await _result(ctx, run_id, _with_answer(step["result"], answer))
            return
        await asyncio.sleep(STEP_PAUSE_S)
        if "ask_human" in step:
            answer = await _ask(ctx, run_id, step["ask_human"])
            if answer is None:  # refused, or the run ended while it waited
                return
        elif "stream" in step:
            seq += 1
            await _stream(ctx, run_id, message_id, seq, step["stream"])
        elif "upload_artifact" in step:
            await _artifact(ctx, run_id, message_id, step["upload_artifact"])


async def _stream(
    ctx: WorkspaceContext, run_id: UUID, message_id: UUID, seq: int, line: dict[str, Any]
) -> None:
    payload = {
        "type": "stream",
        "run_id": str(run_id),
        "seq": seq,
        "kind": line["kind"],
        "text": api.log_text(line["text"]),
        "ts": SystemClock().now().isoformat(),
    }
    async with tenant_session(ctx) as s:
        await api.record_run_event(
            s, run_id, message_id, api.STREAM_EVENT_KIND[line["kind"]], payload
        )


async def _artifact(
    ctx: WorkspaceContext, run_id: UUID, message_id: UUID, artifact: dict[str, Any]
) -> None:
    """Stored as the runner's handler stores an `upload_artifact`, after the same checks
    (the fake computes the size and digest a daemon would declare)."""
    content: str = artifact["content"]
    raw = content.encode("utf-8")
    sha256 = hashlib.sha256(raw).hexdigest()
    refusal = artifact_refusal(artifact["media_type"], content, len(raw), sha256, sha256)
    if refusal is not None:
        _log.warning("the fake runner's artifact was refused", extra={"code": refusal})
        return
    payload = {
        "type": "upload_artifact",
        "run_id": str(run_id),
        "name": artifact["name"],
        "media_type": artifact["media_type"],
        "size": len(raw),
        "sha256": sha256,
        "content": content,
    }
    async with tenant_session(ctx) as s:
        await api.record_run_event(s, run_id, message_id, "artifact", payload)


async def _ask(ctx: WorkspaceContext, run_id: UUID, question: dict[str, Any]) -> str | None:
    """The question through `api.ask_human`, as the run's agent asks with its task token;
    then re-sent with its id every QUESTION_POLL_S (as an agent re-sends after a `pending`
    long poll) until it is answered and the run runs again. None when the ask is refused
    or the run ends first (a stopped run's question stays pending) or is gone (a reset)."""
    inp = api.AskHumanIn.model_validate({**question, "run_id": str(run_id)})
    try:
        async with tenant_session(ctx) as s:
            out = await api.ask_human(
                s, ctx.actor, run_id, inp, caller_key=None, tainted=False, now=SystemClock().now()
            )
    except ProblemError as exc:
        _log.warning("the fake runner's question was refused", extra={"code": exc.code})
        return None
    again = inp.model_copy(update={"question_id": out.id})
    while True:
        await asyncio.sleep(QUESTION_POLL_S)
        async with tenant_session(ctx) as s:
            try:
                status = (await api.get_run(s, run_id)).status
            except NotFound:  # a test reset removed the run (T-SEED-26)
                return None
            if status not in _OPEN:
                return None
            out = await api.ask_human(
                s, ctx.actor, run_id, again, caller_key=None, tainted=False, now=SystemClock().now()
            )
        if out.status == "answered" and status == "running":
            return out.answer or ""


def _with_answer(result: dict[str, Any], answer: str | None) -> dict[str, Any]:
    """The scripted result with `{answer}` in its summary replaced by the last answer."""
    if answer is None:
        return result
    return {**result, "summary": str(result["summary"]).replace("{answer}", answer)}


async def _result(ctx: WorkspaceContext, run_id: UUID, result: dict[str, Any]) -> None:
    """The run's result through `api.accept_result`, as the runner's handler posts a
    dispatch_run run's result; a run already ended keeps its log and nothing else. A
    result invalid once `{answer}` is replaced (a summary past its limit) is logged."""
    try:
        inp = api.PostResultIn.model_validate({**result, "run_id": str(run_id)})
    except ValidationError:
        _log.warning("the fake runner's result was invalid", extra={"code": "invalid_result"})
        return
    try:
        async with tenant_session(ctx) as s:
            await api.accept_result(s, ctx.actor, run_id, inp, now=SystemClock().now())
    except ProblemError as exc:
        _log.warning("the fake runner's result was not accepted", extra={"code": exc.code})


# --- Phase 1: a `run_skill` dispatch (plan, enrich) played from its recording -------------


async def dispatch_skill(ctx: WorkspaceContext, packet: TaskPacket) -> None:
    """Take a `run_skill` dispatch for the fake (see the module): the run is written
    `running` with its `dispatched` event, then the profile's script for the skill, if
    any, plays in the background. AgentUnavailable for an unknown or paused profile, as
    the daemon transport refuses it."""
    async with tenant_session(ctx) as s:
        profile = (
            await s.execute(
                select(_profiles.c.name, _profiles.c.status).where(
                    _profiles.c.id == packet.profile_id, _profiles.c.deleted_at.is_(None)
                )
            )
        ).first()
        if profile is None:
            raise AgentUnavailable(packet.profile_id, "unknown profile")
        if profile.status == "paused":
            raise AgentUnavailable(packet.profile_id, "paused or without a runner")
        await record_dispatch(
            s,
            packet,
            now=SystemClock().now(),
            dispatched={"profile": profile.name, "skill": packet.skill, "runner_id": None},
        )
        workflow_id = await s.scalar(select(_runs.c.workflow_id).where(_runs.c.id == packet.run_id))
    stored = await fake_scripts.lookup(fake_scripts.RUNNER, phase_1_key(profile.name, packet.skill))
    try:
        script = Phase1Script.model_validate(stored) if stored is not None else None
    except ValidationError:  # the default ("") script, or a phase 2 one: not for this skill
        script = None
    if workflow_id is None:  # nothing waits on this run
        return
    if script is not None and (script.profile, script.skill) == (profile.name, packet.skill):
        work = _answer(ctx, packet, script, workflow_id)
    else:  # no script for this skill: an immediate failure (Scott decision 74, T-SEED-20)
        work = _refuse(ctx, packet, profile.name, workflow_id)
    # A fresh context: the playback outlives this step and must not act inside its workflow.
    playback = asyncio.get_running_loop().create_task(work, context=contextvars.Context())
    _playing.add(playback)
    playback.add_done_callback(_done)


async def _answer(
    ctx: WorkspaceContext, packet: TaskPacket, script: Phase1Script, workflow_id: str
) -> None:
    """The recording as the run's result, after the script's delay. A run a test reset
    removed during the delay, or before the answer, is told its runner is lost instead
    (T-SEED-23, T-SEED-24)."""
    run_id = packet.run_id
    if not await _wait_while_present(ctx, run_id, script.delay_ms / 1000):
        await _runner_lost(workflow_id, run_id)
        return
    try:
        output = scripted_output(recorded_reply(script.result), packet.model_dump(mode="json"))
    except ValueError:
        _log.warning("the fake runner has no usable recording for %s", packet.skill)
        return
    message = {
        "type": "result",
        "run_id": str(run_id),
        "status": "succeeded",
        "exit_code": 0,
        "output_json": output,
        "error": None,
    }
    await _deliver(ctx, workflow_id, run_id, message)


async def _refuse(
    ctx: WorkspaceContext, packet: TaskPacket, profile: str, workflow_id: str
) -> None:
    """No script for this skill: a failed result at once, as a runner that cannot run the
    skill answers (Scott decision 74), so the waiting workflow ends now instead of at its
    run timeout."""
    run_id = packet.run_id
    message = {
        "type": "result",
        "run_id": str(run_id),
        "status": "failed",
        "exit_code": 1,
        "output_json": None,
        "error": f"no_script: {profile}/{packet.skill}",
    }
    await _deliver(ctx, workflow_id, run_id, message)


async def _deliver(
    ctx: WorkspaceContext, workflow_id: str, run_id: UUID, message: dict[str, Any]
) -> None:
    """The fake's result as the run's `result` event (once per run) and the message the
    waiting `run_skill` receives (idempotent too), as the api hands over a daemon's
    result; a run a test reset removed is told its runner is lost instead."""
    message_id = uuid5(run_id, "fake-result")
    async with tenant_session(ctx) as s:
        present = await s.scalar(select(_runs.c.id).where(_runs.c.id == run_id))
        if present is not None:
            await api.record_run_event(s, run_id, message_id, "result", message)
    if present is None:
        await _runner_lost(workflow_id, run_id)
        return
    await DBOS.send_async(
        workflow_id, message, api.run_topic(run_id), idempotency_key=str(message_id)
    )


async def _wait_while_present(ctx: WorkspaceContext, run_id: UUID, delay_s: float) -> bool:
    """Wait `delay_s`, checking every DELAY_POLL_S that the run's row still exists. False
    as soon as it is gone (a test reset removed it), True once the delay has passed."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + delay_s
    while (remaining := deadline - loop.time()) > 0:
        await asyncio.sleep(min(DELAY_POLL_S, remaining))
        async with tenant_session(ctx) as s:
            if await s.scalar(select(_runs.c.id).where(_runs.c.id == run_id)) is None:
                return False
    return True


async def _runner_lost(workflow_id: str, run_id: UUID) -> None:
    """Tell the waiting `run_skill` its runner is lost, as the runner sweep does; it then
    ends at once (its run is gone, so its outcome step fails and the workflow errors)."""
    message = {"type": "result", "run_id": str(run_id), "status": api.RUNNER_LOST}
    lost = uuid5(run_id, "fake-runner-lost")
    await DBOS.send_async(workflow_id, message, api.run_topic(run_id), idempotency_key=str(lost))


on_dispatch(dispatched)
