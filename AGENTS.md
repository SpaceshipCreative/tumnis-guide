# AGENTS.md

Rules for every coding agent (and human) working in this repo. They are binding. Read this file before writing code; you should not need the implementation plan to follow them.

- Product requirements: `docs/PRD.md`. Architecture: `docs/ARCHITECTURE.md`. Decisions: `docs/adr/` (read the ADR before re-deciding anything it covers; changing a decision means a new ADR).
- Work packages (WPs): `docs/plan/work-packages.yaml` (IDs, needs, traces) and `docs/IMPLEMENTATION-PLAN-DETAILED.md` (files, interfaces, spec tests and TDD sequence per WP).

## TDD rules

Spec tests come first and are locked. Code exists only to turn a red test green.

1. **Double loop.** Each phase's acceptance suite is committed red on its first day; each WP's spec tests are committed red before its code. A phase closes when its acceptance suite is green.
2. **Red is committed first, in its own PR.** Branch `wp/<WP>-spec`, PR title `[<WP>] spec: <title>`. Every spec test from the WP's table lands as an expected failure:
   - pytest: `@pytest.mark.xfail(strict=True, reason="spec:<WP>")` (with `xfail_strict = true`, a marked test that passes fails the build)
   - Vitest: `test.fails('[<WP>][<REQ>] …', …)`
   - Playwright: `test.fail()` inside the test
3. **Implement one marker at a time.** On `wp/<WP>-impl` (more `-impl-2` for large WPs), follow the WP's TDD sequence: remove one `spec:` marker, watch the test fail for the right reason, write the smallest code that makes it pass, commit the green, refactor while green. Remove a marker the moment its test passes.
4. **Never edit an assertion in an existing test, and never delete a test.** spec-guard fails any PR that does, unless Scott adds the `spec-change` label. Removing a `spec:` marker is allowed; generated contract tests are exempt. If a spec test looks wrong, stop and ask; do not "fix" it.
5. **Bug fixes start with a failing test named after the issue.** On `bug` PRs, red-proof runs the new test against the base commit and requires it to fail there.
6. **Every test names its requirement and its WP** (see Tests and markers). The traceability job fails when a requirement in a finished phase has no test.
7. **Rules are pure; time is an input.** Business rules live in `rules.py` as pure functions with unit tests (see Time).
8. **Fakes obey the real contract.** Every adapter has a fake, and one contract suite runs against both the fake and the real implementation (container or recordings).
9. **Durability is tested by killing.** Every DBOS workflow has a kill-and-resume test: kill the worker at a named step, assert it resumes at the right step and finishes once.
10. **Real Postgres everywhere.** Integration tests run on `pgvector/pgvector:pg18`, with the DBOS system database on the same server. Never SQLite, never mocks of the database.
11. **Sweeps beat checklists.** Meta-tests walk every route, table and MCP tool; a new one is covered automatically or the sweep fails. Model behavior is tested on JSON: skill tests assert tool calls and JSON, three runs per case, all must pass.
12. **Flaky means broken.** Quarantine a flaky test within a day with an issue, fix it within a week.

**Definition of done (every WP):** spec tests merged red first; all green with markers removed; no spec-guard override without Scott's label; requirement and WP tags on every new test; coverage gates hold (80% on rules and the MCP layer; full coverage on planner, focus, threshold and state-machine rules); a new adapter ships with its fake and contract suite; a new module ships with its import-linter contract; generated OpenAPI, schemas and client committed; UI changes checked at phone width (375 px); new shared names added to Part A of the plan.

## Module boundaries

The backend is a modular monolith (ADR-0001): fifteen modules in `backend/tumnis/modules/` on a shared `backend/tumnis/core/`. The registry `backend/tumnis/core/modules.py` (`MODULES`) is the one place a module is listed.

Four boundary rules:

1. A module imports another module only through `tumnis.modules.<name>.api`. Everything else in a module is private.
2. No module reads or writes another module's tables. Cross-module reads go through `api.py`; cross-module reactions go through outbox events.
3. `rules.py` imports nothing that does I/O.
4. The dependency graph between modules has no cycles; `search` and `usage` only subscribe to events and call no other module.

`backend/.importlinter` enforces them in CI (`uv run lint-imports`):

| Contract | Checks |
| --- | --- |
| `modules-api-only` | Modules are independent except for imports of another module's `api` |
| `rules-are-pure` | `tumnis.modules.*.rules` imports no db, tenancy, outbox, events, cache, net, adapters or audit from core; no `models`, `workflows`, `adapters`, `router` or `mcp`; no sqlalchemy, psycopg, dbos, fastapi, httpx, aioboto3, asyncssh, redis or structlog |
| `core-below-modules` | `tumnis.core` never imports `tumnis.modules`, `tumnis.app`, `tumnis.worker`, `tumnis.cli` or `tumnis.wiring` |
| `module-graph-acyclic` | No cycles between sibling modules |
| `search-usage-subscribe-only` | `search` and `usage` import no other module |

A meta-test also parses every `rules*.py` and allows only pure stdlib (`datetime`, `zoneinfo`, `typing`, `dataclasses`, `enum`, `collections`, `itertools`, `functools`, `math`, `decimal`, `fractions`, `re`, `uuid`, `bisect`, `heapq`), `pydantic`, `tumnis.core.types` and the module's own `rules*`.

The composition roots (`tumnis.app`, `tumnis.worker`, `tumnis.cli`, `tumnis.wiring`) may import every module's `router`, `mcp`, `workflows` and `events` to wire them. Keep module `__init__.py` files empty: a re-export there would bypass the api-only rule.

A new module arrives with its tests, fakes, its registry entry and its import-linter contract, or it does not merge. Create it with `scripts/new_module.py <name>`.

## Where things go

Every module has the same shape:

| File | Holds | May import |
| --- | --- | --- |
| `api.py` | Public functions and DTOs; the only import other modules may use | own module, `tumnis.core`, other modules' `api` |
| `router.py` | FastAPI router under `/v1/<module>`; thin calls into `api.py` | `api.py`, core |
| `mcp.py` | MCP tools; thin calls into `api.py` | `api.py`, core |
| `models.py` | SQLAlchemy tables owned by the module | core |
| `rules.py` | Pure functions, no I/O, time passed in | stdlib, pydantic, own `rules`-only helpers |
| `workflows.py` | DBOS workflows and steps | `api.py`, `adapters`, core |
| `events.py` | Event payload models and subscribers | `api.py`, core |
| `adapters/` | One file per outside dependency, each with `fake.py` | core |
| `migrations/` | Alembic revisions for this module's tables | none at runtime |
| `tests/unit` | Rules and pure code; sockets disabled | anything in the module |
| `tests/integration` | Postgres, DBOS, containers | anything |
| `tests/contract` | Adapter and connector contract suites, schema tests | anything |
| `tests/recordings` | Scrubbed provider payloads + expected canonical records | data only |

Elsewhere: core's own tests in `backend/tumnis/core/tests/{unit,integration}`; cross-module suites (meta, isolation, acceptance) in `backend/tests/`; shared fixtures in `backend/tests/conftest.py`; seed and test data in `backend/fixtures/`; frontend code in `frontend/src/` with Playwright specs in `frontend/e2e/`; the runner daemon in `daemon/`; Hermes profiles and skill tests in `profiles/`; generated JSON Schemas in `schemas/`; compose and container config in `deploy/`; CI and drill scripts in `scripts/`; ADRs in `docs/adr/`.

## Tests and markers

**Test IDs.** Every spec test has an ID `T-<WP>-<nn>` (for example `T-P0-18-04`) as the first line of its docstring (pytest) or in its title (Vitest, Playwright). Name test functions for behavior: `test_agent_cannot_move_backlog_to_done`.

**pytest markers** (registered with `--strict-markers`; an unregistered marker is an error):

| Marker | Use |
| --- | --- |
| `req(*ids)` | PRD requirement IDs the test proves, for example `@pytest.mark.req("FR-3.1", "REL-2")` |
| `wp(id)` | Work package that introduced the test, for example `@pytest.mark.wp("P0-18")` |
| `integration` | Needs Postgres or containers; sockets allowed. Put `pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]` at module level |
| `contract` | Adapter, connector or schema contract |
| `slow` | Over 5 seconds |

**Spec tests waiting for code:** `@pytest.mark.xfail(strict=True, reason="spec:<WP>")`. The `spec:` prefix is what spec-guard and the traceability job look for; do not change its form.

```python
@pytest.mark.req("FR-3.1")
@pytest.mark.wp("P0-18")
@pytest.mark.xfail(strict=True, reason="spec:P0-18")
def test_agent_cannot_move_backlog_to_done() -> None:
    """T-P0-18-04"""
```

**Vitest:** tags in brackets in the title: `test.fails('[P0-25][FR-3.10] replays each queued item once', …)`; `test.fails` flips to `test` when the code lands.

**Playwright:** tags through the details object: `test('quick-add from any route', { tag: ['@J2', '@FR-3.3', '@P0-05'] }, async ({ page }) => { test.fail(); … })`. Every spec runs in the `phone` (375 x 812) and `laptop` (1280 x 800) projects.

**Layers:** unit (`-m "not integration and not contract"`, sockets disabled), contract (`-m contract`), integration (`-m integration -n auto`), frontend (Vitest), end to end (Playwright against the seeded app with fakes), skills (homelab runner).

## Adapters and fakes

Placeholder: the adapter template (base class, timeout, circuit breaker, retry, fake and contract suite layout) is added in P0-09. Until then: every outside dependency sits behind an adapter in `<module>/adapters/` with a `fake.py` beside it, and `TUMNIS_ADAPTERS=fake` selects the fakes.

## Time

- Rules take `now: datetime` (UTC, timezone-aware) and `tz: ZoneInfo` as arguments. Nothing in `rules.py` calls `datetime.now()`, `datetime.utcnow()`, `date.today()`, `time.time()`, `random`, `uuid.uuid4()`, `os`, `subprocess` or `socket`; a Ruff banned-API rule (TID251) enforces it.
- Code that does I/O gets the time from the `Clock` protocol in `tumnis/core/clock.py` (`SystemClock` in production, `FixedClock` in tests), never from the system directly.
- Store timestamps as `timestamptz` in UTC; show them in the workspace timezone. Durations are whole minutes (`estimate_minutes`), sizes are bytes.
- Local times go through `local_to_utc(day, local_time, tz)` in `tumnis/core/clock.py`: a time in a DST gap shifts forward by the gap, an ambiguous time takes the first occurrence.
- DBOS timeouts that tests must reach take a configurable duration, not a `FixedClock`.

## Commands

| Command | Does |
| --- | --- |
| `make check` | Lint (Ruff check and format, mypy, import-linter, ESLint, `tsc`) and the unit tests. Run before every commit; must finish under 60 s |
| `make test-int` | Integration tests (Postgres, DBOS, containers) |
| `make e2e` | Playwright end-to-end suite against the seeded app with fakes |
| `make gen` | Regenerate OpenAPI, JSON Schemas and the TypeScript client; commit the result |

Some targets are filled in by later WPs; if a target is still empty, say so rather than working around it.

## Never

- Never edit `frontend/src/api/`: it is generated by `make gen`.
- Never call out to the network from the api process; it writes and enqueues, and the worker makes every outbound call.
- Never read or write another module's tables, or import anything but another module's `api`.
- Never use `datetime.now()` (or any clock, randomness or I/O) in `rules.py`.
- Never store secrets in environment variables; the only secret-related variables are file paths (`MASTER_KEY_FILE`, `API_KEY_PEPPER_FILE`, `METRICS_TOKEN_FILE`). Per-workspace secrets live encrypted in Postgres.
- Never edit an assertion in, or delete, an existing test; never add the `spec-change` label.
