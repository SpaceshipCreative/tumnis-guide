"""`fake_runner` (P1-04, A5): an in-process runner daemon double speaking runner protocol 1
over `/ws/runner`, through Starlette's WebSocket test transport.

    runner = fake_runner(profiles=["acme-site"])          # a runner row, its device token,
    runner.script("acme-site", "enrich", {"estimate_minutes": 20}, delay_ms=0)   # connected
    profile_id = fake_runner.register_profile("acme-site", runner=runner)

It registers with its profiles, acks every server message (protocol 1), answers `run`
from its script `{(profile, skill): (output_json, delay_ms, status)}` (an unscripted skill
is acked and never answered), `health_check` from `script_health` and `provision` from
`script_provision(profile, status, error_code, delay_ms)` (P1-06; unscripted, a create
answers `created`, or `exists` for a profile it has, and a link `linked` or `failed`
with `not_found`; a profile it created or linked joins its profiles). The clock is fixed,
so nothing beats on its own: `heartbeat()` sends one beat, stamped by the server's clock.
`offline(profile)` stops answering for the profile and re-registers without it (the
server then refuses to dispatch to it); `disconnect()` drops the socket.

`fake_runner(..., strict=True)` (P2-02) behaves like an agent that uses its packet: it
validates every `run` packet against schemas/packet/v1/task_packet.json, refuses one
without `callback.task_token`, and calls `GET /v1/tasks` back with the token before it
answers. A refused packet or a failed callback answers `failed` and is listed in
`strict_failures`; `callbacks` holds each (run, callback status); `check_packet(packet)`
says why a packet would be refused (None when it would not).

The runner is sync (a reader thread per socket): call it from sync or async tests alike.
"""

from __future__ import annotations

import threading
import time
import uuid
import warnings
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.testclient import TestClient, WebSocketTestSession

    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.agents.protocol import (
        DaemonMessage,
        Provision,
        Registered,
        Run,
        ServerMessage,
    )

WAIT_S = 10.0
ResultStatus = Literal["succeeded", "failed", "timed_out"]
ProvisionStatus = Literal["created", "exists", "linked", "failed"]
ProvisionError = Literal["template_version_mismatch", "not_found", "hermes_error", "invalid_name"]


def make_test_client(app: FastAPI) -> TestClient:
    """Starlette's TestClient for `app` (not entered). Starlette warns that its client still
    runs on httpx; the warning is about the test transport, not about Tumnis."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "Using `httpx` with `starlette.testclient`")
        from starlette.testclient import TestClient  # noqa: PLC0415

    return TestClient(app, base_url="https://testserver")


def runner_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True)
class Scripted:
    output_json: dict[str, Any] | None
    delay_ms: int = 0
    status: ResultStatus = "succeeded"
    repeat: int = 1  # send the same result (same message_id) this many times


@dataclass(frozen=True)
class ScriptedProvision:
    status: ProvisionStatus = "created"
    error_code: ProvisionError | None = None
    delay_ms: int = 0


class FakeRunner:
    def __init__(
        self,
        client: TestClient,
        token: str,
        runner_id: uuid.UUID,
        *,
        name: str,
        profiles: Sequence[str],
        clock: FixedClock,
        hermes_version: str = "0.9.0",
        daemon_version: str = "0.1.0",
        strict: bool = False,
    ) -> None:
        self.client = client
        self.strict = strict  # P2-02: validate every packet and call back with its token
        self.strict_failures: list[str] = []
        self.callbacks: list[tuple[uuid.UUID, int]] = []  # (run, status of list_tasks)
        self.token = token
        self.runner_id = runner_id
        self.name = name
        self.profiles = list(profiles)
        self.clock = clock
        self.hermes_version = hermes_version
        self.daemon_version = daemon_version
        self.received: list[ServerMessage] = []
        self.sent: list[DaemonMessage] = []
        self.acked: set[uuid.UUID] = set()  # ids of this runner's messages the server acked
        self.ack_log: list[uuid.UUID] = []  # every id the server acked, repeats included
        self.ack_frames: list[Any] = []  # the ack frames themselves (per-message or batched)
        self.nacked: dict[uuid.UUID, str] = {}  # protocol 2: refused message id -> code
        self.protocol_version = 1  # the session's, from `registered`
        self.capabilities: list[str] = ["run", "provision", "health"]
        self.protocol_versions: list[int] = [1]
        self.errors: list[BaseException] = []
        self.registered: Registered | None = None
        self.ack_server_messages = True
        self._offline: set[str] = set()
        self._scripts: dict[tuple[str, str], Scripted] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._provisions: dict[str, ScriptedProvision] = {}
        self._seq = 0
        self._ws: WebSocketTestSession | None = None
        self._ws_cm: Any = None
        self._reader: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._changed = threading.Condition()
        self._timers: list[threading.Timer] = []

    # --- scripting -----------------------------------------------------------------------

    def script(
        self,
        profile: str,
        skill: str,
        output_json: dict[str, Any] | None,
        *,
        delay_ms: int = 0,
        status: ResultStatus = "succeeded",
        repeat: int = 1,
    ) -> None:
        self._scripts[(profile, skill)] = Scripted(output_json, delay_ms, status, repeat)

    def script_health(self, profile: str, **fields: Any) -> None:
        """Fields of the `health_report` for the profile (reachable, authenticated,
        hermes_version, mcp_servers, error, profile_exists)."""
        self._health[profile] = fields

    def script_provision(
        self,
        profile: str,
        status: ProvisionStatus = "created",
        error_code: ProvisionError | None = None,
        delay_ms: int = 0,
    ) -> None:
        """What a `provision` of `profile` answers from now on (P1-06); a long `delay_ms`
        is a runner that does not answer in time."""
        self._provisions[profile] = ScriptedProvision(status, error_code, delay_ms)

    def offline(self, profile: str) -> None:
        self._offline.add(profile)
        if self._ws is not None:
            self.reconnect()

    def online(self, profile: str) -> None:
        self._offline.discard(profile)
        if self._ws is not None:
            self.reconnect()

    # --- the socket ----------------------------------------------------------------------

    def _envelope(self, correlation_id: str) -> dict[str, Any]:
        return {
            "message_id": uuid.uuid4(),
            "correlation_id": correlation_id,
            "sent_at": self.clock.now(),
        }

    def send(self, message: DaemonMessage) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("the fake runner is not connected")
        with self._send_lock:
            ws.send_text(message.model_dump_json())
            self.sent.append(message)

    def send_raw(self, text: str) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("the fake runner is not connected")
        with self._send_lock:
            ws.send_text(text)

    def open(self) -> WebSocketTestSession:
        """The socket, handshake done, nothing sent yet."""
        self._ws_cm = self.client.websocket_connect(
            "/ws/runner", headers=runner_headers(self.token)
        )
        self._ws = self._ws_cm.__enter__()
        return self._ws

    def register_message(self, protocol_versions: Sequence[int] = (1,)) -> DaemonMessage:
        from tumnis.modules.agents.protocol import ProfileInfo, Register  # noqa: PLC0415

        return Register(
            **self._envelope(f"runner:{self.name}"),
            protocol_versions=list(protocol_versions),
            runner_name=self.name,
            host="hermes.example.org",
            os="linux",
            daemon_version=self.daemon_version,
            hermes_version=self.hermes_version,
            profiles=[ProfileInfo(name=p) for p in self.profiles if p not in self._offline],
            capabilities=list(self.capabilities),  # type: ignore[arg-type]
        )

    def connect(
        self,
        *,
        protocol_versions: Sequence[int] | None = None,
        capabilities: Sequence[str] | None = None,
    ) -> Registered:
        """Open the socket, register, wait for `registered`, then read in the background.
        The versions and capabilities stick: `reconnect` registers with them again
        (protocol 2 needs `[1, 2]` with `PROTOCOL_2_CAPABILITIES`)."""
        from tumnis.modules.agents.protocol import Registered, parse_server  # noqa: PLC0415

        if protocol_versions is not None:
            self.protocol_versions = list(protocol_versions)
        if capabilities is not None:
            self.capabilities = list(capabilities)
        ws = self.open()
        self.send(self.register_message(self.protocol_versions))
        first = parse_server(ws.receive_text())
        self._record(first)
        if not isinstance(first, Registered):
            raise AssertionError(f"expected registered, got {first!r}")
        self.registered = first
        self.protocol_version = first.protocol_version
        self.ack(first)
        self._reader = threading.Thread(target=self._read, args=(ws,), daemon=True)
        self._reader.start()
        return first

    def reconnect(self) -> Registered:
        self.disconnect()
        return self.connect()

    def disconnect(self) -> None:
        for timer in self._timers:
            timer.cancel()
        self._timers.clear()
        ws = self._ws
        if ws is not None and self._reader is not None and self._reader.is_alive():
            # Hang up as the daemon does and let the server close its side (the reader
            # ends on its close frame) before the test session is torn down: leaving the
            # session cancels the server's handler wherever it is, even mid-login to
            # Postgres, and that half-open login holds up the test database's drop.
            try:
                with self._send_lock:
                    ws.close(1000)
            except Exception as exc:  # the server closed first
                self.errors.append(exc)
            self._reader.join(WAIT_S)
        cm, self._ws, self._ws_cm = self._ws_cm, None, None
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:  # the server may have closed first
                self.errors.append(exc)
        if self._reader is not None:
            self._reader.join(WAIT_S)
            self._reader = None

    def heartbeat(self, running_run_ids: Sequence[uuid.UUID] = ()) -> None:
        from tumnis.modules.agents.protocol import Heartbeat  # noqa: PLC0415

        self._seq += 1
        self.send(
            Heartbeat(
                **self._envelope(f"runner:{self.name}"),
                seq=self._seq,
                running_run_ids=list(running_run_ids),
            )
        )

    # --- reading -------------------------------------------------------------------------

    def _record(self, message: ServerMessage) -> None:
        with self._changed:
            self.received.append(message)
            self._changed.notify_all()

    def ack(self, message: ServerMessage) -> None:
        """Ack a server message the way the session's protocol does: `ack{ack_of}` on
        protocol 1, `ack{message_ids}` (version 2) on protocol 2."""
        from tumnis.modules.agents.protocol import Ack  # noqa: PLC0415

        if not self.ack_server_messages:
            return
        if self.protocol_version >= 2:
            from tumnis.modules.agents.protocol import AckBatch  # noqa: PLC0415

            self.send(
                AckBatch(  # type: ignore[arg-type]
                    **self._envelope(message.correlation_id), message_ids=[message.message_id]
                )
            )
            return
        self.send(Ack(**self._envelope(message.correlation_id), ack_of=message.message_id))

    def _note_ack(self, message: Any) -> bool:
        """Record an ack or nack from the server; True when the frame was one."""
        ids: list[uuid.UUID] = []
        if message.type == "ack":
            ids = [message.ack_of] if hasattr(message, "ack_of") else list(message.message_ids)
        elif message.type == "nack":
            with self._changed:
                self.nacked[message.nack_of] = message.code
                self._changed.notify_all()
            return True
        else:
            return False
        with self._changed:
            self.ack_frames.append(message)
            self.acked.update(ids)
            self.ack_log.extend(ids)
            self._changed.notify_all()
        return True

    def _read(self, ws: WebSocketTestSession) -> None:
        from tumnis.modules.agents.protocol import (  # noqa: PLC0415
            HealthCheck,
            Provision,
            Run,
            parse_server,
        )

        while True:
            try:
                message = parse_server(ws.receive_text())
            except Exception:  # closed by either side: stop reading
                with self._changed:
                    self._changed.notify_all()
                return
            try:
                if self._note_ack(message):
                    self._record(message)
                    continue
                self._record(message)
                self.ack(message)
                if isinstance(message, Run):
                    self._answer_run(message)
                elif isinstance(message, HealthCheck):
                    self._answer_health(message)
                elif isinstance(message, Provision):
                    self._answer_provision(message)
            except Exception as exc:  # kept for the test to inspect
                self.errors.append(exc)

    def _answer_run(self, run: Run) -> None:
        from tumnis.modules.agents.protocol import Result  # noqa: PLC0415

        scripted = self._scripts.get((run.profile, run.skill))
        if scripted is None or run.profile in self._offline:
            return
        if self.strict:
            refused = self._strict_check(run)
            if refused is not None:
                self.strict_failures.append(refused)
                scripted = Scripted(None, scripted.delay_ms, "failed", scripted.repeat)
        result = Result(
            **self._envelope(run.correlation_id),
            run_id=run.run_id,
            status=scripted.status,
            exit_code=0 if scripted.status == "succeeded" else 1,
            output_json=scripted.output_json,
            text="" if scripted.output_json is None else str(scripted.output_json),
            error=None if scripted.status == "succeeded" else scripted.status,
            duration_ms=scripted.delay_ms,
            tokens={"input": 100, "output": 20},
            hermes_session_id=f"fake-{run.run_id}",
        )

        def answer() -> None:
            try:
                for _ in range(scripted.repeat):
                    self.send(result)
            except Exception as exc:  # the socket went away meanwhile
                self.errors.append(exc)

        if scripted.delay_ms <= 0:
            answer()
            return
        timer = threading.Timer(scripted.delay_ms / 1000, answer)
        timer.daemon = True
        self._timers.append(timer)
        timer.start()

    # --- strict mode (P2-02) -------------------------------------------------------------

    def check_packet(self, packet: dict[str, Any]) -> str | None:
        """Why strict mode refuses the packet (schema, or no task token), None if it is fine."""
        import json  # noqa: PLC0415

        from jsonschema import Draft202012Validator  # noqa: PLC0415

        from tests.fixtures import REPO_ROOT  # noqa: PLC0415

        schema = json.loads((REPO_ROOT / "schemas/packet/v1/task_packet.json").read_text())
        errors = sorted(e.message for e in Draft202012Validator(schema).iter_errors(packet))
        if errors:
            return f"packet does not validate: {errors[0]}"
        callback = packet.get("callback") or {}
        if not callback.get("task_token"):
            return "packet carries no task token"
        return None

    def _strict_check(self, run: Run) -> str | None:
        """Validates the packet, then calls `list_tasks` back with its token (the REST twin)
        the way an agent would; None when both are fine."""
        refused = self.check_packet(run.packet)
        if refused is not None:
            return refused
        token = run.packet["callback"]["task_token"]
        response = self.client.get("/v1/tasks", headers=runner_headers(token))
        self.callbacks.append((run.run_id, response.status_code))
        if response.status_code != 200:
            return f"list_tasks callback answered {response.status_code}"
        return None

    def _answer_health(self, check: Any) -> None:
        from tumnis.modules.agents.protocol import HealthReport  # noqa: PLC0415

        fields: dict[str, Any] = {
            "profile_exists": check.profile in self.profiles,
            "reachable": check.profile not in self._offline,
            "authenticated": True,
            "hermes_version": self.hermes_version,
            "mcp_servers": ["tumnis"],
            "error": None,
        }
        fields.update(self._health.get(check.profile, {}))
        self.send(
            HealthReport(
                **self._envelope(check.correlation_id),
                request_id=check.request_id,
                profile=check.profile,
                **fields,
            )
        )

    def _answer_provision(self, provision: Any) -> None:
        from tumnis.modules.agents.protocol import ProvisionResult  # noqa: PLC0415

        has = provision.profile in self.profiles
        scripted = self._provisions.get(provision.profile)
        if scripted is None:
            if provision.mode == "link":
                scripted = ScriptedProvision(
                    "linked" if has else "failed", None if has else "not_found"
                )
            else:
                scripted = ScriptedProvision("exists" if has else "created")
        ok = scripted.status != "failed"
        result = ProvisionResult(
            **self._envelope(provision.correlation_id),
            request_id=provision.request_id,
            profile=provision.profile,
            status=scripted.status,
            distribution_version=provision.template_version if ok else None,
            error_code=None if ok else scripted.error_code or "hermes_error",
            error=None if ok else f"scripted {scripted.error_code or 'failure'}",
        )

        def answer() -> None:
            try:
                if ok and provision.profile not in self.profiles:
                    self.profiles.append(provision.profile)
                self.send(result)
            except Exception as exc:  # the socket went away meanwhile
                self.errors.append(exc)

        if scripted.delay_ms <= 0:
            answer()
            return
        timer = threading.Timer(scripted.delay_ms / 1000, answer)
        timer.daemon = True
        self._timers.append(timer)
        timer.start()

    # --- waiting -------------------------------------------------------------------------

    def wait_for(self, predicate: Callable[[FakeRunner], bool], timeout: float = WAIT_S) -> None:
        deadline = time.monotonic() + timeout
        with self._changed:
            while not predicate(self):
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"fake runner: condition not met in {timeout} s")
                self._changed.wait(min(left, 0.05))

    def runs(self) -> list[Run]:
        from tumnis.modules.agents.protocol import Run  # noqa: PLC0415

        return [m for m in self.received if isinstance(m, Run)]

    def provisions(self) -> list[Provision]:
        from tumnis.modules.agents.protocol import Provision  # noqa: PLC0415

        return [m for m in self.received if isinstance(m, Provision)]


# --- Rows the runner needs, made through the agents api ------------------------------------


def create_runner(
    workspace: WorkspaceHandle, clock: FixedClock, name: str
) -> tuple[uuid.UUID, str]:
    """A runner row and its device token (shown once), as the workspace's owner would make
    them in Settings."""
    from tests._auth import run_async  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.agents import api  # noqa: PLC0415

    ctx = WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))

    async def make() -> tuple[uuid.UUID, str]:
        async with tenant_session(ctx) as s:
            created = await api.create_runner(ctx, s, api.RunnerIn(name=name), now=clock.now())
        return created.id, created.token

    return run_async(make)


def rotate_runner_token(workspace: WorkspaceHandle, clock: FixedClock, runner_id: uuid.UUID) -> str:
    from tests._auth import run_async  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.agents import api  # noqa: PLC0415

    ctx = WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))

    async def rotate() -> str:
        async with tenant_session(ctx) as s:
            rotated = await api.rotate_runner_token(ctx, s, runner_id, now=clock.now())
        return rotated.token

    return run_async(rotate)


def register_profile(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    name: str,
    *,
    role: Literal["master", "project"] = "project",
    runner_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> uuid.UUID:
    from tests._auth import run_async  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
    from tumnis.core.types import ActorRef  # noqa: PLC0415
    from tumnis.modules.agents import api  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    ctx = WorkspaceContext(workspace.id, ActorRef(f"user:{workspace.user_id}"))

    async def register() -> uuid.UUID:
        async with tenant_session(ctx) as s:
            project = project_id
            if role == "project" and project is None:  # a project profile names its project
                made = await projects.create_project(
                    s, ctx.actor, projects.ProjectCreate(name=f"{name} project"), now=clock.now()
                )
                project = made.id
            body = api.ProfileIn(
                name=name, role=role, transport="daemon", runner_id=runner_id, project_id=project
            )
            profile = await api.register_profile(s, body, now=clock.now())
        return profile.id

    return run_async(register)


class FakeRunnerFactory(Protocol):
    client: TestClient

    def __call__(
        self,
        profiles: Sequence[str] = ("tumnis-master",),
        *,
        name: str = "homelab-hermes",
        connect: bool = True,
        strict: bool = False,
    ) -> FakeRunner: ...

    def register_profile(
        self,
        name: str,
        *,
        runner: FakeRunner,
        role: Literal["master", "project"] = "project",
        project_id: uuid.UUID | None = None,
    ) -> uuid.UUID: ...


class _Factory:
    def __init__(self, client: TestClient, workspace: WorkspaceHandle, clock: FixedClock) -> None:
        self.client = client
        self.workspace = workspace
        self.clock = clock
        self.made: list[FakeRunner] = []

    def __call__(
        self,
        profiles: Sequence[str] = ("tumnis-master",),
        *,
        name: str = "homelab-hermes",
        connect: bool = True,
        strict: bool = False,
    ) -> FakeRunner:
        runner_id, token = create_runner(self.workspace, self.clock, name)
        runner = FakeRunner(
            self.client,
            token,
            runner_id,
            name=name,
            profiles=profiles,
            clock=self.clock,
            strict=strict,
        )
        self.made.append(runner)
        if connect:
            runner.connect()
        return runner

    def register_profile(
        self,
        name: str,
        *,
        runner: FakeRunner,
        role: Literal["master", "project"] = "project",
        project_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return register_profile(
            self.workspace,
            self.clock,
            name,
            role=role,
            runner_id=runner.runner_id,
            project_id=project_id,
        )


@pytest.fixture
def fake_runner(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> Iterator[FakeRunnerFactory]:
    """`fake_runner(profiles=[...], name="homelab-hermes")`: a runner made in `workspace`,
    connected to `app` (its lifespan runs for the test, so the mailbox forwarder listens)
    and registered with those profiles. `fake_runner.client` is the TestClient;
    `fake_runner.register_profile(name, runner=...)` adds an agent profile on it."""
    client = make_test_client(app)
    client.__enter__()
    factory = _Factory(client, workspace, clock)
    try:
        yield factory
    finally:
        for runner in factory.made:
            runner.disconnect()
        client.__exit__(None, None, None)
