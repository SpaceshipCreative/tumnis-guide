"""The policy editor's routes (P2-05, FR-5.6, SEC-3): `GET /v1/projects/{id}/policy` reads
the project's approval policy and `PUT` replaces its gated and allowed lists at the version
read. A change emits `policy.changed` and writes a `policy.changed` audit row with the
lists before and after; a stale version is 409 with the policy as it is now; a class in
both lists is 422."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from tests._pg import OWNER

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _owner(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        return conn.execute(query.encode(), params).fetchall()


async def _project_and_policy(client: SessionClient) -> tuple[str, dict[str, Any]]:
    made = await client.post("/v1/projects", json={"name": "Policy case"})
    assert made.status_code == 201, made.text
    project_id: str = made.json()["id"]
    read = await client.get(f"/v1/projects/{project_id}/policy")
    assert read.status_code == 200, read.text
    policy: dict[str, Any] = read.json()
    return project_id, policy


def _moved(policy: dict[str, Any], action: str) -> dict[str, Any]:
    """The policy's lists with `action` moved from allowed to gated."""
    return {
        "gated": [*policy["gated"], action],
        "allowed": [a for a in policy["allowed"] if a != action],
        "version": policy["version"],
    }


@pytest.mark.req("FR-5.6", "SEC-3")
@pytest.mark.wp("P2-05")
async def test_put_policy_moves_a_class_emits_and_audits(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """Moving `trigger_preview_deploy` to gated answers the new policy at the next version,
    emits one `policy.changed` with before and after, and writes one `policy.changed` audit
    row targeting the project with both lists in its details."""
    project_id, policy = await _project_and_policy(session_client)
    assert "trigger_preview_deploy" in policy["allowed"]

    body = _moved(policy, "trigger_preview_deploy")
    saved = await session_client.put(f"/v1/projects/{project_id}/policy", json=body)
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["version"] == policy["version"] + 1
    assert "trigger_preview_deploy" in out["gated"]
    assert "trigger_preview_deploy" not in out["allowed"]
    reread = await session_client.get(f"/v1/projects/{project_id}/policy")
    assert reread.json() == out

    events = _owner(db, "SELECT payload FROM outbox WHERE name = 'policy.changed'")
    assert len(events) == 1
    payload = events[0][0]
    assert payload["project_id"] == project_id
    assert payload["before"]["gated"] == policy["gated"]
    assert payload["after"]["gated"] == out["gated"]
    assert payload["after"]["allowed"] == out["allowed"]

    rows = _owner(
        db,
        "SELECT target_type, target_id::text, details FROM audit_log"
        " WHERE action = 'policy.changed'",
    )
    assert len(rows) == 1
    target_type, target_id, details = rows[0]
    assert (target_type, target_id) == ("project", project_id)
    assert details["before"]["allowed"] == policy["allowed"]
    assert details["after"]["gated"] == out["gated"]


@pytest.mark.req("FR-5.6", "REL-2")
@pytest.mark.wp("P2-05")
async def test_put_policy_stale_version_answers_current(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """A second save at the version the first one used is 409 `stale_version` whose
    `current` is the saved policy; nothing more is emitted or audited."""
    project_id, policy = await _project_and_policy(session_client)
    first = await session_client.put(
        f"/v1/projects/{project_id}/policy", json=_moved(policy, "open_pull_request")
    )
    assert first.status_code == 200, first.text

    stale = await session_client.put(
        f"/v1/projects/{project_id}/policy", json=_moved(policy, "create_draft")
    )
    assert stale.status_code == 409, stale.text
    problem = stale.json()
    assert problem["code"] == "stale_version"
    assert problem["current"] == first.json()
    assert _owner(db, "SELECT count(*) FROM outbox WHERE name = 'policy.changed'") == [(1,)]
    assert _owner(db, "SELECT count(*) FROM audit_log WHERE action = 'policy.changed'") == [(1,)]


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
async def test_put_policy_class_in_both_lists_is_422(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """A class in both gated and allowed is 422 `policy_conflict` naming it; the policy
    keeps its version."""
    project_id, policy = await _project_and_policy(session_client)
    body = {
        "gated": [*policy["gated"], "read"],
        "allowed": policy["allowed"],
        "version": policy["version"],
    }
    response = await session_client.put(f"/v1/projects/{project_id}/policy", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "policy_conflict"
    assert "read" in response.json()["detail"]
    reread = await session_client.get(f"/v1/projects/{project_id}/policy")
    assert reread.json()["version"] == policy["version"]


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P2-05")
async def test_put_policy_malformed_class_is_422(
    app: FastAPI, session_client: SessionClient, workspace: WorkspaceHandle, db: DbUrls
) -> None:
    """A class name that is not lowercase letters, digits and underscores is 422
    `invalid_action_class`; the policy keeps its version."""
    project_id, policy = await _project_and_policy(session_client)
    body = {
        "gated": [*policy["gated"], "Send Email!"],
        "allowed": policy["allowed"],
        "version": policy["version"],
    }
    response = await session_client.put(f"/v1/projects/{project_id}/policy", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "invalid_action_class"
    reread = await session_client.get(f"/v1/projects/{project_id}/policy")
    assert reread.json()["version"] == policy["version"]
