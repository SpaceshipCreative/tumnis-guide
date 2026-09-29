"""`decisions.api.decide` (P1-02, FR-11.3, FR-11.5, FR-11.9, Caching NFR, Data flow rule
6): the provider chain with the vLLM fallback, the decision log, the 24-hour cache, the
local-decisions-only switch and the Jev rate limit keyed on the credential."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from tumnis.modules.decisions.tests._cases import POINTS, stored_inputs

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.usefixtures("core_db", "test_cache"),
]

PINNED = "jev-1.13.0"
CANARY = "CANARY-7f3a91"
DAY = timedelta(hours=24)


def _subject() -> Any:
    from tumnis.modules.decisions.api import SubjectRef  # noqa: PLC0415

    return SubjectRef(type="task", id=uuid.uuid4())


async def _decide(
    point: str,
    providers: Any,
    clock: FixedClock,
    *,
    inputs: dict[str, Any] | None = None,
    subject: Any = None,
    project_id: uuid.UUID | None = None,
) -> Any:
    from tumnis.modules.decisions.api import decide  # noqa: PLC0415
    from tumnis.modules.decisions.catalog import DecisionPoint  # noqa: PLC0415

    return await decide(
        DecisionPoint(point),
        stored_inputs(point) if inputs is None else inputs,
        subject=subject or _subject(),
        project_id=project_id,
        providers=providers,
        clock=clock,
    )


async def _log(owner_session: AsyncSession, where: str = "true", **params: Any) -> list[Any]:
    await owner_session.commit()  # a fresh snapshot
    return list(
        (
            await owner_session.execute(
                text(f"SELECT * FROM decision_log WHERE {where} ORDER BY created_at, id"),  # noqa: S608
                params,
            )
        )
        .mappings()
        .all()
    )


def _down(fake: Any, point: str) -> None:
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415

    fake.script(point, fail=AdapterUnavailable(fake.provider, "ask", "down (test)"))


@pytest.mark.req("FR-11.3")
@pytest.mark.wp("P1-02")
async def test_jev_down_uses_vllm_marked_fallback(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-05
    With the Jev fake raising and the vLLM fake answering, the decision comes from vLLM
    and is marked `fallback = true`, `fallback_reason = "primary_failed"`, and the log row
    holds the effective threshold, stricter than the point's default by the margin.
    """
    from tumnis.modules.decisions.rules import DEFAULT_THRESHOLDS  # noqa: PLC0415

    _down(providers.jev, "quick_add_label")
    decision = await _decide("quick_add_label", providers, clock)

    assert len(providers.jev.calls) == 1
    assert len(providers.vllm.calls) == 1
    assert decision.provider == "vllm"
    assert decision.fallback is True
    assert decision.fallback_reason == "primary_failed"
    assert decision.cached is False
    [row] = await _log(owner_session)
    assert row["id"] == decision.decision_id
    assert (row["provider"], row["fallback"], row["fallback_reason"]) == (
        "vllm",
        True,
        "primary_failed",
    )
    default = DEFAULT_THRESHOLDS["quick_add_label"]
    assert default.min_confidence is not None
    assert row["threshold"]["min_confidence"] > default.min_confidence
    assert row["threshold"]["min_confidence"] == pytest.approx(
        default.min_confidence + default.fallback_margin
    )


@pytest.mark.req("FR-11.3")
@pytest.mark.wp("P1-02")
async def test_both_down_creates_review_item(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-06
    With both fakes failing, the decision routes to REVIEW with provider `none` and no
    value, and exactly one open `decision_unavailable` review item targets the subject,
    through `tasks.api.add_review_item` (asking again for the same subject reuses it).
    """
    from tumnis.modules.decisions.rules import Route  # noqa: PLC0415

    _down(providers.jev, "quick_add_label")
    _down(providers.vllm, "quick_add_label")
    subject = _subject()
    decision = await _decide("quick_add_label", providers, clock, subject=subject)
    again = await _decide("quick_add_label", providers, clock, subject=subject)

    for got in (decision, again):
        assert got.route is Route.REVIEW
        assert got.provider == "none"
        assert got.value is None
        assert got.answers == {}
        assert got.model_version is None
    await owner_session.commit()
    items = (
        await owner_session.execute(
            text(
                "SELECT kind, target_type, target_id, dedupe_key, payload FROM review_items "
                "WHERE decided_at IS NULL AND deleted_at IS NULL"
            )
        )
    ).all()
    assert len(items) == 1
    kind, target_type, target_id, dedupe_key, payload = items[0]
    assert (kind, target_type, target_id) == ("decision_unavailable", "task", subject.id)
    assert dedupe_key == f"decision:quick_add_label:{subject.id}"
    assert payload["point"] == "quick_add_label"
    rows = await _log(owner_session)
    assert [(r["provider"], r["outcome"]) for r in rows] == [("none", "review")] * 2


def _script_mixed(jev: Any) -> dict[str, str]:
    """Jev answers giving one of each outcome; returns point -> expected outcome."""
    jev.script(
        "quick_add_label",
        {
            "label": {
                "type": "choice",
                "choice": "human",
                "confidence": 0.95,
                "probabilities": {"human": 0.96, "ai": 0.02, "hybrid": 0.01, "unknown": 0.01},
            }
        },
    )
    jev.script("actionability", {"actionable": {"type": "noul", "noul": 0.93}})
    # Unscripted: project_match picks p01 at 0.7 (< 0.85), focus_on_task and approval_need
    # answer a noul of 0.5 (inside the band; approval asked).
    return {
        "quick_add_label": "applied",
        "actionability": "applied",
        "project_match": "review",
        "focus_on_task": "deterministic",
        "approval_need": "approval_required",
    }


@pytest.mark.req("FR-11.5")
@pytest.mark.wp("P1-02")
async def test_every_decision_logged(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-07
    After N decisions with mixed outcomes, the log has N rows, each with the model version,
    the input hash (sha256 of the canonical request and model), the fields sent, the
    effective threshold and the outcome the decision returned.
    """
    from tumnis.modules.decisions.catalog import (  # noqa: PLC0415
        DecisionPoint,
        build_request,
        input_hash,
    )
    from tumnis.modules.decisions.rules import DEFAULT_THRESHOLDS  # noqa: PLC0415

    expected = _script_mixed(providers.jev)
    decisions = [await _decide(point, providers, clock) for point in expected]

    rows = await _log(owner_session)
    assert len(rows) == len(expected)
    for (point, outcome), decision, row in zip(expected.items(), decisions, rows, strict=True):
        req = build_request(DecisionPoint(point), stored_inputs(point))
        assert decision.route.value == outcome, point
        assert row["id"] == decision.decision_id
        assert row["decision_point"] == point
        assert row["outcome"] == outcome
        assert row["provider"] == "jev"
        assert row["model_version"] == PINNED
        assert bytes(row["input_hash"]) == input_hash(req, PINNED)
        assert len(bytes(row["input_hash"])) == 32
        assert row["fields_sent"] == list(req.fields_sent)
        assert row["threshold"] == DEFAULT_THRESHOLDS[point].model_dump(mode="json")
        assert row["answer"] is not None
        assert row["subject_type"] == "task"
        assert row["cached"] is False
        assert row["overridden"] is None


def _with_canary(value: Any) -> Any:
    if isinstance(value, str):
        return f"{value} {CANARY}"
    if isinstance(value, list):
        return [_with_canary(item) for item in value]
    if isinstance(value, dict):
        return {key: _with_canary(item) for key, item in value.items()}
    return value


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-02")
async def test_log_never_holds_input_text(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-08
    With a canary string in every text input of all nine points (answered by Jev, then by
    the vLLM fallback, then by nobody), no text, text[] or jsonb column of `decision_log`
    holds it, nor the `decision.made` events or the review items written with them.
    """
    for point in POINTS:
        await _decide(point, providers, clock, inputs=_with_canary(stored_inputs(point)))
    _down(providers.jev, "quick_add_label")
    await _decide(
        "quick_add_label", providers, clock, inputs=_with_canary(stored_inputs("quick_add_label"))
    )
    _down(providers.vllm, "quick_add_label")
    await _decide(
        "quick_add_label", providers, clock, inputs=_with_canary(stored_inputs("quick_add_label"))
    )

    rows = await _log(owner_session)
    assert len(rows) == len(POINTS) + 2
    for table in ("decision_log", "outbox", "review_items"):
        leaked: int = (
            await owner_session.execute(
                text(f"SELECT count(*) FROM {table} t WHERE t::text LIKE :canary"),  # noqa: S608
                {"canary": f"%{CANARY}%"},
            )
        ).scalar_one()
        assert leaked == 0, table
    made: int = (
        await owner_session.execute(
            text("SELECT count(*) FROM outbox WHERE name = 'decision.made'")
        )
    ).scalar_one()
    assert made == len(POINTS) + 2


@pytest.mark.req("Caching NFR")
@pytest.mark.wp("P1-02")
async def test_cache_hit_within_24h(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-09
    The same point and inputs asked twice within 24 hours (fixed clock, 23 h 59 min apart)
    call the provider once; the second decision is `cached = true` and is logged too.
    """
    first = await _decide("quick_add_label", providers, clock)
    clock.advance(DAY - timedelta(minutes=1))
    second = await _decide("quick_add_label", providers, clock)

    assert len(providers.jev.calls) == 1
    assert (first.cached, second.cached) == (False, True)
    assert second.provider == "jev"
    assert second.answers == first.answers
    assert second.decision_id != first.decision_id
    rows = await _log(owner_session)
    assert [r["cached"] for r in rows] == [False, True]
    assert rows[0]["input_hash"] == rows[1]["input_hash"]


@pytest.mark.req("Caching NFR")
@pytest.mark.wp("P1-02")
async def test_cache_expires_after_24h(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock
) -> None:
    """T-P1-02-10
    At 24 hours and 1 second after the first decision, the provider is called again.
    """
    await _decide("quick_add_label", providers, clock)
    clock.advance(DAY + timedelta(seconds=1))
    later = await _decide("quick_add_label", providers, clock)

    assert len(providers.jev.calls) == 2
    assert later.cached is False


@pytest.mark.req("Caching NFR")
@pytest.mark.wp("P1-02")
async def test_threshold_change_clears_cache(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-11
    Updating the point's threshold makes the next identical decision call the provider,
    and that decision is routed and logged with the new threshold.
    """
    from tumnis.modules.decisions.api import put_threshold  # noqa: PLC0415
    from tumnis.modules.decisions.catalog import DecisionPoint  # noqa: PLC0415
    from tumnis.modules.decisions.rules import Route, Threshold  # noqa: PLC0415

    first = await _decide("quick_add_label", providers, clock)  # the fake's 0.7 < 0.80
    assert first.route is Route.REVIEW
    await put_threshold(workspace.ctx, DecisionPoint.QUICK_ADD_LABEL, Threshold(min_confidence=0.6))
    second = await _decide("quick_add_label", providers, clock)

    assert len(providers.jev.calls) == 2
    assert second.cached is False
    assert second.route is Route.APPLY
    rows = await _log(owner_session)
    assert rows[1]["threshold"]["min_confidence"] == pytest.approx(0.6)


@pytest.mark.req("FR-11.5")
@pytest.mark.wp("P1-02")
async def test_model_version_change_clears_cache_and_flags_recheck(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-12
    Changing the decisions slot's pinned model clears the cache (the next identical
    decision calls the provider with the new model) and sets `needs_recheck` on every
    decision point's threshold row for the new model, carrying over the values in force
    (a user-set one keeps its value); the old model's rows are left as they were.
    """
    from tumnis.modules.decisions.api import (  # noqa: PLC0415
        ProviderConfigIn,
        put_provider_config,
        put_threshold,
    )
    from tumnis.modules.decisions.catalog import DecisionPoint  # noqa: PLC0415
    from tumnis.modules.decisions.rules import Threshold  # noqa: PLC0415

    config = ProviderConfigIn(
        slot="decisions", primary="jev", fallback="vllm", model_version=PINNED
    )
    await put_provider_config(workspace.ctx, config)
    await put_threshold(
        workspace.ctx, DecisionPoint.QUICK_ADD_LABEL, Threshold(min_confidence=0.75)
    )
    await _decide("quick_add_label", providers, clock)
    await put_provider_config(
        workspace.ctx, config.model_copy(update={"model_version": "jev-1.14.0"})
    )
    after = await _decide("quick_add_label", providers, clock)

    assert [call.model for call in providers.jev.calls] == [PINNED, "jev-1.14.0"]
    assert after.cached is False
    assert after.model_version == "jev-1.14.0"
    await owner_session.commit()
    rows = (
        await owner_session.execute(
            text(
                "SELECT decision_point, model_version, value, source, needs_recheck "
                "FROM thresholds WHERE deleted_at IS NULL ORDER BY model_version, decision_point"
            )
        )
    ).all()
    new = {r.decision_point: r for r in rows if r.model_version == "jev-1.14.0"}
    old = {r.decision_point: r for r in rows if r.model_version == PINNED}
    assert set(new) == set(POINTS)
    assert all(r.needs_recheck for r in new.values())
    assert new["quick_add_label"].value["min_confidence"] == pytest.approx(0.75)
    assert new["quick_add_label"].source == "user"
    assert set(old) == {"quick_add_label"}
    assert old["quick_add_label"].needs_recheck is False


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-02")
async def test_local_only_project_never_calls_jev(
    workspace: WorkspaceHandle, providers: Any, clock: FixedClock, owner_session: AsyncSession
) -> None:
    """T-P1-02-13
    Given the project `Beta app` set to local decisions only, when `decide` runs once per
    decision point with its `project_id`, then the Jev fake records no call, and every
    decision (and log row) says provider `vllm`, `fallback_reason = "local_only"`.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    async with tenant_session(workspace.ctx) as s:
        project = await projects.create_project(
            s, SYSTEM_ACTOR, projects.ProjectCreate(name="Beta app"), now=clock.now()
        )
    async with tenant_session(workspace.ctx) as s:
        patch = projects.ProjectPatch(local_decisions_only=True, version=project.version)
        updated = await projects.update_project(
            s, SYSTEM_ACTOR, project.id, patch, project.version, now=clock.now()
        )
    assert updated.model_dump()["local_decisions_only"] is True

    decisions = [await _decide(point, providers, clock, project_id=project.id) for point in POINTS]

    assert providers.jev.calls == []
    assert len(providers.vllm.calls) == len(POINTS)
    for decision in decisions:
        assert (decision.provider, decision.fallback, decision.fallback_reason) == (
            "vllm",
            True,
            "local_only",
        )
    rows = await _log(owner_session)
    assert len(rows) == len(POINTS)
    assert {(r["provider"], r["fallback_reason"], r["project_id"]) for r in rows} == {
        ("vllm", "local_only", project.id)
    }


@pytest.mark.req("FR-11.9")
@pytest.mark.wp("P1-02")
@pytest.mark.usefixtures("master_key_file")
async def test_jev_limit_keyed_on_credential(
    db: DbUrls, workspace: WorkspaceHandle, providers: Any, clock: FixedClock
) -> None:
    """T-P1-02-17
    With the limit set to 3 per 60 s for the test, a fourth Jev call on the same
    credential waits until the window moves (fixed clock); a second workspace with a
    different credential is not held back; cache hits do not count against the limit.
    """
    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.settings_store import put_setting  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, use_workspace  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
    from tumnis.modules.decisions.api import (  # noqa: PLC0415
        JevSettings,
        ProviderConfigIn,
        put_provider_config,
    )
    from tumnis.modules.decisions.limiter import (  # noqa: PLC0415
        credential_fingerprint,
        limiter_for,
    )

    waits: list[float] = []

    async def sleep(seconds: float) -> None:  # waiting moves the fixed clock
        waits.append(seconds)
        clock.advance(timedelta(seconds=seconds))

    other = WorkspaceContext(make_workspace(db, "Other"), SYSTEM_ACTOR)
    keys = {}
    for ctx in (workspace.ctx, other):
        key = "ts_live_" + secrets.token_hex(16)
        keys[ctx.workspace_id] = key
        config = ProviderConfigIn(
            slot="decisions",
            primary="jev",
            fallback="vllm",
            model_version=PINNED,
            api_key=SecretStr(key),
        )
        await put_provider_config(ctx, config)
        await put_setting(ctx, "decisions.jev", JevSettings(rpm=3), expected_version=None)
    limiter_for(credential_fingerprint(keys[workspace.id]), rpm=3, clock=clock, sleep=sleep)

    def inputs(n: int) -> dict[str, Any]:
        return {**stored_inputs("quick_add_label"), "title": f"Task number {n}"}

    for n in range(3):
        await _decide("quick_add_label", providers, clock, inputs=inputs(n))
    assert waits == []
    hit = await _decide("quick_add_label", providers, clock, inputs=inputs(0))
    assert hit.cached is True
    assert waits == []

    with use_workspace(other):
        elsewhere = await _decide("quick_add_label", providers, clock, inputs=inputs(9))
    assert elsewhere.cached is False
    assert waits == []

    started = clock.now()
    await _decide("quick_add_label", providers, clock, inputs=inputs(3))
    assert len(waits) == 1
    assert clock.now() - started == timedelta(seconds=60)
    assert len(providers.jev.calls) == 5
