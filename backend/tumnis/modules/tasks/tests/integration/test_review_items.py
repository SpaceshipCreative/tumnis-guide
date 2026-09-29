"""The review item interface (P0-18, R-03, FR-3.1): any module registers a kind and queues a
human decision through `tasks.api`; kinds are text validated by the registry; an open item
with the same dedupe key is the same item; the badge counts only what waits now."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.tasks.tests.conftest import owner_rows
from tumnis.modules.tasks.tests.integration import _testmod

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.tasks.tests.conftest import MakeProject

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
def conflict_kind() -> str:
    """`test_conflict`, registered once per process by the stand-in module."""
    from tumnis.modules.tasks import api  # noqa: PLC0415

    if _testmod.KIND not in api.review_kinds():
        _testmod.register()
    return _testmod.KIND


def _target() -> Any:
    from tumnis.modules.tasks.api import TargetRef  # noqa: PLC0415

    return TargetRef(type="folder_file", id=uuid.uuid4())


@pytest.mark.req("FR-3.1")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
async def test_any_module_can_add_a_review_item(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    conflict_kind: str,
    make_project: MakeProject,
    workspace: WorkspaceHandle,
    db: DbUrls,
) -> None:
    """T-P0-18-14
    The stand-in module's `test_conflict` kind: `add_review_item(kind, target=..., project_id
    =p, payload={"path": "a.md"}, dedupe_key="f1")` twice returns the same id and leaves one
    row whose `kind` is plain text. `GET /v1/review/kinds` lists the kind and its actions.
    """
    from tumnis.modules.tasks import api  # noqa: PLC0415

    project = await make_project()
    target = _target()
    first = await api.add_review_item(
        conflict_kind,
        target=target,
        project_id=project.id,
        payload={"path": "a.md"},
        dedupe_key="f1",
    )
    second = await api.add_review_item(
        conflict_kind,
        target=target,
        project_id=project.id,
        payload={"path": "a.md"},
        dedupe_key="f1",
    )
    assert first == second
    assert owner_rows(
        db,
        "SELECT id, kind, pg_typeof(kind)::text, target_type, target_id, project_id, payload,"
        " dedupe_key, workspace_id FROM review_items",
    ) == [
        (
            first,
            "test_conflict",
            "text",
            "folder_file",
            target.id,
            project.id,
            {"path": "a.md"},
            "f1",
            workspace.id,
        )
    ]

    kinds = await session_client.get("/v1/review/kinds")
    assert kinds.status_code == 200, kinds.text
    by_kind = {item["kind"]: item for item in kinds.json()["items"]}
    assert by_kind["test_conflict"]["actions"] == ["accept", "reject"]
    assert by_kind["test_conflict"]["owner_module"] == "testmod"
    assert by_kind["test_conflict"]["impact_scope"] == "project"


@pytest.mark.req("FR-3.1")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
async def test_badge_counts_only_unreviewed_unsnoozed(
    app: FastAPI,
    session_client: SessionClient,
    conflict_kind: str,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P0-18-15
    Given items open, decided, snoozed until now + 1 h, snoozed until now - 1 h and
    trashed, `GET /v1/review/count` returns 2 (the open one and the one whose snooze ended).
    """
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415
    from tumnis.modules.tasks import api  # noqa: PLC0415

    ids = {
        state: await api.add_review_item(
            conflict_kind, target=_target(), project_id=None, payload={"path": f"{state}.md"}
        )
        for state in ("open", "decided", "snoozed", "snooze_over", "trashed")
    }
    now = clock.now()
    updates = {
        "decided": ("decided_at = %s, decision = 'accept'", now),
        "snoozed": ("snoozed_until = %s", now + timedelta(hours=1)),
        "snooze_over": ("snoozed_until = %s", now - timedelta(hours=1)),
        "trashed": ("deleted_at = %s", now),
    }
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        for state, (assignment, value) in updates.items():
            conn.execute(
                f"UPDATE review_items SET {assignment} WHERE id = %s".encode(),  # noqa: S608
                (value, ids[state]),
            )

    counted = await session_client.get("/v1/review/count")
    assert counted.status_code == 200, counted.text
    assert counted.json() == {"count": 2}


@pytest.mark.req("FR-3.1")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
async def test_unknown_kind_and_bad_payload_are_rejected(
    app: FastAPI, conflict_kind: str, db: DbUrls
) -> None:
    """T-P0-18-21
    `add_review_item("nope", ...)` raises UnknownReviewKind (`unknown_review_kind`, 422); a
    payload that fails `ConflictPayload` raises a validation error; registering
    `test_conflict` twice raises. Nothing is written.
    """
    from pydantic import ValidationError  # noqa: PLC0415

    from tumnis.modules.tasks import api  # noqa: PLC0415

    with pytest.raises(api.UnknownReviewKind) as unknown:
        await api.add_review_item("nope", target=_target(), project_id=None, payload={})
    assert unknown.value.code == "unknown_review_kind"
    assert unknown.value.status == 422
    with pytest.raises(ValidationError):
        await api.add_review_item(conflict_kind, target=_target(), project_id=None, payload={})
    with pytest.raises(ValueError, match="test_conflict"):
        _testmod.register()
    assert owner_rows(db, "SELECT count(*) FROM review_items") == [(0,)]
