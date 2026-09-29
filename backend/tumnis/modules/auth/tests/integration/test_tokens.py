"""Task tokens live as long as their run; device tokens rotate on reissue (P0-14, R-27,
R-28, FR-14.10)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tests._keys import CANARY_PREFIX, bearer_client, canary_test_app

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, MasterKeyFile, PepperFile, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
def app(  # noqa: PLR0917
    db: DbUrls,
    dbos_sys_db: DbUrls,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    pepper_file: PepperFile,
) -> FastAPI:
    return canary_test_app(db, dbos_sys_db, clock, str(pepper_file.path))


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P0-14")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_task_token_lives_until_its_run_ends(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-14-14
    A task token's scopes must be a subset of the issuing key's (ScopeEscalation
    otherwise). On its project the token works; on another project it is 404 `not_found`;
    a route needing a scope it lacks is 403 `insufficient_scope`. It still works after the
    clock passes the 60-minute run time cap while the run is open, and is 401 right after
    `revoke_task_tokens_for_run`.
    """
    from tumnis.modules.auth import api, tokens  # noqa: PLC0415

    key = await api.create_key(
        workspace.ctx,
        api.KeyIn(name="profile", scopes=["tasks:read", "tasks:write"]),
        now=clock.now(),
    )
    project, other = uuid.uuid4(), uuid.uuid4()
    run_id = uuid.uuid4()
    with pytest.raises(tokens.ScopeEscalation):
        await tokens.issue_task_token(
            workspace.ctx,
            run_id=run_id,
            project_id=project,
            api_key_id=key.id,
            scopes=frozenset({"tasks:read", "delegate"}),
            now=clock.now(),
        )
    token = await tokens.issue_task_token(
        workspace.ctx,
        run_id=run_id,
        project_id=project,
        api_key_id=key.id,
        scopes=frozenset({"tasks:read"}),
        now=clock.now(),
    )
    assert token.display.startswith("tmt_")
    base = f"/v1{CANARY_PREFIX}/projects"

    async with bearer_client(app, token.display) as client:
        own = await client.get(f"{base}/{project}")
        assert own.status_code == 200, own.text
        assert own.json()["principal"] == "task_token"
        elsewhere = await client.get(f"{base}/{other}")
        assert elsewhere.status_code == 404, elsewhere.text
        assert elsewhere.json()["code"] == "not_found"
        write = await client.post(f"{base}/{project}/write")
        assert write.status_code == 403, write.text
        assert write.json()["code"] == "insufficient_scope"

        clock.advance(hours=2)
        assert (await client.get(f"{base}/{project}")).status_code == 200

        revoked = await tokens.revoke_task_tokens_for_run(workspace.ctx, run_id, now=clock.now())
        assert revoked == 1
        after = await client.get(f"{base}/{project}")
        assert after.status_code == 401, after.text


@pytest.mark.req("FR-14.10")
@pytest.mark.wp("P0-14")
@pytest.mark.xfail(strict=True, reason="spec:P0-14")
async def test_device_token_rotates_on_reissue(
    app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P0-14-15
    A `tmd_` device token resolves to the runner's device principal; issuing again for the
    runner invalidates the previous token (even one already cached) and the new one works.
    """
    from tumnis.core.principal import AuthFailure, Principal  # noqa: PLC0415
    from tumnis.modules.auth import api, tokens  # noqa: PLC0415

    runner_id = uuid.uuid4()
    first = await tokens.issue_device_token(workspace.ctx, runner_id=runner_id, now=clock.now())
    assert first.display.startswith("tmd_")
    found = await api.authenticate_bearer(first.display, now=clock.now())
    assert isinstance(found, Principal)
    assert found.kind == "device"
    assert found.workspace_id == workspace.id
    assert found.subject_id == runner_id

    second = await tokens.issue_device_token(workspace.ctx, runner_id=runner_id, now=clock.now())
    assert isinstance(await api.authenticate_bearer(first.display, now=clock.now()), AuthFailure)
    renewed = await api.authenticate_bearer(second.display, now=clock.now())
    assert isinstance(renewed, Principal)
    assert renewed.subject_id == runner_id
