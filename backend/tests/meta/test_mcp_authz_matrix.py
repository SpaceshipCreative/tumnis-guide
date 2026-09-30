"""Who may call each tool, identical on both doors (P2-01, FR-14.10, R-28, R-34).

The expected outcome of every (op, caller kind) pair is computed from the op (`scope`,
`master_only`, whether it names a project), never written by hand, so an op registered
later is swept with no edit here. Each pair is asserted on MCP and on the REST twin, and
the two must give the same code (REST's missing credential is a 401 `unauthenticated`, and
so is MCP's).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._mcp import Callers, World

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

KINDS: Final = (
    "none",
    "no_scopes",
    "exact_scope",
    "all_but_scope",
    "limited_inside",
    "limited_outside",
    "task_token",
    "master",
    "delegate_not_master",
)


def _ops() -> list[Any]:
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    load_mcp()
    return agent_surface.ops()


def _project_scoped(op: Any) -> bool:
    return op.project_arg is not None or op.project_resolver is not None


def expected(op: Any, kind: str) -> str | None:
    """The code the pair must give; None when the call is allowed."""
    from tests._mcp import TOKEN_SCOPES  # noqa: PLC0415

    if kind == "none":
        return "unauthenticated"
    if kind in {"no_scopes", "all_but_scope"}:
        return "insufficient_scope"
    if kind == "task_token" and op.scope not in TOKEN_SCOPES:
        return "insufficient_scope"
    if kind == "limited_outside" and _project_scoped(op):
        return "not_found"
    if op.master_only and kind != "master":
        return "master_only"
    return None


async def _credential(  # noqa: PLR0911  # one return per caller kind
    callers: Callers, world: World, op: Any, kind: str
) -> str | None:
    from tests._mcp import ALL_SCOPES  # noqa: PLC0415

    scope = frozenset({op.scope})
    if kind == "none":
        return None
    if kind == "no_scopes":
        return (await callers.key(frozenset()))[0]
    if kind == "exact_scope":
        return (await callers.key(scope))[0]
    if kind == "all_but_scope":
        return (await callers.key(ALL_SCOPES - scope))[0]
    if kind == "limited_inside":
        return (await callers.key(scope, frozenset({world.projects["A"]})))[0]
    if kind == "limited_outside":
        return (await callers.key(scope, frozenset({world.projects["B"]})))[0]
    if kind == "task_token":
        return await callers.task_token("A")
    if kind == "master":
        return await callers.master_key()
    return (await callers.key(scope | {"delegate"}))[0]


async def _both_doors(app: FastAPI, key: str | None, op: Any, world: World) -> tuple[Any, Any]:
    from tests._mcp import SAMPLES, http_for, mcp_call, rest_call  # noqa: PLC0415

    assert op.name in SAMPLES, f"add a sample for {op.name} in tests/_mcp.py"
    async with http_for(app, key) as http:
        via_mcp = await mcp_call(http, op.name, await SAMPLES[op.name](world, "A"))
        via_rest = await rest_call(http, op, await SAMPLES[op.name](world, "A"))
    return via_mcp, via_rest


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
async def test_scope_matrix(surface_app: FastAPI, world: World, callers: Callers) -> None:
    """T-P2-01-05
    Every op x caller kind (none, a key with no scopes, exactly the op's scope, every scope
    but it, project-limited inside and outside, a task token, the master key, a non-master
    key with `delegate`) gives the expected allow or code (401 `unauthenticated`, 403
    `insufficient_scope`, 404 `not_found`, 403 `master_only`), identical on both doors."""
    ops = _ops()
    assert ops
    wrong = []
    for op in ops:
        for kind in KINDS:
            want = expected(op, kind)
            key = await _credential(callers, world, op, kind)
            via_mcp, via_rest = await _both_doors(surface_app, key, op, world)
            if via_mcp.code != want or via_rest.code != want:
                wrong.append(
                    f"{op.name} x {kind}: want {want}, mcp {via_mcp.code} {via_mcp.data!r:.200},"
                    f" rest {via_rest.code} {via_rest.data!r:.200}"
                )
    assert wrong == [], "\n".join(wrong)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
async def test_project_limited_key_outside_project_is_not_found(
    surface_app: FastAPI, world: World, callers: Callers
) -> None:
    """T-P2-01-06
    A key limited to project A gets 404 `not_found` for project B on every op that names
    a project, on both doors (R-28: existence never leaks)."""
    from tests._mcp import SAMPLES, http_for, mcp_call, rest_call  # noqa: PLC0415

    scoped = [op for op in _ops() if _project_scoped(op)]
    assert scoped, "no project-scoped op registered"
    wrong = []
    for op in scoped:
        key, _key_id = await callers.key(frozenset({op.scope}), frozenset({world.projects["A"]}))
        async with http_for(surface_app, key) as http:
            via_mcp = await mcp_call(http, op.name, await SAMPLES[op.name](world, "B"))
            via_rest = await rest_call(http, op, await SAMPLES[op.name](world, "B"))
        if (via_mcp.code, via_rest.code) != ("not_found", "not_found"):
            wrong.append(f"{op.name}: mcp {via_mcp.code}, rest {via_rest.code}")
    assert wrong == [], "\n".join(wrong)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
async def test_cookie_session_rejected_on_mcp(surface_app: FastAPI, session_client: Any) -> None:
    """T-P2-01-07
    `/mcp` with only a session cookie (and its CSRF token) answers 401
    `mcp_requires_bearer`, so a browser session can never reach a tool."""
    from tests._mcp import MCP_ACCEPT  # noqa: PLC0415

    response = await session_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Accept": MCP_ACCEPT, "Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "mcp_requires_bearer"
