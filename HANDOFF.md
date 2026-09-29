# HANDOFF: P1-01 (Decisions slot with Jev)

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p1-01`, branch `wp/P1-01`. Main (P0-18, bcbae03) is merged in at f3e83a8. Nothing is pushed.

## Done (commits on wp/P1-01)

| SHA | What |
| --- | --- |
| 6f9a7bd | pins `typesafe-sdk==0.7.2` and `httpx2==2.13.1` (backend/pyproject.toml, uv.lock) |
| 9e9098b | red spec tests T-P1-01-01..16, snapshot inputs, `adapters/port.py` (protocol + typed answers) |
| 0bae681 | `catalog.py` (nine points) and `rules.py`; T-01..03 green |
| 1e4b1ea | `build_request`; T-04, 05, 06, 13 green |
| bf627e5 | golden `*.payload.json`; T-07 and T-14 green |
| 12ef207 | `adapters/jev.py` (JevDecisions, `wire_body`, `UnpinnedModel`), `adapters/fake.py`, registration, 12 recordings; T-08, 09, 10, 12, 16 green |
| 5714d67 | `limiter.py`; T-11 green (+ a credential-fingerprint test) |
| bfca4b1 | `migrations/0001_provider_configs.py` (revision `decisions_0001`, branch `decisions`), `models.py`, `api.py` (`ProviderConfigIn`, `ProviderConfig`, `put/get_provider_config`, `ask_raw`, re-exported answer types); `row_factory.COLUMN_VALUES[("provider_configs","slot")]`; T-15 green |
| 169022c | `.importlinter`: contracts `typesafe-sdk-in-jev-only` and `api-never-calls-out` |
| 642e954 | restores `tests/recordings/.gitkeep` (repo-layout meta test) |
| a01fd33 | edge tests: `test_catalog_edges.py`, `test_fake_provider.py`, `contract/test_jev_errors.py`; `rules.py` and `catalog.py` at 100% lines |
| b6ead78 | `scripts/record_jev.py` (manual recorder; dry-run verified against the replay transport, never called the real API) |

All 16 spec tests are green; no `spec:P1-01` marker is left.

Verified at a01fd33: `make check` exit 0 (882 unit passed, Vitest 50 passed); `make gen` produced no diff; decisions unit + contract 75 passed; decisions integration (T-15) passed; `lint-imports` 8 contracts kept (and a probe showed the new ones bite).

## Remaining steps, in order

1. Run the full contract and integration layers from `backend/` (not yet run in full on this branch; `frontend/dist` does not exist here):
   `uv run pytest -m contract` and `uv run pytest -m integration -n auto`. Watch A0.3 isolation (`tests/acceptance/test_a0_3_tenant_isolation.py`) and the table registry with the new `provider_configs` table.
2. Add P1-01's names to Part A (A12 table) of `docs/IMPLEMENTATION-PLAN-DETAILED.md`: revision `decisions_0001` (`provider_configs` with `ck_provider_configs_slot`, `ux_provider_configs_ws_slot`); `decisions/{catalog,limiter}.py`, `adapters/{port,jev,fake}.py`; `catalog.build_request/estimate_tokens/OutboundRequest/questions_for/CATALOGUE/MissingDecisionInput/InvalidDecisionInput/TooManyOptions/DecisionRequestTooLarge/MAX_REQUEST_TOKENS/MAX_STATE_PLUS_QUESTION_TOKENS`; `rules.label_reason/option_key/option_position/is_pinned_model`; `api.ProviderConfigIn/ProviderConfig/put_provider_config/get_provider_config/ask_raw/credential_aad`; `limiter.SlidingWindowLimiter/limiter_for/credential_fingerprint`; `jev.JevDecisions/wire_body/UnpinnedModel/JEV_POLICY`; `FakeDecisions.script/set_health/reset/calls`; import-linter contracts `typesafe-sdk-in-jev-only`, `api-never-calls-out`; `scripts/record_jev.py`. Commit `docs: P1-01 names in Part A (A12)`.
3. Re-run `make check`, then write the final report.

## Decisions and deviations (put these in the report)

- `build_request`, `estimate_tokens`, `OutboundRequest` and the errors live in `catalog.py`, not `rules.py`: they need `unicodedata` (NFC) and `json`, and rules files may import only the T-P0-01-09 allow-list, which is a locked test. `rules.py` keeps `label_reason`, option keys and `is_pinned_model`.
- The protocol's short name is `provider` (not `name`): the adapter base already uses `name` for the registry name `decisions.jev`.
- The DTOs and the protocol live in `adapters/port.py` (AGENTS: ports live there); `api.py` re-exports them.
- `FieldRule` gained `item_fields` (the whitelist inside a `record_list`); `ChoiceDef.criteria` values may be JSON objects (project options).
- `JevDecisions(api_key, pinned_model, *, clock, transport=None, base_url=None, policy=JEV_POLICY)`: clock is injected (adapter base). One attempt per ask (`RetryPolicy(max_attempts=1)`, SDK retries off); ceiling timeout 10 s, per-point timeout passed to the SDK. Error map: 429 and 5xx, 408 and connection errors are `AdapterUnavailable` (429 carries Retry-After), SDK timeout is `AdapterTimeout`, other 4xx and client-side SDK errors are `AdapterRejected`.
- The registry's real factory (`adapters/__init__.build_jev`) imports `jev.py` with `importlib`, so no static import reaches the SDK from the app; `api-never-calls-out` checks `tumnis.*`, and every module's api, router and mcp as single modules (a plain `tumnis.app` source breaks the lint-tree meta tests, which rewrite the config into a tree without `app`).
- The SDK's `typesafe_sdk` logger is pinned to INFO in `jev.py` (it logs bodies at DEBUG).
- Only `decisions.jev` is registered. `decisions.vllm` (plan: the fake also under that name) waits for P1-02, because every registered adapter needs a real or recorded contract class.
- Point timeouts (plan defaults, not given in the plan): quick_add_label 800 ms, focus_on_task and nudge_warranted 1,500 ms, the rest 2,000 ms. Required fields were chosen per point (see `catalog._SPECS`).
- The 12 recordings are synthetic (hand-authored in the SDK 0.7.2 wire shape, `"synthetic": true`, `recorded_at: null`); nothing called the real, paid API.
- Seed `provider_configs` rows were not added: the seed loader's counts are asserted exactly by a locked test (`tests/harness/test_harness_unit.py`, `result.counts == {...}`), so a new seed kind needs Scott's `spec-change`.
- No `POST /v1/test/fakes/{adapter}/script` route: it does not exist yet; the fake is scripted in-process (`fakes["decisions.jev"].script(...)`).
- The limiter is per process; the `decisions` DBOS queue limiter (A9) bounds the fleet.

## For Scott

- Run `TYPESAFE_API_KEY=... uv run --directory backend python ../scripts/record_jev.py --point all` once, review and commit, to replace the synthetic recordings (Done checklist: "Recorded fixtures cover every FR-11.4 decision point").
- The SDK builds its own `httpx2` client for `api.typesafe.ai`, outside `core.net`'s SSRF guard (fixed public host; the plan specifies the SDK).
- The Phase 1 acceptance suite (`[P1] spec: acceptance suite`, label wp:P1-01, A1.1 to A1.6) does not exist on main and was not written here.
- Seed rows for `provider_configs` need a spec-change (above).

## Gotchas

- Shared-file edits so far: `backend/pyproject.toml` and `backend/uv.lock` (the two pins), `backend/.importlinter` (two contracts appended), `backend/tumnis/core/tests/integration/row_factory.py` (one `COLUMN_VALUES` entry).
- `rtk` filters pytest output; use `rtk proxy uv run pytest ...` to see results.
- The integration test T-15 needs `master_key_file` and its own `core_db` fixture (copied from core's settings-store tests).
- zsh: `echo =====` fails ("= not found"); quote it.

## Verify

```bash
cd /Users/sjordan/Projects/Tumnis-Guide-wt/p1-01
make check
cd backend
rtk proxy uv run pytest tumnis/modules/decisions -m "not integration" -q
rtk proxy uv run pytest tumnis/modules/decisions -m integration -q
rtk proxy uv run pytest -m contract -q
rtk proxy uv run pytest -m integration -n auto -q
uv run lint-imports
```
