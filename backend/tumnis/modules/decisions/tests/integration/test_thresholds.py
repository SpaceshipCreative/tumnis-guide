"""Threshold edits and the recheck flag (P3-08, FR-11.5, SEC-3): a human edits a
threshold on Settings > Calibration with a reason, which is kept in the threshold history
and the audit log; a new pinned model flags every threshold for recheck."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from tumnis.modules.decisions.tests._cases import POINTS, stored_inputs

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._auth import SessionClient
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

CALIBRATION = "/v1/decisions/calibration"
REASON = "Auto-applied answers were right 97 times in 100 at 0.80"


def _points(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["decision_point"]: p for p in page["points"]}


@pytest.mark.req("FR-11.5", "SEC-3")
@pytest.mark.wp("P3-08")
@pytest.mark.xfail(strict=True, reason="spec:P3-08")
async def test_threshold_edit_is_logged_and_audited(
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    owner_session: AsyncSession,
) -> None:
    """T-P3-08-05
    `PUT /v1/decisions/thresholds/project_match` without a reason (blank) is refused 422
    and changes nothing. With a reason it answers the point with the new threshold (source
    `user`), writes one `thresholds_history` row (before: the default 0.85, after: 0.80,
    the reason) and one `threshold.changed` audit row with the same before, after and
    reason; the calibration page then shows the new value.
    """
    path = "/v1/decisions/thresholds/project_match"
    refused = await session_client.put(
        path, json={"threshold": {"min_confidence": 0.8}, "reason": "   "}
    )
    assert refused.status_code == 422, refused.text

    edited = await session_client.put(
        path, json={"threshold": {"min_confidence": 0.8}, "reason": REASON}
    )
    assert edited.status_code == 200, edited.text
    point = edited.json()
    assert point["decision_point"] == "project_match"
    assert point["threshold"]["min_confidence"] == pytest.approx(0.8)
    assert point["source"] == "user"
    assert point["needs_recheck"] is False

    history = (
        await owner_session.execute(
            text(
                "SELECT workspace_id, decision_point, model_version, before, after, reason "
                "FROM thresholds_history"
            )
        )
    ).all()
    assert len(history) == 1, history
    row = history[0]
    assert row.workspace_id == workspace.id
    assert row.decision_point == "project_match"
    assert row.model_version == "jev-1.13.0"
    assert row.before["min_confidence"] == pytest.approx(0.85)
    assert row.after["min_confidence"] == pytest.approx(0.8)
    assert row.reason == REASON

    audits = (
        await owner_session.execute(
            text(
                "SELECT actor_type, reason, details, occurred_at FROM audit_log "
                "WHERE action = 'threshold.changed'"
            )
        )
    ).all()
    assert len(audits) == 1, audits
    audit = audits[0]
    details = audit.details if isinstance(audit.details, dict) else json.loads(audit.details)
    assert audit.actor_type == "user"
    assert audit.reason == REASON
    assert audit.occurred_at == clock.now()
    assert details["decision_point"] == "project_match"
    assert details["before"]["min_confidence"] == pytest.approx(0.85)
    assert details["after"]["min_confidence"] == pytest.approx(0.8)

    page = await session_client.get(CALIBRATION)
    assert page.status_code == 200, page.text
    shown = _points(page.json())["project_match"]
    assert shown["threshold"]["min_confidence"] == pytest.approx(0.8)
    assert shown["source"] == "user"


@pytest.mark.req("FR-11.5")
@pytest.mark.wp("P3-08")
@pytest.mark.xfail(strict=True, reason="spec:P3-08")
async def test_model_version_change_flags_recheck(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    providers: Any,
    test_cache: Any,
) -> None:
    """T-P3-08-06
    With `jev-1.13.0` pinned, the calibration page shows that model and no point flagged.
    A new `model_version` on the decisions provider config flags every point for recheck
    on the page (under the new model) and clears the Jev cache: the same decision asks the
    provider again. Editing one point's threshold (with a reason) confirms it and clears
    its flag; the others stay flagged.
    """
    from tumnis.modules.decisions.api import (  # noqa: PLC0415
        ProviderConfigIn,
        SubjectRef,
        decide,
        put_provider_config,
    )
    from tumnis.modules.decisions.catalog import DecisionPoint  # noqa: PLC0415

    async def ask() -> Any:
        return await decide(
            DecisionPoint.QUICK_ADD_LABEL,
            stored_inputs("quick_add_label"),
            subject=SubjectRef(type="task", id=task_id),
            project_id=None,
            providers=providers,
            clock=clock,
        )

    task_id = uuid.uuid4()
    config = ProviderConfigIn(
        slot="decisions", primary="jev", fallback="vllm", model_version="jev-1.13.0"
    )
    await put_provider_config(workspace.ctx, config)
    await ask()
    before = await session_client.get(CALIBRATION)
    assert before.status_code == 200, before.text
    assert before.json()["model_version"] == "jev-1.13.0"
    assert set(_points(before.json())) == set(POINTS)
    assert not any(p["needs_recheck"] for p in before.json()["points"])

    await put_provider_config(
        workspace.ctx, config.model_copy(update={"model_version": "jev-1.14.0"})
    )
    again = await ask()
    assert again.cached is False
    assert [call.model for call in providers.jev.calls] == ["jev-1.13.0", "jev-1.14.0"]

    flagged = await session_client.get(CALIBRATION)
    assert flagged.status_code == 200, flagged.text
    assert flagged.json()["model_version"] == "jev-1.14.0"
    assert all(p["needs_recheck"] for p in flagged.json()["points"])

    confirmed = await session_client.put(
        "/v1/decisions/thresholds/quick_add_label",
        json={"threshold": {"min_confidence": 0.8}, "reason": "Checked against jev-1.14.0"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["needs_recheck"] is False
    after = _points((await session_client.get(CALIBRATION)).json())
    assert after["quick_add_label"]["needs_recheck"] is False
    assert all(p["needs_recheck"] for name, p in after.items() if name != "quick_add_label")
