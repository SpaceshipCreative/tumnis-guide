"""decisions public functions and DTOs; the only file other modules may import.

P1-01: the typed answers every decisions provider returns, the decisions slot's provider
config (primary, fallback, pinned model and the credential sealed with the workspace data
key), and `ask_raw`, a bare call to one provider for a built request.

P1-02: `decide`, the one function every decision caller goes through. It routes a typed
answer to an outcome (applied, review, deterministic or approval required), falls back to
vLLM with a stricter threshold, honours the per-project local decisions only switch,
caches provider answers for 24 hours, waits on the Jev limiter before each Jev call, and
logs every decision (typed answers, never the inputs) with a `decision.made` event.
`put_threshold` sets a point's threshold; `record_outcome` stores what the human did.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, Literal, Self
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from sqlalchemy import insert, null, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import tenancy
from tumnis.core.adapters.errors import AdapterError
from tumnis.core.adapters.registry import current_mode, resolve
from tumnis.core.cache import CacheKey, CacheSpec, invalidate_on_commit, register_cache
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy
from tumnis.core.outbox import emit
from tumnis.core.settings_store import get_setting, open_for_workspace, seal_for_workspace
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.modules.decisions import generation_config
from tumnis.modules.decisions.adapters.port import (
    ChoiceAnswer,
    DecisionsProvider,
    GenerationProvider,
    NoulAnswer,
    ProviderName,
    ProviderResponse,
    ScoreAnswer,
    TypedAnswer,
)
from tumnis.modules.decisions.catalog import (
    CATALOGUE,
    DecisionPoint,
    OutboundRequest,
    QuestionSpec,
    build_request,
    input_hash,
)
from tumnis.modules.decisions.limiter import credential_fingerprint, limiter_for
from tumnis.modules.decisions.models import DecisionLog
from tumnis.modules.decisions.models import ProviderConfig as ProviderConfigRow
from tumnis.modules.decisions.models import Threshold as ThresholdRow
from tumnis.modules.decisions.payloads import (
    DecisionMadeV1,
    DecisionUnavailablePayload,
    DecisionValueEdit,
)
from tumnis.modules.decisions.rules import (
    DEFAULT_THRESHOLDS,
    Route,
    Threshold,
    effective_threshold,
    is_pinned_model,
    low_route,
    main_answer,
    route,
)
from tumnis.modules.projects import api as projects
from tumnis.modules.tasks import api as tasks
from tumnis.settings import GenerationSettings

__all__ = [
    "ChoiceAnswer",
    "Decision",
    "DecisionPoint",
    "DecisionsProvider",
    "JevSettings",
    "NoulAnswer",
    "ProviderConfig",
    "ProviderConfigIn",
    "ProviderResponse",
    "Providers",
    "Route",
    "ScoreAnswer",
    "Slot",
    "SubjectRef",
    "Threshold",
    "TypedAnswer",
    "VllmSettings",
    "ask_raw",
    "assess_blocking_impact",
    "configure_generation",
    "configure_net_policy",
    "decide",
    "get_provider_config",
    "put_provider_config",
    "put_threshold",
    "record_outcome",
]

_log = structlog.get_logger(__name__)

Slot = Literal["decisions", "generation", "speech", "embeddings"]
PINNED_JEV_DEFAULT: Final = "jev-1.13.0"  # plan default; the row's model_version wins


def credential_aad(workspace_id: UUID, slot: str) -> bytes:
    """Binds a sealed credential to its workspace and slot: a blob copied elsewhere fails."""
    return f"tumnis:provider_config:v1:{workspace_id}:{slot}".encode()


class ProviderConfigIn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: Slot
    primary: ProviderName
    fallback: ProviderName | None = None
    model_version: str
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _pinned(self) -> Self:
        if self.primary == "jev" and not is_pinned_model(self.model_version):
            raise ValueError(
                f"model_version must be a pinned versioned id, not {self.model_version!r}"
            )
        return self


class ProviderConfig(ProviderConfigIn):
    version: int


_UPSERT: Final = text(
    """
    INSERT INTO provider_configs (slot, "primary", fallback, model_version, credentials_enc)
    VALUES (:slot, :primary, :fallback, :model_version, :credentials_enc)
    ON CONFLICT (workspace_id, slot)
    DO UPDATE SET "primary" = EXCLUDED."primary", fallback = EXCLUDED.fallback,
                  model_version = EXCLUDED.model_version,
                  credentials_enc = EXCLUDED.credentials_enc,
                  deleted_at = NULL,
                  version = provider_configs.version + 1
    RETURNING version
    """
)


async def put_provider_config(
    ctx: WorkspaceContext, config: ProviderConfigIn, *, session: AsyncSession | None = None
) -> int:
    """Store the slot's config (replacing any), sealing the key; returns the row version.
    A new pinned model for the decisions slot carries every point's threshold over to it,
    flagged `needs_recheck`, and clears the cached answers (P1-02, FR-11.5)."""
    async with session_for(ctx, session) as s:
        previous = None
        if config.slot == "decisions":
            previous = await s.scalar(
                select(ProviderConfigRow.model_version).where(
                    ProviderConfigRow.slot == "decisions", ProviderConfigRow.deleted_at.is_(None)
                )
            )
        sealed = None
        if config.api_key is not None:
            _, sealed = await seal_for_workspace(
                s,
                ctx.workspace_id,
                config.api_key.get_secret_value().encode(),
                aad=credential_aad(ctx.workspace_id, config.slot),
            )
        params = config.model_dump(include={"slot", "primary", "fallback", "model_version"})
        written = await s.execute(_UPSERT, {**params, "credentials_enc": sealed})
        version = int(written.scalar_one())
        if previous is not None and previous != config.model_version:
            await _carry_thresholds(s, ctx.workspace_id, previous, config.model_version)
        return version


async def get_provider_config(
    ctx: WorkspaceContext, slot: Slot, *, session: AsyncSession | None = None
) -> ProviderConfig | None:
    """The slot's config with its key opened, or None when the slot is not configured."""
    t = ProviderConfigRow
    async with session_for(ctx, session) as s:
        row = (
            await s.execute(select(t).where(t.slot == slot, t.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if row is None:
            return None
        api_key = None
        if row.credentials_enc is not None:
            opened = await open_for_workspace(
                s,
                ctx.workspace_id,
                bytes(row.credentials_enc),
                aad=credential_aad(ctx.workspace_id, slot),
            )
            api_key = SecretStr(opened.decode())
        return ProviderConfig.model_validate(
            {
                "slot": row.slot,
                "primary": row.primary,
                "fallback": row.fallback,
                "model_version": row.model_version,
                "api_key": api_key,
                "version": row.version,
            }
        )


async def ask_raw(
    provider: DecisionsProvider,
    point: DecisionPoint,
    inputs: Mapping[str, Any],
    *,
    model: str,
    timeout_ms: int | None = None,
) -> ProviderResponse:
    """Build the whitelisted request for `point` (MissingDecisionInput,
    DecisionRequestTooLarge before any call) and ask `provider` with the pinned `model`.
    No routing, logging of the decision, caching or rate limiting: `decide` (P1-02) adds
    them around its own call."""
    req = build_request(point, inputs)
    timeout = CATALOGUE[point].timeout_ms if timeout_ms is None else timeout_ms
    return await provider.ask(req, model=model, timeout_ms=timeout)


def configure_generation(
    settings: GenerationSettings,
    *,
    net_policy: NetPolicy | None = None,
    provider: GenerationProvider | None = None,
) -> None:
    """Set the Generation slot's endpoint and timeouts for this process (P1-03): the worker
    calls it once at start; tests pass a `provider` (a fake). Only `generation_api` asks
    the slot; nothing here generates text."""
    generation_config.configure(settings, net_policy=net_policy, provider=provider)


# --- decide (P1-02) ------------------------------------------------------------------------


class SubjectRef(BaseModel):
    """What a decision is about."""

    model_config = ConfigDict(frozen=True)

    type: Literal["task", "message", "note", "review_item", "focus_session", "run"]
    id: UUID


DecisionProvider = Literal["jev", "vllm", "fake", "none"]
FallbackReason = Literal["primary_failed", "local_only"]


class Decision(BaseModel):
    decision_id: UUID
    point: DecisionPoint
    route: Route
    value: Any | None
    answers: dict[str, TypedAnswer]
    provider: DecisionProvider
    fallback: bool
    fallback_reason: FallbackReason | None
    model_version: str | None
    cached: bool


@dataclass(frozen=True)
class Providers:
    """The provider chain `decide` asks: the primary (Jev) and the vLLM fallback."""

    jev: DecisionsProvider | None = None
    vllm: DecisionsProvider | None = None


class JevSettings(BaseModel):
    """Workspace setting `decisions.jev`: the request limit per 60 s (FR-11.9)."""

    rpm: int = 1_200


DEFAULT_VLLM_MODEL: Final = "vllm-local"


class VllmSettings(BaseModel):
    """Workspace setting `decisions.vllm`: where the fallback runs and the model it serves.
    Without a `base_url` the real fallback is not built (a fake always is)."""

    base_url: str | None = None
    model: str = DEFAULT_VLLM_MODEL


DECISIONS_CACHE: Final = register_cache(
    CacheSpec(
        "decisions",
        "workspace",
        86_400,  # Caching NFR: 24 hours
        ("put_threshold", "put_provider_config (decisions model_version)"),
    )
)

DECISION_UNAVAILABLE: Final = tasks.ReviewKindSpec(
    kind="decision_unavailable",
    owner_module="decisions",
    payload_schema=DecisionUnavailablePayload,
    actions=("accept", "edit", "reject", "snooze"),  # accept asks again, edit sets by hand
    impact_scope="task",
    action_payloads=MappingProxyType({"edit": DecisionValueEdit}),  # P1-13
)
tasks.register_review_kind(DECISION_UNAVAILABLE)


def _context() -> WorkspaceContext:
    ctx = tenancy.current()
    if ctx is None:
        raise RuntimeError("decisions api called outside a workspace context")
    return ctx


def _tag(workspace_id: UUID, point: str) -> str:
    """Every cached answer of one point in one workspace."""
    return f"ws:{workspace_id}:decisions:{point}"


# --- Thresholds ------------------------------------------------------------------------------

_THRESHOLD_KEY: Final = ("workspace_id", "decision_point", "model_version")


async def _pinned_model(ctx: WorkspaceContext, s: AsyncSession) -> str:
    config = await get_provider_config(ctx, "decisions", session=s)
    return PINNED_JEV_DEFAULT if config is None else config.model_version


async def put_threshold(
    ctx: WorkspaceContext,
    point: DecisionPoint,
    threshold: Threshold,
    *,
    model_version: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Set the point's threshold (source `user`) for the model (the pinned one when not
    given) and clear the point's cached answers."""
    t = ThresholdRow
    async with session_for(ctx, session) as s:
        model = model_version or await _pinned_model(ctx, s)
        stmt = pg_insert(t).values(
            decision_point=point.value,
            model_version=model,
            value=threshold.model_dump(mode="json"),
            source="user",
            needs_recheck=False,
        )
        await s.execute(
            stmt.on_conflict_do_update(
                index_elements=list(_THRESHOLD_KEY),
                set_={
                    "value": stmt.excluded.value,
                    "source": "user",
                    "needs_recheck": False,
                    "deleted_at": None,
                    "version": t.version + 1,
                },
            )
        )
        await invalidate_on_commit(s, tag=_tag(ctx.workspace_id, point.value))


async def _carry_thresholds(s: AsyncSession, workspace_id: UUID, old: str, new: str) -> None:
    """Every point gets a row for the new model holding the value in force for the old one
    (its row, else the default; the source kept), flagged `needs_recheck`; the old rows
    stay as they were. The points' cached answers are cleared."""
    t = ThresholdRow
    found = (
        (await s.execute(select(t).where(t.model_version == old, t.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    carried = {row.decision_point: row for row in found}
    rows = []
    for point in DecisionPoint:
        prior = carried.get(point.value)
        rows.append(
            {
                "decision_point": point.value,
                "model_version": new,
                "value": (
                    DEFAULT_THRESHOLDS[point].model_dump(mode="json")
                    if prior is None
                    else prior.value
                ),
                "source": "default" if prior is None else prior.source,
                "needs_recheck": True,
            }
        )
    stmt = pg_insert(t).values(rows)
    await s.execute(
        stmt.on_conflict_do_update(
            index_elements=list(_THRESHOLD_KEY),
            set_={"needs_recheck": True, "version": t.version + 1},
        )
    )
    for point in DecisionPoint:
        await invalidate_on_commit(s, tag=_tag(workspace_id, point.value))


async def _threshold(s: AsyncSession, point: DecisionPoint, model: str) -> Threshold:
    t = ThresholdRow
    value = await s.scalar(
        select(t.value).where(
            t.decision_point == point.value, t.model_version == model, t.deleted_at.is_(None)
        )
    )
    return DEFAULT_THRESHOLDS[point] if value is None else Threshold.model_validate(value)


# --- Providers -------------------------------------------------------------------------------

_net_policy: list[NetPolicy] = [NetPolicy(mode="hosted")]  # the strictest until configured
_built: dict[tuple[str, str], DecisionsProvider] = {}


def configure_net_policy(policy: NetPolicy) -> None:
    """The worker sets its deployment's SSRF policy for the real vLLM fallback."""
    _net_policy[0] = policy


def _build(name: str, identity: str, **deps: Any) -> DecisionsProvider:
    """One instance per (adapter, credential or endpoint) in this process, so one
    workspace's broken token does not open the circuit for another."""
    key = (name, identity)
    if key not in _built:
        _built[key] = resolve(name, current_mode(), **deps)
    return _built[key]


def _production_providers(config: ProviderConfig | None, vllm: VllmSettings) -> Providers:
    if current_mode() == "fake":
        return Providers(jev=_build("decisions.jev", "fake"), vllm=_build("decisions.vllm", "fake"))
    jev = None
    if config is not None and config.api_key is not None:
        jev = _build(
            "decisions.jev",
            f"{credential_fingerprint(config.api_key)}:{config.model_version}",
            api_key=config.api_key,
            pinned_model=config.model_version,
            clock=SystemClock(),
        )
    fallback = None
    if vllm.base_url is not None:
        fallback = _build(
            "decisions.vllm",
            vllm.base_url,
            base_url=vllm.base_url,
            clock=SystemClock(),
            net_policy=_net_policy[0],
        )
    return Providers(jev=jev, vllm=fallback)


@dataclass(frozen=True)
class _Slot:
    name: Literal["jev", "vllm", "fake"]
    provider: DecisionsProvider | None
    model: str
    fallback_reason: FallbackReason | None


def _chain(
    config: ProviderConfig | None,
    providers: Providers,
    *,
    vllm_model: str,
    local_only: bool,
) -> list[_Slot]:
    """The providers to ask, in order. A local-only project asks vLLM alone, so nothing
    reaches Jev (Data flow rule 6)."""
    jev_model = PINNED_JEV_DEFAULT if config is None else config.model_version

    def slot(name: ProviderName, reason: FallbackReason | None) -> _Slot:
        if name == "vllm":
            return _Slot("vllm", providers.vllm, vllm_model, reason)
        return _Slot(name, providers.jev, jev_model, reason)

    if local_only:
        return [slot("vllm", "local_only")]
    primary: ProviderName = "jev" if config is None else config.primary
    fallback: ProviderName | None = "vllm" if config is None else config.fallback
    chain = [slot(primary, None)]
    if fallback is not None and fallback != primary:
        chain.append(slot(fallback, "primary_failed"))
    return chain


async def _acquire_jev(ctx: WorkspaceContext, config: ProviderConfig | None, clock: Clock) -> None:
    """Wait for a slot on the limiter of the Jev credential (R-32, FR-11.9); without a
    stored key, the workspace stands in for it."""
    credential: SecretStr | str = (
        config.api_key
        if config is not None and config.api_key is not None
        else f"workspace:{ctx.workspace_id}"
    )
    setting = await get_setting(ctx, "decisions.jev", JevSettings)
    rpm = JevSettings().rpm if setting is None else setting.value.rpm
    await limiter_for(credential_fingerprint(credential), rpm=rpm, clock=clock).acquire()


class _CacheEntry(BaseModel):
    """A cached answer and the provider that gave it: the key is the plan's (point and input
    hash), so the entry itself says whose answer it is."""

    slot: str
    response: ProviderResponse


@dataclass(frozen=True)
class _Asked:
    response: ProviderResponse | None
    slot: _Slot | None
    cached: bool


async def _ask(  # noqa: PLR0917  # the pieces of one decision, spelled out
    ctx: WorkspaceContext,
    config: ProviderConfig | None,
    chain: Sequence[_Slot],
    req: OutboundRequest,
    spec: QuestionSpec,
    clock: Clock,
) -> _Asked:
    """The cache (the first provider's answers only: never a fallback's), then each
    provider of the chain until one answers."""
    first = chain[0]
    key = CacheKey.for_workspace(
        ctx.workspace_id, "decisions", spec.point.value, input_hash(req, first.model).hex()
    )
    hit = await DECISIONS_CACHE.get(key)
    if hit is not None:
        entry = _CacheEntry.model_validate_json(hit)
        if entry.slot == first.name:  # another provider's answer is a miss (Data flow rule 6)
            return _Asked(entry.response, first, cached=True)
    token = DECISIONS_CACHE.token()  # an invalidation during the call skips the fill
    for slot in chain:
        if slot.provider is None:
            continue
        if slot.name != "vllm":
            await _acquire_jev(ctx, config, clock)
        try:
            response = await slot.provider.ask(req, model=slot.model, timeout_ms=spec.timeout_ms)
        except AdapterError as exc:
            _log.warning(
                "decisions.provider_failed",
                point=spec.point.value,
                provider=slot.name,
                error=type(exc).__name__,
            )
            continue
        if slot is first:
            await DECISIONS_CACHE.fill(
                key,
                _CacheEntry(slot=first.name, response=response).model_dump_json().encode(),
                since=token,
                tags=(_tag(ctx.workspace_id, spec.point.value),),
            )
        return _Asked(response, slot, cached=False)
    return _Asked(None, None, cached=False)


def _confidence(answer: TypedAnswer) -> float:
    """Choice and Score confidence; a Noul's distance from 0.5 scaled to 0..1."""
    if isinstance(answer, NoulAnswer):
        return abs(answer.noul - 0.5) * 2
    return answer.confidence


async def decide(  # the plan's signature plus the injected providers and clock
    point: DecisionPoint,
    inputs: Mapping[str, Any],
    *,
    subject: SubjectRef,
    project_id: UUID | None,
    providers: Providers | None = None,
    clock: Clock | None = None,
) -> Decision:
    """Worker-only. Order: local-only check, cache, limiter before each Jev call (R-32),
    primary, fallback, route, log, emit. Runs in the current workspace context."""
    ctx = _context()
    clock = clock or SystemClock()
    spec = CATALOGUE[point]
    req = build_request(point, inputs)  # refuses a bad request before anything is asked
    config = await get_provider_config(ctx, "decisions")
    local_only = project_id is not None and await projects.local_decisions_only(project_id)
    vllm_setting = await get_setting(ctx, "decisions.vllm", VllmSettings)
    vllm = VllmSettings() if vllm_setting is None else vllm_setting.value
    if providers is None:
        providers = _production_providers(config, vllm)
    chain = _chain(config, providers, vllm_model=vllm.model, local_only=local_only)
    asked = await _ask(ctx, config, chain, req, spec, clock)

    async with tenant_session(ctx) as s:
        pinned = PINNED_JEV_DEFAULT if config is None else config.model_version
        threshold = await _threshold(s, point, pinned)
        answer: TypedAnswer | None = None
        if asked.response is None or asked.slot is None:
            # Nobody answered: judged as strictly as the last provider tried would be.
            reason: FallbackReason | None = chain[-1].fallback_reason
            fallback = any(x.fallback_reason for x in chain)
            eff = effective_threshold(threshold, fallback=fallback)
            outcome, value = low_route(spec), None
            provider: DecisionProvider = "none"
            answers: dict[str, TypedAnswer] = {}
        else:
            reason = asked.slot.fallback_reason
            fallback = reason is not None
            eff = effective_threshold(threshold, fallback=fallback)
            answers = dict(asked.response.answers)
            answer = main_answer(spec.main_question, answers)
            outcome, value = route(spec, answer, threshold, fallback=fallback)
            provider = asked.slot.name
        latency = None if asked.cached or asked.response is None else asked.response.latency_ms
        model_version = None if asked.response is None else asked.response.model
        decision_id: UUID = await s.scalar(  # type: ignore[assignment]  # RETURNING id is never null
            insert(DecisionLog)
            .values(
                decision_point=point.value,
                project_id=project_id,
                subject_type=subject.type,
                subject_id=subject.id,
                provider=provider,
                fallback=fallback,
                fallback_reason=reason,
                model_version=model_version,
                input_hash=input_hash(req, chain[0].model),
                fields_sent=list(req.fields_sent),
                answer=(
                    null()
                    if not answers
                    else {qid: a.model_dump(mode="json") for qid, a in answers.items()}
                ),
                confidence=None if answer is None else _confidence(answer),
                threshold=eff.model_dump(mode="json"),
                outcome=outcome.value,
                cached=asked.cached,
                latency_ms=latency,
            )
            .returning(DecisionLog.id)
        )
        if provider == "none" and outcome is Route.REVIEW:
            await tasks.add_review_item(
                "decision_unavailable",
                target=tasks.TargetRef(type=subject.type, id=subject.id),
                project_id=project_id,
                payload=DecisionUnavailablePayload(decision_id=decision_id, point=point).model_dump(
                    mode="json"
                ),
                dedupe_key=f"decision:{point.value}:{subject.id}",
                session=s,
            )
        await emit(
            s,
            DecisionMadeV1(
                decision_id=decision_id,
                decision_point=point,
                outcome=outcome,
                provider=provider,
                fallback=fallback,
                cached=asked.cached,
            ),
            occurred_at=clock.now(),
        )
    _log.info(  # typed facts only; never the inputs (Data flow rule 6)
        "decisions.decision",
        decision_id=str(decision_id),
        point=point.value,
        outcome=outcome.value,
        provider=provider,
        fallback=fallback,
        fallback_reason=reason,
        cached=asked.cached,
        model_version=model_version,
        latency_ms=latency,
    )
    return Decision(
        decision_id=decision_id,
        point=point,
        route=outcome,
        value=value,
        answers=answers,
        provider=provider,
        fallback=fallback,
        fallback_reason=reason,
        model_version=model_version,
        cached=asked.cached,
    )


async def record_outcome(
    decision_id: UUID,
    *,
    overridden: bool,
    final_value: Any,
    outcome_at: datetime | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Record what the human did with the decision on its log row (FR-11.5): whether they
    overrode it, the value they chose and when (`outcome_at`, now when not given).
    Idempotent: writing the same outcome again changes nothing."""
    t = DecisionLog
    stmt = (
        update(t)
        .where(t.id == decision_id, t.deleted_at.is_(None))
        .values(
            overridden=overridden,
            final_value=null() if final_value is None else final_value,
            outcome_at=outcome_at or SystemClock().now(),
        )
    )
    if session is not None:
        await session.execute(stmt)
        return
    async with tenant_session(_context()) as s:
        await s.execute(stmt)


# --- Blocking impact of a review item (P1-13, FR-11.4) -----------------------------------


async def assess_blocking_impact(
    item_id: UUID, *, providers: Providers | None = None, clock: Clock | None = None
) -> Decision | None:
    """Worker-only. Asks `blocking_impact` about an open review item (subject
    `review_item`) with what it blocks (tasks' `review_impact_facts`), and stores the
    factor the answer gives the item's order (`tasks.jev_factor`: 1.0 unless the decision
    applied) with the decision's id. With no provider answering nothing is written, so the
    factor stays 1.0 and the deterministic impact alone orders the queue. None when the
    item is no longer open."""
    ctx = _context()
    clock = clock or SystemClock()
    async with tenant_session(ctx) as s:
        facts = await tasks.review_impact_facts(s, item_id)
    if facts is None:
        return None
    inputs: dict[str, Any] = {
        "item_kind": facts.kind,
        "item_summary": facts.target_title or facts.kind.replace("_", " "),
        "downstream_task_count": facts.tasks,
        "downstream_human_minutes": facts.minutes,
    }
    if facts.project_name is not None:
        inputs["project_name"] = facts.project_name
    if facts.nearest_due is not None:
        inputs["nearest_due_in_days"] = (facts.nearest_due - clock.now().date()).days
    decision = await decide(
        DecisionPoint.BLOCKING_IMPACT,
        inputs,
        subject=SubjectRef(type="review_item", id=item_id),
        project_id=facts.project_id,
        providers=providers,
        clock=clock,
    )
    if decision.provider == "none":
        return decision
    answer = decision.answers.get(CATALOGUE[DecisionPoint.BLOCKING_IMPACT].main_question)
    score = answer if isinstance(answer, ScoreAnswer) else None
    await tasks.set_review_jev(
        item_id,
        jev_factor=tasks.jev_factor(score, decision.route.value),
        decision_id=decision.decision_id,
    )
    return decision
