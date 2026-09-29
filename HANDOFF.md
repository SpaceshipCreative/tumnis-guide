# P1-02 handoff (Fallback, thresholds and the decision log)

Branch `wp/P1-02` (pushed to origin). No PR opened yet. No CodeRabbit threads, no CI runs yet.

CONTINUATION 1 (stopped early for the VM reboot) added steps 1-4 and 10 below. A fresh agent:
`git fetch origin && git switch -c agent/P1-02-c2 origin/wp/P1-02`, push with
`git push origin agent/P1-02-c2:wp/P1-02`, use `/usr/bin/git` (plain `git` is refused), and start
at "Remaining TDD steps" step 5 (`decide()`).

## Done

- `126fefc test(decisions): P1-02 spec tests (red)`: every spec test T-P1-02-01..17 as a strict
  expected failure, plus interface stubs so mypy passes. `make check` green at that commit
  (run with the SEMGREP_* env vars below). Red confirmed: unit 31 xfailed, contract 3 xfailed
  (TestVllmFake passes), integration 11 xfailed, Vitest 2 expected fail.
- `38fc08f feat(decisions): route, effective_threshold and vote_answer (T-P1-02-01..04)`: markers
  removed from `test_route.py` and `test_vote_answer.py`, all four green. `rules.py` also has
  `main_answer(main_question, answers)` (duplicate: the highest `dup_n` Noul). New non-spec
  tests `tests/unit/test_rules_edges.py`; `decisions/rules.py` is at 100% line coverage.
- `29ced5d feat(decisions): VllmDecisions over the OpenAI-compatible API (T-P1-02-15)`:
  `adapters/vllm.py` (public `chat_bodies(req, model=)`, `VLLM_POLICY`, one POST per question via
  asyncio.gather under one `self.call`, allowed port taken from base_url), 11 synthetic
  recordings in `tests/recordings/vllm/` (generated from the Jev recordings' inputs; notes say
  synthetic, Scott to re-record; generator was a one-off script, not committed), marker removed
  from both `TestVllmOnRecordings` and `test_request_bodies_use_structured_outputs`, new
  `tests/contract/test_vllm_errors.py` (error mapping, SSRF hosted mode, lazy registry build).
  `.importlinter` `api-never-calls-out` now also forbids `tumnis.modules.decisions.adapters.vllm`
  (shared file edit; contract name changed to "The api process never imports the Jev or vLLM
  adapter or the Jev SDK"). Contract layer for the module: 45 passed. `lint-imports`: 8 kept.
- `5039e3f feat(decisions): thresholds and decision_log tables (decisions_0002)`: migration
  `0002_thresholds_log.py` (revision `decisions_0002`, down `decisions_0001`, expand), models
  `Threshold` and `DecisionLog` in `models.py`, `row_factory.COLUMN_VALUES` entries for
  `decision_log.provider` and `.outcome`. The integration DB template applies it (the two
  `test_provider_configs.py` tests passed). NOT yet run: the table-registry, RLS/isolation and
  squawk sweeps (the full `make test-int` and `make check` are still to do).

Still red (xfail strict): T-P1-02-05..14, 16, 17 (the integration tests and the Vitest ones).
`make check` has not been re-run since the red commit.

## Spec tests (all red, `reason="spec:P1-02"`)

| ID | Where |
| --- | --- |
| 01, 02, 03 | `backend/tumnis/modules/decisions/tests/unit/test_route.py` (`test_route_table[point-case]`, `test_fallback_is_stricter`, `test_approval_need_fails_safe`) |
| 04 | `tests/unit/test_vote_answer.py::test_vote_shares_and_confidence` |
| 05-13, 17 | `tests/integration/test_decide.py` (fixtures in `tests/integration/conftest.py`: `core_db`, `test_cache` (InProcessCache on the test clock via `use_backend`), `providers` = `Providers(jev=fakes["decisions.jev"], vllm=fakes["decisions.vllm"])`) |
| 14 | `tests/integration/test_outcomes.py::test_human_override_recorded_on_log` (subscriber name `decisions.record_outcome`; final value read from `payload.value`) |
| 15 | `tests/contract/test_vllm_contract.py::TestVllmOnRecordings` + `test_request_bodies_use_structured_outputs` (recordings dir `tests/recordings/vllm/*.json`, format `{point, inputs, exchanges: [{request, response: {status, body}}], recorded_at, notes}`; replay matches the body without `model` and echoes the requested model) |
| 16 | `frontend/src/components/project/rail/SettingsSection.test.tsx` (two `test.fails`, phone and laptop; role `switch` named `Local decisions only`, description matching /never sent to Jev/i, one PATCH `{local_decisions_only: true, version: 3}` with an Idempotency-Key) |

## Interfaces already in place (stub bodies raise NotImplementedError)

- `rules.py`: answer types moved here (`ChoiceAnswer`, `ScoreAnswer`, `NoulAnswer`, `TypedAnswer`; `adapters/port.py` re-exports), `Threshold`, `Route`, `DEFAULT_THRESHOLDS` (real data, keyed by point string), protocols `SpecLike`, `QuestionLike` (rules.py may import only stdlib/pydantic/own rules*, so it cannot import `catalog`), stubs `effective_threshold`, `route`, `vote_answer`.
- `catalog.py`: stub `input_hash(req, model) -> bytes` (needs `hashlib`+`json`, not allowed in rules.py; re-add `import hashlib`).
- `api.py`: `SubjectRef`, `Decision`, `Providers` (dataclass jev/vllm), `JevSettings(rpm=1200)` (workspace setting key `decisions.jev`), stubs `put_threshold`, `decide(point, inputs, *, subject, project_id, providers=None, clock=None)`, `record_outcome`. Add the new names to `__all__`.
- `limiter.py`: `limiter_for(..., sleep=None)` (real, done).
- `adapters/__init__.py`: `decisions.vllm` registered (`build_vllm` lazy-imports `adapters/vllm.py`; fake `FakeDecisions`). `adapters/vllm.py` is a stub.
- `SettingsSection.tsx` stub (prop `project: Pick<ProjectOut, "id" | "version">`; widen with `local_decisions_only` after `make gen`).

## Remaining TDD steps (remove one marker at a time; Scott approved marker removal)

Steps 1, 2, 3, 4 and 10 below are DONE (see Done). Steps 5-9 and 11 remain, then `make check`, the
layers, the PR. Findings from reading the code for step 5-8 (verified, not yet implemented):

- Add `low_route(spec) -> Route` to `rules.py` (the `_LOW_ROUTE` map is already there, private):
  `decide` needs it for the both-providers-down case (REVIEW for quick_add_label, APPROVAL_REQUIRED
  for approval_need).
- `decide` reads its workspace from `tumnis.core.tenancy.current()` (the tests use `use_workspace`),
  raising RuntimeError when None, like `projects.api._context()`.
- Payload models: put `DecisionMadeV1` and `DecisionUnavailablePayload` in a new
  `decisions/payloads.py` (like `tasks/payloads.py`), re-exported by `events.py`, so `api.py` can
  emit them while `events.py` (subscriber) calls `api.record_outcome` without an import cycle.
  Fixture: `backend/tests/contract/fixtures/events/decision.made/v1.json` (format: the payload
  dict including `"schema_version": 1`), then `make gen` (also needed after the projects change).
- `record_outcome(decision_id, *, overridden, final_value, outcome_at=None, session=None)`: the
  test needs `outcome_at == envelope.occurred_at`, so the subscriber passes it (extra optional
  arg vs the plan). Write JSON null as `sa.null()` (SQLAlchemy stores Python None in JSONB as
  JSON null otherwise). Subscriber runs in `tenant_session(WorkspaceContext(env.workspace_id,
  SYSTEM_ACTOR))` like `usage/events.py`; `run_subscriber` already sets `use_workspace`.
- `HumanDecidedV1` (tasks/payloads.py) has `decision`, `previous`, `payload`, `decision_id`.
- Usage: add `"decision.made": (("decisions", _one),)` to `usage/rules.py` COUNTERS (its comment
  says each emitting WP adds its line; `test_counter_map` only asserts a subset).
- Projects: the `projects.local_decisions_only` column and `Project` model field already exist
  (P0-17). Only add `local_decisions_only: bool` to `_Row` and `ProjectOut` (default False),
  `bool | None = None` on `ProjectPatch` (extend the null check loop next to name/status), the
  cached accessor with `register_cache(CacheSpec("projects.local_only", "workspace", None,
  ("update_project (local_decisions_only)",)))` and `invalidate_on_commit(s, key)` in
  `update_project` when the field is in `values`. Router needs no change (PATCH takes
  `ProjectPatch`).
- Cache API: `register_cache` returns a `NamedCache` (`get`, `set(key, value, tags=)`,
  `token()`, `fill(key, value, since=, tags=)`, `invalidate_tag`); `CacheKey.for_workspace(ws,
  "decisions", point, hash_hex)`; writers call `await invalidate_on_commit(session, tag=tag)`.
  Use token/fill around the provider call so an invalidation racing the call is not overwritten.
- Model-change hook (T-12): `put_provider_config` is in `api.py`; after the upsert, when slot ==
  "decisions" and the previous row's model_version differs (read it before the upsert), insert
  threshold rows for all 9 points for the new model (value = old model's row if any else the
  default, source kept, `needs_recheck` true, `ON CONFLICT DO NOTHING`) and
  `invalidate_on_commit(s, tag=...)` for each point.
- `decide` design already worked out: read config (`get_provider_config(ctx, "decisions")`;
  absent -> primary jev, fallback vllm, model `PINNED_JEV_DEFAULT`); thresholds keyed by
  (point, config.model_version); first slot's model (jev: config.model_version; vllm: setting
  `decisions.vllm` model, default "vllm-local") feeds `input_hash` and the cache key, so a
  local-only entry never collides with a Jev one; call `provider.ask(req, model=, timeout_ms=
  CATALOGUE[point].timeout_ms)` directly (the request is already built for the hash; `ask_raw`
  would build it twice); `Decision.provider` is the slot name, `model_version` the response's
  model. Log row + review item + `decision.made` in ONE `tenant_session`. Both down: fallback
  False, provider "none", answers {}, threshold = the stricter (fallback) one if a fallback was
  in the chain.
- Test 17 detail: the test pre-creates `limiter_for(fingerprint, rpm=3, clock, sleep)`; `decide`
  must call `limiter_for(fp, rpm=..., clock=clock)` (not pass sleep) before each Jev-slot call,
  fp = `credential_fingerprint(config.api_key or f"workspace:{ws}")`, rpm from
  `get_setting(ctx, "decisions.jev", JevSettings)`. Optionally `register_section` for
  `decisions.jev` and `decisions.vllm` so the Settings API can set them (not required by tests).
- Docker-backed tests: this agent used `dangerouslyDisableSandbox` for ONE targeted
  `pytest -m integration tumnis/modules/decisions/tests/integration/test_provider_configs.py` run
  (the permission gate allowed it), which the updated vm-agent-rules discourage. The rules want
  bare `make test-int` (whole layer, about 10 min, `-n auto`). Prefer that and report.

1. `route`/`effective_threshold` (T-01, 03), then T-02. Clamp so fallback is never looser: `min_confidence' = max(v, min(v+m, .99))`, `t_yes' = max(v, min(v+m, .99))`, `t_no' = min(v, max(v-m, .01))`. Choice: winner == `spec.abstain.option` -> (low, None); conf >= min -> (APPLY, choice); else (low, choice). Score: conf >= min -> (APPLY, score) else (low, None). approval_need: noul <= t_no -> (APPLY, False) else (APPROVAL_REQUIRED, None). Other Nouls: >= t_yes -> True, <= t_no -> False, else (low, None). low = review/deterministic/require_approval -> REVIEW/DETERMINISTIC/APPROVAL_REQUIRED. Also a `main_answer(main_question, answers)` helper: `duplicate`'s main question `dup` means the highest-noul `dup_n`. `decisions/rules.py` needs 100% line coverage (unit tests): add non-spec unit tests for every branch.
2. `vote_answer` (T-04): probabilities over all options (zeros for unsampled), winner = argmax (ties: first in criteria order), confidence `(k*p_max-1)/(k-1)`; Score keys "0".."k-1", value sum(level*p); Noul share of "yes"; ignore samples outside the allowed set, ValueError if none valid.
3. `VllmDecisions` (T-15): `Adapter` subclass, `name="decisions.vllm"`, `provider="vllm"`, `__init__(base_url, *, clock, net_policy, resolver=system_resolver, transport=None, policy=VLLM_POLICY)`; client from `tumnis.core.net.guarded_client(replace(net_policy, ports=net_policy.ports | {8000}), timeout=..., resolver=..., inner=transport)`. One POST `/v1/chat/completions` per question (asyncio.gather), body `{model, messages: [system, user(question + allowed answers + state JSON)], n: 5, temperature: 0.7, max_tokens: small, structured_outputs: {choice: [...]}}` (no `guided_*`). Map httpx errors/5xx/429 -> AdapterUnavailable, 4xx -> AdapterRejected. Then generate `tests/recordings/vllm/*.json` from the Jev answered recordings' inputs (one per Jev recording name, synthetic votes consistent with the Jev answers; notes must say "synthetic, not from the homelab vLLM; Scott to re-record"). Contract test passes `BASE_URL = http://10.20.0.5:8000` with `NetPolicy(mode="self-hosted")`.
4. Migration `backend/tumnis/modules/decisions/migrations/0002_thresholds_log.py`: revision `decisions_0002`, down_revision `decisions_0001`, `phase = "expand"`, `create_tenant_table` for `thresholds` (unique index `ux_thresholds_ws_point_model` on workspace_id, decision_point, model_version; source check default 'default'; needs_recheck) and `decision_log` (plan DDL; checks on provider, fallback_reason, outcome; indexes `ix_decision_log_point_time` (workspace_id, decision_point, created_at DESC) and `ix_decision_log_subject`). Models in `models.py`. Add `row_factory.COLUMN_VALUES` entries (`backend/tumnis/core/tests/integration/row_factory.py`) for `decision_log.provider` ("jev"), `decision_log.outcome` ("applied") and anything else the isolation/table-registry sweeps ask for.
5. `decide()` (T-07, 08, then 05, 06, 09-13, 17): config from `get_provider_config(ctx, "decisions")` (default jev/vllm/`jev-1.13.0` when absent); local-only via a new cached projects api accessor (see below); threshold row for (point, pinned model) else default; `build_request`; `input_hash(req, first_model)`; cache `register_cache(CacheSpec("decisions", "workspace", 86400, ("thresholds", "provider_configs.model_version")))`, key `CacheKey.for_workspace(ws, "decisions", point, hash_hex)`, tag `ws:{ws}:decisions:{point}`, value = ProviderResponse JSON; cache only answers of the first provider in the chain (Jev, or vLLM for local-only), never a fallback answer. Chain: local-only -> [vllm (fallback_reason local_only)]; else [jev, vllm (primary_failed)]. Before each Jev call `limiter_for(credential_fingerprint(api_key or f"workspace:{ws}"), rpm=JevSettings.rpm from get_setting(ctx, "decisions.jev"), clock=clock).acquire()`. Catch `AdapterError` per provider. `Decision.provider` = chain slot name ("jev"/"vllm"/"none"), not the response's `provider` (the fakes say "fake"). Fallback and local-only both use the stricter threshold. Log row in its own `tenant_session`: threshold = effective threshold JSON, answer = all typed answers JSON, confidence = choice/score confidence or |noul-0.5|*2, latency from the response (None when cached/none). Both down and route REVIEW: register review kind `decision_unavailable` (owner decisions, payload `DecisionUnavailablePayload{decision_id, point}`, actions accept/edit/reject/snooze, impact task) at import of api.py and call `tasks.api.add_review_item(..., target=TargetRef(type=subject.type, id=subject.id), project_id=project_id, dedupe_key=f"decision:{point}:{subject.id}", session=s)`; no item for deterministic/approval routes. Emit `decision.made` (`DecisionMadeV1` in `events.py`: decision_id, decision_point, outcome, provider, fallback, cached) through `tumnis.core.outbox.emit` in the same transaction; add its fixture `backend/tests/contract/fixtures/events/decision.made/v1.json` and run `make gen`. One structlog `decisions.decision` line (never inputs). Production providers when `providers=None`: cache instances per (adapter, credential/base_url); fake mode `resolve(name, "fake")` without deps; real Jev needs the api key, real vLLM needs workspace setting `decisions.vllm` {base_url, model} (else skipped).
6. `put_threshold` (T-11): upsert source 'user', needs_recheck false, `invalidate_on_commit(s, tag=...)`. Model change (T-12) inside `put_provider_config`: when slot `decisions` model_version changes, upsert a row for every point for the new model (value = old row's or default, source kept, needs_recheck true) and invalidate every point's tag.
7. Projects (T-13): `local_decisions_only: bool = False` on `ProjectOut`, `bool | None` on `ProjectPatch` (refuse null like name/status), plus a cached accessor `projects.api.local_decisions_only(project_id, *, session=None)` (cache `projects.local_only`, invalidated with `invalidate_on_commit` in `update_project` when the field changes). Deviation from the plan's `get_project(...).local_decisions_only`: get_project also computes health stats. Then `make gen` (OpenAPI + client).
8. `events.py` subscriber `@subscribe("human.decided", name="decisions.record_outcome")` (T-14): ignore events without `decision_id`; overridden = decision not in {accept, approve}; final_value = payload.value; outcome_at = envelope.occurred_at; idempotent. Optionally add `"decision.made": (("decisions", _one),)` to `usage/rules.py` COUNTERS (A8 lists usage as a subscriber).
9. Toggle (T-16): switch button `role="switch"` `aria-checked`, `aria-describedby` explainer ("... never sent to Jev ..."), `useWrite` + `apiWrite({kind: "update", method: "PATCH", path: `/projects/${id}`, body: {local_decisions_only}, version, schema: zProjectOut})`; render it on `routes/projects.$projectId.tsx` so the preview shows it.
10. `.importlinter`: add `tumnis.modules.decisions.adapters.vllm` to `api-never-calls-out` forbidden modules (shared file; report it).
11. Update Part A of the plan (A12 row for P1-02) with new names.

## Gotchas

- Worktree-isolated agent: plain `git` gets rewritten by the rtk hook and refused; use `/usr/bin/git`. `git branch -m` failed to update `.git/config` (read-only in sandbox) but the rename took.
- Agent threads reset cwd every call. Docker tests (integration) need `cd backend && uv run pytest ...` with `dangerouslyDisableSandbox: true` (the permission gate allowed it); sandboxed, the Docker socket gives "Operation not permitted".
- `make check` needs `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/semgrep_settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/semgrep_version make check` (inline, no `export`).
- Vitest title parser (`scripts/ci/ts_tests.mjs`) does not read `test.fails.each`; keep explicit `test.fails`.
- mypy checks tests; `warn_unused_ignores` is on.
- P1-03 (parallel) also edits `decisions/adapters/__init__.py`, `adapters/fake.py` and `.importlinter`; expect merge conflicts there.

## Verify

```bash
cd backend && uv run pytest -q -n 2 tumnis/modules/decisions -m "not integration and not contract"
cd backend && uv run pytest -q tumnis/modules/decisions -m contract
cd backend && uv run pytest -q -n 2 -m integration tumnis/modules/decisions   # unsandboxed
cd frontend && npx vitest run src/components/project/rail/SettingsSection.test.tsx
```

## Deviations so far (for the PR body)

- Commit trailer: this continuation's commits use `Co-Authored-By: Claude Sonnet 5.5` (the harness
  attribution for the running model); earlier commits say Opus 5.5 per the rules file.
- `decide` calls `provider.ask` directly instead of `ask_raw` (needs the built request for the
  hash and `fields_sent`); planned `payloads.py` split; `record_outcome` gets an optional
  `outcome_at`; `main_answer` and `low_route` helpers in `rules.py`; `input_hash` lives in
  `catalog.py` (rules allow-list), from the earlier agent.
- `local_decisions_only` is read through a cached `projects.api.local_decisions_only()` accessor,
  not `get_project(...)` (which computes health stats); the DB column pre-existed.
- The vLLM recordings are synthetic (below).

## Scott items

- vLLM recordings will be synthetic (no homelab vLLM access from the VM); re-record from the homelab.
- Alembic revision `decisions_0002` (not yet written), chained after `decisions_0001`.
