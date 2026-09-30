"""The task token every dispatch hands its agent (P2-02, SAF-1, R-27, R-28, R-31).

Tokens are P0-14's (`auth/tokens.py`); P2-02 chooses their scopes per run kind
(`agents.rules.run_token_scopes`, a subset of the issuing profile key's), issues one per
dispatch (`agents.api.issue_run_token`) and ends them when the run ends.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.auth.tests.integration.test_revocation import _line, key_probe

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import PepperFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

ALL_SCOPES = frozenset(
    {
        "tasks:read",
        "tasks:write",
        "context:read",
        "knowledge:write",
        "drafts:write",
        "delegate",
        "ingest",
    }
)
RUN_KINDS = ("enrich", "plan", "task", "proposal", "stuck", "notify")
REFUSED_WITHIN_MS = 1_000


async def _key(workspace: WorkspaceHandle, clock: FixedClock, scopes: frozenset[str]) -> Any:
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    return await auth.create_key(
        workspace.ctx,
        auth.KeyIn(name=f"profile key {uuid.uuid4().hex[:6]}", scopes=sorted(scopes)),
        now=clock.now(),
    )


async def _token(
    workspace: WorkspaceHandle,
    clock: FixedClock,
    *,
    kind: str,
    project_id: uuid.UUID,
    api_key_id: uuid.UUID,
    run_id: uuid.UUID | None = None,
) -> str:
    from tumnis.modules.agents import api as agents  # noqa: PLC0415

    return await agents.issue_run_token(
        workspace.ctx,
        run_id=run_id or uuid.uuid4(),
        kind=agents.RunKind(kind),
        project_id=project_id,
        api_key_id=api_key_id,
        now=clock.now(),
    )


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
async def test_token_limited_to_its_project(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P2-02-10
    A task token for a run in project A reads A's task packet on both doors; the same
    token on project B's task is 404 `not_found` on REST and on MCP (R-28), for a read and
    for a write.
    """
    from tests._mcp import (  # noqa: PLC0415
        http_for,
        idem,
        make_world,
        mcp_call,
        mcp_running,
        rest_call,
    )
    from tumnis.core import agent_surface  # noqa: PLC0415

    world = await make_world(workspace, clock)
    key = await _key(workspace, clock, frozenset({"tasks:read", "tasks:write", "context:read"}))
    token = await _token(
        workspace, clock, kind="task", project_id=world.projects["A"], api_key_id=key.id
    )
    own = await world.task("A")
    other = await world.task("B")
    packet_op = agent_surface.get_op("get_task_packet")
    status_op = agent_surface.get_op("update_task_status")

    async with mcp_running(app), http_for(app, token) as http:
        mine_rest = await rest_call(http, packet_op, {"task_id": str(own.id)})
        mine_mcp = await mcp_call(http, "get_task_packet", {"task_id": str(own.id)})
        assert mine_rest.ok, mine_rest
        assert mine_mcp.ok, mine_mcp
        assert mine_rest.data["body"]["task"]["id"] == str(own.id)

        theirs_rest = await rest_call(http, packet_op, {"task_id": str(other.id)})
        theirs_mcp = await mcp_call(http, "get_task_packet", {"task_id": str(other.id)})
        assert (theirs_rest.status, theirs_rest.code) == (404, "not_found")
        assert theirs_mcp.code == "not_found"

        move = {"task_id": str(other.id), "to": "today", "version": other.version}
        write_rest = await rest_call(http, status_op, {**move, "idempotency_key": idem()})
        write_mcp = await mcp_call(http, "update_task_status", {**move, "idempotency_key": idem()})
        assert (write_rest.status, write_rest.code) == (404, "not_found")
        assert write_mcp.code == "not_found"


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
@pytest.mark.parametrize("kind", RUN_KINDS)
async def test_token_scopes_are_subset_of_key(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock, kind: str
) -> None:
    """T-P2-02-11
    For every RunKind the issued token's scopes equal `run_token_scopes(kind, key_scopes)`:
    a key holding every scope but `tasks:write` yields a token without it, and a write the
    token lacks the scope for is 403 `insufficient_scope`.
    """
    from tests._mcp import http_for, idem, make_world  # noqa: PLC0415
    from tumnis.core.principal import Principal  # noqa: PLC0415
    from tumnis.modules.agents.api import RunKind, run_token_scopes  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    world = await make_world(workspace, clock)
    project = world.projects["A"]
    for held in (ALL_SCOPES, ALL_SCOPES - {"tasks:write"}):
        key = await _key(workspace, clock, held)
        token = await _token(workspace, clock, kind=kind, project_id=project, api_key_id=key.id)
        principal = await auth.authenticate_bearer(token, now=clock.now())
        assert isinstance(principal, Principal)
        assert principal.kind == "task_token"
        assert principal.project_ids == frozenset({project})
        assert set(principal.scopes) == set(run_token_scopes(RunKind(kind), held))
        assert set(principal.scopes) <= held

    assert isinstance(principal, Principal)
    assert "tasks:write" not in principal.scopes
    async with http_for(app, token) as http:
        refused = await http.post(
            "/v1/tasks",
            json={"project_id": str(project), "title": "Not allowed", "label": "ai"},
            headers={"Idempotency-Key": idem()},
        )
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "insufficient_scope"


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
@pytest.mark.usefixtures("master_key_file")
async def test_token_fails_after_run_ends(
    app: FastAPI,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    pepper_file: PepperFile,
) -> None:
    """T-P2-02-12
    When the run ends in this process (`agents.api.run_ended`, which every finished run
    calls whatever its status), the run's token fails in a second process
    (P0-14's key probe, its own cache and invalidation listener) within one second, and
    here it is 401 `unauthenticated` with detail `token_expired`.
    """
    from tests._mcp import http_for, make_world  # noqa: PLC0415
    from tumnis.core.principal import AuthFailure  # noqa: PLC0415
    from tumnis.modules.agents import api as agents  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    world = await make_world(workspace, clock)
    key = await _key(workspace, clock, frozenset({"tasks:read", "context:read"}))
    run_id = uuid.uuid4()
    token = await _token(
        workspace,
        clock,
        kind="enrich",
        project_id=world.projects["A"],
        api_key_id=key.id,
        run_id=run_id,
    )

    async with key_probe(db, str(pepper_file.path), token) as process:
        await agents.run_ended(workspace.ctx, run_id, now=clock.now())
        line = await _line(process, 10.0)
    assert line is not None, "the probe never saw the token refused"
    verb, ms = line.split()
    assert verb == "refused"
    assert int(ms) < REFUSED_WITHIN_MS

    failure = await auth.authenticate_bearer(token, now=clock.now())
    assert isinstance(failure, AuthFailure)
    assert (failure.code, failure.detail) == ("unauthenticated", "token_expired")
    async with http_for(app, token) as http:
        answer = await http.get("/v1/tasks", params={"project_id": str(world.projects["A"])})
    assert answer.status_code == 401, answer.text
    assert answer.json()["code"] == "unauthenticated"
    assert "token_expired" in str(answer.json().get("detail"))


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
async def test_token_never_carries_delegate_or_ingest(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P2-02-13
    Even from a key holding every scope (the master's), a task token of any kind has
    neither `delegate` nor `ingest`.
    """
    from tests._mcp import make_world  # noqa: PLC0415
    from tumnis.core.principal import Principal  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    world = await make_world(workspace, clock)
    master = await _key(workspace, clock, ALL_SCOPES)
    for kind in RUN_KINDS:
        token = await _token(
            workspace, clock, kind=kind, project_id=world.projects["A"], api_key_id=master.id
        )
        principal = await auth.authenticate_bearer(token, now=clock.now())
        assert isinstance(principal, Principal)
        assert "delegate" not in principal.scopes, kind
        assert "ingest" not in principal.scopes, kind
        assert principal.scopes, kind
