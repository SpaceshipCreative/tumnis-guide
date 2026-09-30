"""Fixtures for the MCP meta-tests (P2-01); the shared ones (app, clock, db, workspace,
key_client, session_client) come from backend/tests/fixtures.

- `surface_app`: the shared `app` with its MCP session manager running.
- `world`: projects A and B with a Hybrid parent each (tests/_mcp.py `make_world`).
- `callers`: keys, a task token and the master key for the sweeps (tests/_mcp.py).
- `echo_v2_op`: a test-only op `_echo_v2` (schema_version 2, a v1 adapter renaming `text`
  to `message`), removed from the registry after the test (T-P2-01-11).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._mcp import Callers, World
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock


@pytest.fixture
async def surface_app(app: FastAPI) -> AsyncIterator[FastAPI]:
    from tests._mcp import mcp_running  # noqa: PLC0415

    async with mcp_running(app):
        yield app


@pytest.fixture
async def world(workspace: WorkspaceHandle, clock: FixedClock, app: FastAPI) -> World:
    from tests._mcp import make_world  # noqa: PLC0415

    return await make_world(workspace, clock)


@pytest.fixture
def callers(app: FastAPI, world: World) -> Iterator[Callers]:
    from tests._mcp import Callers  # noqa: PLC0415

    made = Callers(app, world)
    try:
        yield made
    finally:
        made.close()


@pytest.fixture
def echo_v2_op() -> Iterator[Any]:
    """`_echo_v2`: echoes `message`; a v1 call names it `text` and is upgraded."""
    from pydantic import BaseModel  # noqa: PLC0415

    from tumnis.core import agent_surface  # noqa: PLC0415

    class EchoIn(agent_surface.SurfaceInput):
        message: str

    class EchoOut(BaseModel):
        schema_version: int = 2
        message: str

    async def handler(_call: Any, data: BaseModel) -> BaseModel:
        assert isinstance(data, EchoIn)  # the registry validated it
        return EchoOut(message=data.message)

    def from_v1(raw: dict[str, Any]) -> dict[str, Any]:
        upgraded = {k: v for k, v in raw.items() if k != "text"}
        upgraded["message"] = raw.get("text", "")
        upgraded["schema_version"] = 2
        return upgraded

    op = agent_surface.SurfaceOp(
        name="_echo_v2",
        description="Test-only echo (schema version 2).",
        scope="tasks:read",
        input_model=EchoIn,
        output_model=EchoOut,
        rest_method="POST",
        rest_path="/v1/_test/echo-v2",
        write=False,
        updates_existing=False,
        project_arg=None,
        project_resolver=None,
        handler=handler,
        schema_version=2,
        previous_version_adapter=from_v1,
    )
    agent_surface.register_op(op)
    try:
        yield op
    finally:
        agent_surface.unregister_op(op.name)
