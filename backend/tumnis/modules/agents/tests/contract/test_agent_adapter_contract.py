"""The AgentAdapter contract for each implementation (P1-04, FR-14.6, FR-5.11): the
FakeAgent, HermesAgent over the daemon transport with the fake runner connected, and
HermesAgent over the MCP endpoint transport against an in-process fake MCP server."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tumnis.modules.agents.tests.contract.base import (
    SCRIPTED_OUTPUT,
    SKILL,
    AgentAdapterContract,
    TakeOffline,
)

if TYPE_CHECKING:
    from tests.fakes.fake_runner import FakeRunner, FakeRunnerFactory
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.agents.adapters.fake import FakeAgent
    from tumnis.modules.agents.api import AgentAdapter

FAKE_PROFILE = uuid.UUID("01950000-0000-7000-8000-000000000301")
MCP_HOST = "fake-hermes.test"
MCP_PROFILE = "acme-site"


@pytest.mark.contract
@pytest.mark.req("FR-14.6")
@pytest.mark.wp("P1-04")
class TestFakeAgent(AgentAdapterContract):
    """T-P1-04-09
    AgentAdapterContract passes for FakeAgent (in memory, no socket).
    """

    impl = "fake"

    @pytest.fixture
    def profile_id(self) -> uuid.UUID:
        return FAKE_PROFILE

    @pytest.fixture
    def subject(self, fakes: Fakes) -> FakeAgent:
        fake: FakeAgent = fakes["agents.hermes"]
        fake.script(SKILL, SCRIPTED_OUTPUT)
        return fake

    @pytest.fixture
    def take_offline(self, subject: FakeAgent, profile_id: uuid.UUID) -> TakeOffline:
        async def go() -> None:
            subject.offline(profile_id)

        return go


@pytest.mark.contract
@pytest.mark.integration
@pytest.mark.enable_socket
@pytest.mark.req("FR-14.6")
@pytest.mark.wp("P1-04")
class TestHermesDaemonTransportWithFakeRunner(AgentAdapterContract):
    """T-P1-04-10
    AgentAdapterContract passes for HermesAgent(DaemonTransport) with the fake runner
    connected over /ws/runner: dispatch writes the mailbox row, the api forwards it to the
    runner, the runner's result comes back as a run event.
    """

    impl = "real"

    @pytest.fixture
    def runner(self, fake_runner: FakeRunnerFactory) -> FakeRunner:
        runner = fake_runner(profiles=["acme-site"])
        runner.script("acme-site", SKILL, SCRIPTED_OUTPUT)
        return runner

    @pytest.fixture
    def profile_id(self, fake_runner: FakeRunnerFactory, runner: FakeRunner) -> uuid.UUID:
        return fake_runner.register_profile("acme-site", runner=runner)

    @pytest.fixture
    def subject(
        self, workspace: WorkspaceHandle, clock: FixedClock, profile_id: uuid.UUID
    ) -> AgentAdapter:
        from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
            DaemonTransport,
            HermesAgent,
        )

        return HermesAgent(profile_id, DaemonTransport(workspace.ctx, clock))

    @pytest.fixture
    def take_offline(self, runner: FakeRunner) -> TakeOffline:
        async def go() -> None:
            runner.offline("acme-site")

        return go


def _fake_mcp_server() -> Any:
    """A FastMCP server with the one tool the transport calls: `run_skill(profile, skill,
    packet_json)` answers the scripted output as the result record's JSON text."""
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

    server = FastMCP("fake-hermes", stateless_http=True, json_response=True, host=MCP_HOST)

    @server.tool()
    def run_skill(profile: str, skill: str, packet_json: str) -> str:
        packet = json.loads(packet_json)
        ok = profile == MCP_PROFILE and skill == SKILL and packet["skill"] == SKILL
        return json.dumps(
            {
                "status": "succeeded" if ok else "failed",
                "output_json": SCRIPTED_OUTPUT if ok else None,
                "text": json.dumps(SCRIPTED_OUTPUT),
                "error": None if ok else "unknown profile or skill",
                "duration_ms": 5,
            }
        )

    return server


class _Endpoint:
    """The fake server's app on an in-process transport; `online = False` refuses every
    connection as a stopped server would."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.online = True

    def client(self) -> httpx.AsyncClient:
        if self.online:
            transport: httpx.AsyncBaseTransport = httpx.ASGITransport(app=self.app)
        else:

            def refuse(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("connection refused", request=request)

            transport = httpx.MockTransport(refuse)
        return httpx.AsyncClient(transport=transport, base_url=f"http://{MCP_HOST}")


@pytest.mark.contract
@pytest.mark.req("FR-5.11")
@pytest.mark.wp("P1-04")
@pytest.mark.xfail(strict=True, reason="spec:P1-04")
class TestHermesMcpEndpointTransport(AgentAdapterContract):
    """T-P1-04-11
    AgentAdapterContract passes for HermesAgent(McpEndpointTransport) against an
    in-process fake MCP server built with the MCP Python SDK (Streamable HTTP, one tool
    `run_skill(profile, skill, packet_json)`).
    """

    impl = "real"

    @pytest.fixture
    async def endpoint(self) -> AsyncIterator[_Endpoint]:
        server = _fake_mcp_server()
        app = server.streamable_http_app()
        async with server.session_manager.run():
            yield _Endpoint(app)

    @pytest.fixture
    def profile_id(self) -> uuid.UUID:
        return FAKE_PROFILE

    @pytest.fixture
    def subject(
        self, endpoint: _Endpoint, clock: FixedClock, profile_id: uuid.UUID
    ) -> AgentAdapter:
        from tumnis.modules.agents.adapters.hermes import (  # noqa: PLC0415
            HermesAgent,
            McpEndpointTransport,
        )

        factory: Callable[[], httpx.AsyncClient] = endpoint.client
        transport = McpEndpointTransport(
            f"http://{MCP_HOST}/mcp", profile=MCP_PROFILE, clock=clock, client_factory=factory
        )
        return HermesAgent(profile_id, transport)

    @pytest.fixture
    def take_offline(self, endpoint: _Endpoint) -> TakeOffline:
        async def go() -> None:
            endpoint.online = False

        return go
