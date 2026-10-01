"""FakeAgent (P1-04): the in-memory AgentAdapter for unit and Playwright runs, no socket.

Scripted per skill with `script(skill, output_json, status=..., delay_ms=...)`; an
unscripted skill answers `{}`. `offline()` makes every dispatch raise AgentUnavailable
until `online()`. `calls` records each dispatched packet.

In the compose.test stack (P2-04, R-37) it is scripted through `POST
/v1/test/fakes/runner/script`, stored in Postgres (tumnis.core.fake_scripts) because the
api and the worker are separate processes: `parse_runner_script` reads the phase 1 body
(a recorded answer per profile and skill) and the phase 2 body (the steps of each run of
a task, by title). While the store is enabled, each dispatch goes to the hooks registered
with `on_dispatch`: `agents.fake_play` records it as the last packet and plays the task's
steps.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tumnis.core import fake_scripts
from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.agents.adapters.port import (
    AgentCapabilities,
    AgentHealth,
    AgentUnavailable,
    RunEvent,
    RunHandle,
)
from tumnis.modules.agents.packet_builder import TaskPacket

ScriptedStatus = Literal["succeeded", "failed", "timed_out"]


MAX_STEP_TEXT: Final = 4000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Phase1Script(_Strict):
    """A recorded answer for a profile and skill (phase 1: `runnerScript(profile, skill,
    result)`, `recordings/runner/<result>`)."""

    profile: str = Field(min_length=1, max_length=64)
    skill: str = Field(min_length=1, max_length=64)
    result: str = Field(min_length=1, max_length=255)
    delay_ms: int = Field(default=0, ge=0, le=60_000)


class StreamStep(_Strict):
    kind: Literal["log", "tool_call", "file_touched"]
    text: str = Field(min_length=1, max_length=MAX_STEP_TEXT)


class ArtifactStep(_Strict):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(max_length=255)
    content: str


class AskHumanStep(_Strict):
    """A question to the human, asked through `ask_human` as the run's agent; the run's
    playback waits for the answer (Scott decision 55)."""

    prompt: str = Field(min_length=1, max_length=MAX_STEP_TEXT)
    choices: list[Annotated[str, Field(min_length=1, max_length=500)]] | None = Field(
        default=None, max_length=20
    )


class ResultStep(BaseModel):
    """The result the run posts; the whole body is checked as `post_result`'s input when
    it is played (the same validation as any agent's result)."""

    model_config = ConfigDict(extra="allow", frozen=True)

    outcome: Literal["done", "partial", "blocked"]
    summary: str = Field(min_length=1, max_length=20_000)


class Step(_Strict):
    """One thing a run does: exactly one of a stream line, an artifact, a question to the
    human or its result. A result's summary may name the last answer as `{answer}`.
    Approvals (`request_approval`) are not played: refused."""

    stream: StreamStep | None = None
    upload_artifact: ArtifactStep | None = None
    ask_human: AskHumanStep | None = None
    result: ResultStep | None = None

    @model_validator(mode="after")
    def _one_action(self) -> Self:
        given = [self.stream, self.upload_artifact, self.ask_human, self.result]
        if sum(step is not None for step in given) != 1:
            raise ValueError(
                "a step names exactly one of stream, upload_artifact, ask_human or result"
            )
        return self


class TaskScript(_Strict):
    """What the fake runner does in each run of the task titled `task_title` (phase 2,
    `fakes.runner.script(taskTitle, runs)`): `runs[n]` is the (n+1)th run's steps."""

    task_title: str = Field(min_length=1, max_length=500)
    runs: list[list[Step]] = Field(max_length=20)


DispatchHook = Callable[[TaskPacket], Awaitable[None]]
_dispatch_hooks: list[DispatchHook] = []


def on_dispatch(hook: DispatchHook) -> None:
    """Call `hook` with every packet a FakeAgent dispatches while the fake-script store is
    enabled. `agents.fake_play` registers the scripted playback here when it is imported
    (it reads the agents api, which this package must not import)."""
    if hook not in _dispatch_hooks:
        _dispatch_hooks.append(hook)


def task_script_key(title: str) -> str:
    return f"task:{title}"


# --- Phase 1 playback (SEED, R-37): a recording fitted to the packet ------------------------

# The runner recordings a phase 1 script names (`result`), kept with the backend's tests
# (and so in the image: only the build's caches are excluded).
RECORDINGS_DIR: Final = Path(__file__).resolve().parents[4] / "tests" / "fakes" / "recordings"
RUNNER_RECORDINGS: Final = RECORDINGS_DIR / "runner"
# A recorded enrichment reply whose `task_id` is this answers for the packet's own task.
TASK_ID_SENTINEL: Final = "00000000-0000-0000-0000-000000000000"
# A recorded plan reply names a task `title:<task title>`; one no candidate has gets this id.
TITLE_PREFIX: Final = "title:"
UNKNOWN_TASK_ID: Final = "0199ffff-0000-7000-8000-00000000beef"


def phase_1_key(profile: str, skill: str) -> str:
    """The match key of a phase 1 script (`parse_runner_script`)."""
    return f"{profile}/{skill}"


def recorded_reply(name: str) -> dict[str, Any] | None:
    """The JSON of the runner recording called `name`: a bare file name inside
    RUNNER_RECORDINGS; None for a recording of JSON `null` (a reply with no JSON, such as
    `plan__no_json`). A path, a parent reference or a missing file raises ValueError, so
    a posted script never reads outside the folder."""
    if not name or name in {".", ".."} or Path(name).name != name or "\\" in name:
        raise ValueError(f"not a recording name: {name!r}")
    path = RUNNER_RECORDINGS / name
    if not path.is_file():
        raise ValueError(f"no runner recording named {name!r}")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if loaded is None:
        return None
    if not isinstance(loaded, dict):
        raise ValueError(f"runner recording {name!r} is not a JSON object")
    return loaded


def load_recording(name: str) -> dict[str, Any]:
    """`recorded_reply` for a recording that holds a JSON object (ValueError otherwise)."""
    loaded = recorded_reply(name)
    if loaded is None:
        raise ValueError(f"runner recording {name!r} is not a JSON object")
    return loaded


def scripted_output(
    output: Mapping[str, Any] | None, packet: Mapping[str, Any]
) -> dict[str, Any] | None:
    """A recorded reply fitted to the packet it answers (the packet as JSON): a plan
    reply's `title:<title>` picks and alternates become the ids of the packet's candidates
    with those titles (UNKNOWN_TASK_ID when none has it); an enrichment reply's sentinel
    `task_id` becomes the packet's own task (`body.task.id`). Anything else is copied as
    it is; the recording itself is never changed."""
    if output is None:
        return None
    body = packet.get("body")
    body = body if isinstance(body, Mapping) else {}
    if isinstance(output.get("picks"), list):
        return _for_plan(output, body)
    if output.get("task_id") != TASK_ID_SENTINEL:
        return dict(output)
    task = body.get("task")
    own = task.get("id") if isinstance(task, Mapping) else None
    return {**output, "task_id": own if isinstance(own, str) else TASK_ID_SENTINEL}


def _for_plan(output: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    candidates = body.get("candidates")
    titles = {
        str(c.get("title")): str(c.get("task_id"))
        for c in (candidates if isinstance(candidates, list) else [])
        if isinstance(c, Mapping)
    }

    def resolve(value: Any) -> Any:
        if isinstance(value, str) and value.startswith(TITLE_PREFIX):
            return titles.get(value.removeprefix(TITLE_PREFIX), UNKNOWN_TASK_ID)
        return value

    return {
        **output,
        "picks": [
            {**pick, "task_id": resolve(pick.get("task_id"))} if isinstance(pick, Mapping) else pick
            for pick in output["picks"]
        ],
        "alternates": [resolve(a) for a in output.get("alternates") or []],
    }


def parse_runner_script(body: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The fake runner's stored script (R-37, `POST /v1/test/fakes/runner/script`): the
    phase 1 body is keyed `<profile>/<skill>`, the phase 2 body `task:<title>` and stored
    with `played: 0` (how many of its runs the fake has played). Raises ValueError
    (pydantic's ValidationError) for anything else."""
    if "task_title" in body:
        script = TaskScript.model_validate(body)
        stored = script.model_dump(mode="json", exclude_none=True)
        return task_script_key(script.task_title), {**stored, "played": 0}
    phase_1 = Phase1Script.model_validate(body)
    return phase_1_key(phase_1.profile, phase_1.skill), phase_1.model_dump(mode="json")


@dataclass(frozen=True)
class _Script:
    output_json: dict[str, Any] | None
    status: ScriptedStatus
    delay_ms: int


class FakeAgent:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock: Clock = clock or SystemClock()
        self.calls: list[TaskPacket] = []
        self._scripts: dict[str, _Script] = {}
        self._offline_all = False
        self._offline: set[UUID] = set()
        self._events: dict[UUID, list[RunEvent]] = {}

    def script(
        self,
        skill: str,
        output_json: dict[str, Any] | None,
        *,
        status: ScriptedStatus = "succeeded",
        delay_ms: int = 0,
    ) -> None:
        """What a run of `skill` answers from now on."""
        self._scripts[skill] = _Script(output_json, status, delay_ms)

    def offline(self, profile_id: UUID | None = None) -> None:
        """Dispatch to `profile_id` (every profile when None) raises AgentUnavailable."""
        if profile_id is None:
            self._offline_all = True
        else:
            self._offline.add(profile_id)

    def online(self) -> None:
        self._offline_all = False
        self._offline.clear()

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            transport="fake",
            skills=frozenset({"enrich", "plan"}),
            supports_stream=False,
            supports_cancel=False,
        )

    async def dispatch(self, packet: TaskPacket) -> RunHandle:
        if self._offline_all or packet.profile_id in self._offline:
            raise AgentUnavailable(packet.profile_id, "offline")
        self.calls.append(packet)
        if fake_scripts.enabled():  # the compose.test stack's worker (R-37)
            for hook in _dispatch_hooks:
                await hook(packet)
        script = self._scripts.get(packet.skill, _Script({}, "succeeded", 0))
        if script.delay_ms:
            await asyncio.sleep(script.delay_ms / 1000)
        now = self.clock.now()
        result = {
            "status": script.status,
            "output_json": script.output_json,
            "error": None if script.status == "succeeded" else f"scripted {script.status}",
        }
        self._events[packet.run_id] = [
            RunEvent(
                run_id=packet.run_id,
                message_id=uuid5(packet.run_id, "dispatched"),
                kind="dispatched",
                payload={"skill": packet.skill},
                at=now,
            ),
            RunEvent(
                run_id=packet.run_id,
                message_id=uuid5(packet.run_id, "result"),
                kind="result",
                payload=result,
                at=now,
            ),
        ]
        return RunHandle(
            run_id=packet.run_id,
            profile_id=packet.profile_id,
            transport="fake",
            correlation_id=packet.correlation_id,
        )

    async def stream(self, run: RunHandle) -> AsyncIterator[RunEvent]:
        """The run's events: `dispatched`, then its one `result`; replayed for a finished
        run."""
        for event in self._events.get(run.run_id, []):
            yield event

    async def cancel(self, run: RunHandle) -> None:
        """Phase 1 has no cancel on the agent side; a finished run stays finished."""

    async def health(self) -> AgentHealth:
        return AgentHealth(status="ok", reachable=True, authenticated=True, version="fake")
