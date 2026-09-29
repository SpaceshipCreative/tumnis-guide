# P0-18 handoff (Tasks module and state machine)

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p0-18`, branch `wp/P0-18`. Main merged in
up to PR #34 (`cf34ee5`). Binding rules: the coordinator's `prompt-footer.md`, `AGENTS.md`,
plan section `docs/IMPLEMENTATION-PLAN-DETAILED.md` lines 5192-5545.

## Done (commits)

| SHA | What |
| --- | --- |
| `e1804fe` | `test(tasks)`: all T-P0-18 spec tests red + interface stubs |
| `a1f2eb9` | `feat(tasks)`: rules.py (TRANSITIONS, check/apply_transition, estimates, layout, today order); rules.py 100% lines (extra `tests/unit/test_rules_details.py`) |
| `07c8ddf` | `feat(tasks)`: migration `tasks_0001`, models, api, router, payloads/events, review registry, `core/limits.py`, `integrations.api.get_context_item_ref`, projects PATCH `subtask_threshold_min`, `make gen` output, frontend seams replaced |
| `cf34ee5` | merge main (PR #31, #34) |
| `c6138e7` | T-P0-17-20 green; usage `_usage.create_task` calls `tasks.api`; authz `LOOKUP_TARGETS["tasks"]` |

## Spec tests

- Green (markers removed): T-P0-18-01 to 19, 21, 22 (unit + integration) and T-P0-17-20
  (= T-P0-18-20).
- Still strict xfail `spec:P0-18`: **T-P0-02-03**
  (`backend/tests/harness/test_harness_integration.py::test_tumnis_seed_loads_into_postgres`).
  Projects (3) and tasks (30) now load; it still fails only on `events: 0 != 6` because no
  module registers an `event` seed writer (calendar events need an integrations
  `connections` row; there is no API to create one). Not P0-18 scope; flag for Scott or the
  calendar WP. Do not remove the marker.

## Remaining steps (in order)

1. Run the full layers from `backend/` and fix anything red:
   `uv run pytest -m "not integration and not contract" -n auto`, `uv run pytest -m contract`
   (includes the Schemathesis fuzz `tests/contract/test_openapi_fuzz.py` over the new routes:
   watch for 5xx, e.g. NUL bytes in text fields or invalid rank keys), and
   `uv run pytest -m integration -n auto`. Already green in isolation: tasks unit and
   integration (67), `tests/meta`, `tests/isolation`, A0.3, usage, projects (402 passed).
2. `make gen && git diff --exit-code` (must be clean; it was after `07c8ddf`).
3. squawk: `cd backend && uv run python ../scripts/ci/squawk_migrations.py --changed-since main`
   (not yet run on `tasks_0001`: enums via `CREATE TYPE`, `COLLATE "C"` text columns).
4. Playwright (optional for this WP; J2 and A0.2 use `/v1/tasks`, `/columns`, `/move` but
   need UI from P0-23/P0-24): unique compose project (`-p tumnis-p018`,
   `TUMNIS_TEST_PORT=18418`, `E2E_BASE_URL`), tear down after; move `frontend/dist` aside
   before backend layers (none exists now).
5. `git merge main` again before finishing; final report (shared-file edits listed below).

## Decisions and deviations

- Payload models live in `tasks/payloads.py` (re-exported by `events.py`): `events.py`
  calls `api.ensure_default_columns`, and `api.py` emits the payloads, so keeping both in
  `events.py` made an import cycle the `module-graph-acyclic` contract refuses.
- `human.decided` (R-07) payload model is defined (schema committed) but nothing emits it
  yet (P1-13/P2-05 do).
- `task.status_changed.actor` is the ActorRef string (`user:<id>`, `api_key:<id>`,
  `system`); the field `from` is `from_` in Python (alias, serialized by alias).
- `add_review_item` takes an optional `session=` (plan signature has none; "runs in the
  caller's transaction"); without it, it opens a transaction in `tenancy.current()`.
- The open-dedupe index is `WHERE decided_at IS NULL AND deleted_at IS NULL` (plan says
  `decided_at IS NULL`; a trashed item is not open).
- Default columns: subscriber `tasks.create_default_columns` on `project.created`, plus a
  lazy, advisory-locked `ensure_default_columns` in every read/write that needs columns
  (the worker may not have run yet). GET `/columns` and `/board` may therefore write.
- `link_context_item` returns the link (`TaskContextItemOut`), not None.
- No extra GET routes for comments/context items (plan lists POST only).
- Label source by actor: human `user`, agent `agent`, system `fallback`; task `source`:
  `user`/`agent`/`system`, seeds `seed`. Seed priorities 0-3 map to low..urgent; seeds are
  inserted in their given status (no state machine), label_source `user`.
- Review routes (`/v1/review/count`, `/v1/review/kinds`) are session-only; task, board and
  column routes take a session or a key (`tasks:read` / `tasks:write`); task routes use
  `project_param="lookup:tasks"`.
- `ProjectPatch`/`ProjectOut` gained `subtask_threshold_min` (needed by T-P0-18-11).
- T-P0-18-05 is `test_lifecycle_pg.py::TestTaskLifecyclePg::test_random_lifecycles_match_the_matrix_on_postgres`
  (a pytest class running `run_state_machine_as_test`, since a unittest TestCase cannot take
  fixtures); it reads rows back as the owner via psycopg instead of `owner_session`.

## Shared-file edits

- `backend/.importlinter`: `search-usage-subscribe-only` gains
  `ignore_imports = tumnis.modules.usage.tests.** -> tumnis.modules.tasks.api`.
- No edits to `pyproject.toml`, `uv.lock`, `Makefile`, `AGENTS.md`, `frontend/package.json`.
- Other modules touched: `integrations/api.py` (`get_context_item_ref`), `projects/api.py`
  (threshold field), `usage/tests/integration/_usage.py`, `tests/meta/_authz.py`,
  `core/tests/integration/row_factory.py` (COLUMN_VALUES for `board_columns.sort_key`,
  `board_columns.status_map`, `tasks.board_rank`, `review_items.kind`).
- Not done: Part A (A12) additions in the plan for the new names (payloads.py,
  `get_context_item_ref`, subscriber, `lookup:tasks`, row_factory values). AGENTS DoD asks
  for it; add a P0-18 row to A12 if the coordinator wants plan edits in WP branches.

## Gotchas

- Postgres revision: `tasks_0001` (branch `tasks`, depends on `auth_0001`,
  `projects_0001`, `integrations_0001`). No core revision added.
- `rules.py` may not import `types` (boundary meta-test allow-list), hence a plain dict
  typed `Mapping` for `TRANSITIONS`.
- P0-30's rollback-rehearsal data smoke POSTs `/v1/projects` with a key, but project writes
  are session-only: it will 403 there (needs a key-capable path or a session; for Scott).
- Known flakes not to chase: #35 (psycopg ResourceWarning), #36 (csp.spec.ts).

## Verify

```bash
cd backend
uv run pytest tumnis/modules/tasks -m "not integration" --cov=tumnis.modules.tasks.rules --cov-report=term-missing -p no:xdist
uv run pytest tumnis/modules/tasks tumnis/modules/projects tumnis/modules/usage -m integration -n auto
uv run pytest tests/meta tests/isolation tests/acceptance/test_a0_3_tenant_isolation.py -n auto
cd .. && make check && make gen && git diff --exit-code
```
