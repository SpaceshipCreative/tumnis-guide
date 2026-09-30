"""Write rules on the agent surface, swept over every registered op (P2-01, FR-14.10,
REL-2, SAAS-1, R-31).

Every write takes an idempotency key (MCP: the `idempotency_key` argument; REST: the
`Idempotency-Key` header), every update names the version it read and a stale one is 409
`stale_version` with the current record, a retry replays the first answer, and a payload
at `schema_version` N or N-1 is accepted while anything else is 422
`unsupported_schema_version`. A write by a key with no run is tainted (R-31).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._mcp import Callers, World

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _ops() -> list[Any]:
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.wiring import load_mcp  # noqa: PLC0415

    load_mcp()
    return agent_surface.ops()


async def _writer(callers: Callers) -> str:
    from tests._mcp import ALL_SCOPES  # noqa: PLC0415

    return (await callers.key(ALL_SCOPES))[0]


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_keyless_writes_are_tainted(
    surface_app: FastAPI, world: World, callers: Callers
) -> None:
    """T-P2-01-21
    A task created by a key with no run is tainted, through either door; one created with
    a task token (bound to a run) is not. A default profile key holds only `tasks:read`
    and `context:read`, so it cannot write at all (R-31)."""
    from tests._mcp import SAMPLES, http_for, mcp_call, rest_call  # noqa: PLC0415
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.modules.auth.scopes import DEFAULT_PROFILE_SCOPES  # noqa: PLC0415

    _ops()
    create = agent_surface.get_op("create_task")
    key = await _writer(callers)
    async with http_for(surface_app, key) as http:
        via_mcp = await mcp_call(http, "create_task", await SAMPLES["create_task"](world, "A"))
        via_rest = await rest_call(http, create, await SAMPLES["create_task"](world, "A"))
    assert via_mcp.ok, via_mcp
    assert via_rest.ok, via_rest
    assert via_mcp.data["tainted"] is True
    assert via_rest.data["tainted"] is True

    token = await callers.task_token("A")
    async with http_for(surface_app, token) as http:
        with_run = await mcp_call(http, "create_task", await SAMPLES["create_task"](world, "A"))
    assert with_run.ok, with_run
    assert with_run.data["tainted"] is False

    assert frozenset({"tasks:read", "context:read"}) == DEFAULT_PROFILE_SCOPES
    profile_key, _key_id = await callers.key(DEFAULT_PROFILE_SCOPES)
    async with http_for(surface_app, profile_key) as http:
        for op in _ops():
            if op.write:
                refused = await mcp_call(http, op.name, await SAMPLES[op.name](world, "A"))
                assert refused.code == "insufficient_scope", (op.name, refused)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_write_tools_require_idempotency_key(
    surface_app: FastAPI, world: World, callers: Callers
) -> None:
    """T-P2-01-08
    Every write op called without its key fails with `idempotency_key_required`: on MCP
    without the argument, on REST without the header."""
    from tests._mcp import SAMPLES, http_for, mcp_call, rest_call  # noqa: PLC0415

    writes = [op for op in _ops() if op.write]
    assert writes
    async with http_for(surface_app, await _writer(callers)) as http:
        for op in writes:
            args = await SAMPLES[op.name](world, "A")
            args.pop("idempotency_key")
            via_mcp = await mcp_call(http, op.name, args)
            via_rest = await rest_call(http, op, args)
            assert via_mcp.code == "idempotency_key_required", (op.name, via_mcp)
            assert via_rest.code == "idempotency_key_required", (op.name, via_rest)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_update_tools_require_version_and_409_on_stale(
    surface_app: FastAPI, world: World, callers: Callers
) -> None:
    """T-P2-01-09
    Every update op needs `version` (422 `validation_error` without it), and a stale one
    answers 409 `stale_version` with the current record, on both doors."""
    from tests._mcp import SAMPLES, http_for, idem, mcp_call, rest_call  # noqa: PLC0415

    updates = [op for op in _ops() if op.updates_existing]
    assert updates
    async with http_for(surface_app, await _writer(callers)) as http:
        for op in updates:
            assert op.write, f"{op.name} updates a record but is not a write"
            args = await SAMPLES[op.name](world, "A")
            del args["version"]
            assert (await mcp_call(http, op.name, args)).code == "validation_error", op.name
            assert (await rest_call(http, op, args)).code == "validation_error", op.name

            for door in ("mcp", "rest"):
                args = await SAMPLES[op.name](world, "A")
                args["version"] += 7
                args["idempotency_key"] = idem()
                stale = (
                    await mcp_call(http, op.name, args)
                    if door == "mcp"
                    else await rest_call(http, op, args)
                )
                assert stale.code == "stale_version", (op.name, door, stale)
                assert stale.data["current"]["version"] == args["version"] - 7, (op.name, door)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_replay_returns_original_and_mismatch_rejected(
    surface_app: FastAPI, world: World, callers: Callers
) -> None:
    """T-P2-01-10
    On MCP, the same key and arguments return the first result (nothing is done twice);
    the same key with changed arguments is 422 `idempotency_mismatch`."""
    from tests._mcp import SAMPLES, http_for, mcp_call  # noqa: PLC0415

    writes = [op for op in _ops() if op.write]
    assert writes
    async with http_for(surface_app, await _writer(callers)) as http:
        for op in writes:
            args = await SAMPLES[op.name](world, "A")
            first = await mcp_call(http, op.name, args)
            again = await mcp_call(http, op.name, args)
            assert first.ok, (op.name, first)
            assert again.ok, (op.name, again)
            assert again.data == first.data, op.name
            changed = await SAMPLES[op.name](world, "A")
            changed["idempotency_key"] = args["idempotency_key"]
            mismatch = await mcp_call(http, op.name, changed)
            assert mismatch.code == "idempotency_mismatch", (op.name, mismatch)


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P2-01")
@pytest.mark.xfail(strict=True, reason="spec:P2-01")
async def test_schema_version_n_and_n_minus_1_accepted(
    surface_app: FastAPI, world: World, callers: Callers, echo_v2_op: Any
) -> None:
    """T-P2-01-11
    `_echo_v2` (schema_version 2, with a v1 adapter renaming `text` to `message`) accepts
    version 2, no version (the current one) and version 1 through the adapter; 3 and 0
    fail with `unsupported_schema_version`. Every registered op refuses its version + 1
    and version - 2 on both doors, and accepts its current version."""
    from tests._mcp import SAMPLES, http_for, mcp_call, rest_call  # noqa: PLC0415

    async with http_for(surface_app, await _writer(callers)) as http:
        current = await mcp_call(http, "_echo_v2", {"schema_version": 2, "message": "hi"})
        default = await mcp_call(http, "_echo_v2", {"message": "hi"})
        previous = await mcp_call(http, "_echo_v2", {"schema_version": 1, "text": "hi"})
        assert current.ok, current
        assert default.ok, default
        assert previous.ok, previous
        assert current.data["message"] == default.data["message"] == previous.data["message"]
        for bad in (3, 0):
            refused = await mcp_call(http, "_echo_v2", {"schema_version": bad, "message": "x"})
            assert refused.code == "unsupported_schema_version", (bad, refused)

        for op in _ops():
            if op.name == "_echo_v2":
                continue
            args = await SAMPLES[op.name](world, "A")
            args["schema_version"] = op.schema_version
            assert (await mcp_call(http, op.name, args)).ok, op.name
            for bad in (op.schema_version + 1, op.schema_version - 2):
                for door in ("mcp", "rest"):
                    args = await SAMPLES[op.name](world, "A")
                    args["schema_version"] = bad
                    got = (
                        await mcp_call(http, op.name, args)
                        if door == "mcp"
                        else await rest_call(http, op, args)
                    )
                    assert got.code == "unsupported_schema_version", (op.name, door, bad, got)
