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
  as a daemon would: a stream line or an artifact becomes a run event, the result goes
  through `api.accept_result`, the one result path (never a direct DBOS send).

Pacing: the steps follow one another STEP_PAUSE_S apart, and a result waits RESULT_HOLD_S
first, so the run view shows a running run (its lines, its ticking clock, its Stop
button) before the result ends it. The hold is sized for A2.1, which checks those within
Playwright's 5 s expect window and then waits at most 5 s for In review.

The playback is not durable: a worker that dies mid-script loses the rest (a fake).
"""

import asyncio
import hashlib
import logging
from typing import Any, Final
from uuid import UUID, uuid5

from tumnis.core import fake_scripts
from tumnis.core.clock import SystemClock
from tumnis.core.errors import ProblemError
from tumnis.core.tenancy import WorkspaceContext, current, tenant_session
from tumnis.core.types import ActorRef
from tumnis.core.versioning import NotFound
from tumnis.modules.agents import api
from tumnis.modules.agents.adapters.fake import TaskScript, on_dispatch, task_script_key
from tumnis.modules.agents.packet_builder import TaskPacket
from tumnis.modules.agents.rules import artifact_refusal
from tumnis.modules.tasks import api as tasks

_log = logging.getLogger(__name__)

STEP_PAUSE_S: float = 0.1
RESULT_HOLD_S: float = 4.0
# The fake runner speaks as a device, as a real runner's handler does (`device:<runner>`);
# it has no runner row, so the nil id.
FAKE_RUNNER_ACTOR: Final = ActorRef(f"device:{UUID(int=0)}")

_playing: set["asyncio.Task[None]"] = set()  # strong references until each playback ends


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
    for index, step in enumerate(steps):
        message_id = uuid5(run_id, f"fake-step:{index}")
        if "result" in step:
            await asyncio.sleep(RESULT_HOLD_S)
            await _result(ctx, run_id, step["result"])
            return
        await asyncio.sleep(STEP_PAUSE_S)
        if "stream" in step:
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


async def _result(ctx: WorkspaceContext, run_id: UUID, result: dict[str, Any]) -> None:
    """The run's result through `api.accept_result`, as the runner's handler posts a
    dispatch_run run's result; a run already ended keeps its log and nothing else."""
    inp = api.PostResultIn.model_validate({**result, "run_id": str(run_id)})
    try:
        async with tenant_session(ctx) as s:
            await api.accept_result(s, ctx.actor, run_id, inp, now=SystemClock().now())
    except ProblemError as exc:
        _log.warning("the fake runner's result was not accepted", extra={"code": exc.code})


on_dispatch(dispatched)
