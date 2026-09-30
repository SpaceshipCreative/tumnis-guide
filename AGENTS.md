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

A meta-test also parses every `rules*.py` and allows only pure stdlib (`datetime`, `zoneinfo`, `typing`, `dataclasses`, `enum`, `collections`, `itertools`, `functools`, `math`, `decimal`, `fractions`, `re`, `uuid`, `bisect`, `heapq`, `unicodedata`), `pydantic`, `tumnis.core.types` and the module's own `rules*`.

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

**Tables and migrations.** Every tenant table lives in `public` and is created in its module's Alembic revision with `tumnis.core.migration_helpers.create_tenant_table(name, *columns)`: base columns (`id` uuidv7, `workspace_id`, `created_at`, `updated_at`, `version`, `deleted_at`, `created_by` with `ACTOR_CHECK`), a `(workspace_id, id)` index, row-level security with the `tenant_isolation` policy and the touch trigger. `workspace_id` leads every unique or multi-column index. A table made any other way fails the table registry (`backend/tests/meta/test_table_registry.py`) unless its allow-list in `backend/tests/meta/_catalog.py` names it with a reason. Each module's first revision has `branch_labels = ("<module>",)` and `depends_on = "auth_0001"` (the `workspaces` table; add other modules' revisions it references); new revisions: `uv run alembic revision -m "..." --head <module>@head`. Every revision declares `phase = "expand"` (additive; squawk checks it in CI) or `phase = "contract"` (drops what the previous release needed). Read and write tenant rows inside `tumnis.core.tenancy.tenant_session(WorkspaceContext(workspace_id, actor))`: the app role sees only the workspace in context, and no context sees nothing.

**Routes.** Every `/v1` route is declared on `tumnis.core.routing.v1_router("<module>", prefixed=True)` (routes are `TumnisRoute`s; `create_app` includes each module's `router` under `/v1`) with `@route_policy(RoutePolicy(...))` under the route decorator; a route without a policy cannot be built, and the route registry (`backend/tests/meta/test_route_registry.py`) fails any write that is neither `idempotent=True` nor `idempotent=False` with a `not_idempotent_reason`, any bare `list[...]` response without an `unpaginated_reason`, and any `Page[...]` response not declared `paginated=True` with `cursor` and `limit`:

```python
router = v1_router("tasks", prefixed=True)

@router.patch("/{task_id}")
@route_policy(RoutePolicy(auth="session_or_key", scopes=frozenset({"tasks:write"}), idempotent=True))
async def update_task(task_id: UUID, body: TaskPatch, session: SessionDep) -> TaskOut: ...

@router.get("")
@route_policy(RoutePolicy(auth="session_or_key", scopes=frozenset({"tasks:read"}), paginated=True))
async def list_tasks(session: SessionDep, page: Annotated[PageParams, Depends(page_params)]) -> Page[TaskOut]:
    return await paginate(session, stmt, keys=[SortKey(t.c.due_on, nulls_last_sentinel=date.max)],
                          id_col=t.c.id, cursor=page.cursor, limit=page.limit, model=TaskOut)
```

An idempotent write runs in one transaction with its `Idempotency-Key` row (`SessionDep` from `tumnis.core.idempotency` is that session; never commit yourself); a retry replays the stored response with `Idempotent-Replayed: true` for 24 hours. Versioned writes use `tumnis.core.versioning.update_versioned` (stale: `StaleVersion`, answered 409 `stale_version` with `current`). Raise `tumnis.core.errors.ProblemError(status, "<code>", detail)` for every other error: every answer is `application/problem+json` with a stable `code`. Before the handler, `TumnisRoute` refuses an API key or token on an `auth="session"` route (403 `session_required`), a key or token missing a policy scope (403 `insufficient_scope`; sessions hold every scope), and a project-limited key or task token aiming at another project (404 `not_found`, R-28) through `project_param` (`path:<name>`, `query:<name>`, `body:<name>`, or `lookup:<module>` with the module's `register_project_lookup`); it also refuses a NUL character (U+0000) in a path or query parameter or anywhere in a JSON body (422 `validation_error`; PostgreSQL text cannot store it). List routes filter to `principal.project_ids` in the module's `api.py`, and the authorization matrix (`backend/tests/meta/test_authz_matrix.py`) sweeps every route. The caller is `tumnis.core.principal.principal_of(request)`; rate limits (`RoutePolicy.rate_limit`, 429) and body limits (`max_body_bytes`, 413) come with the policy.

**Events.** A module that changes its rows emits in the same transaction: inside `tenant_session`, `await tumnis.core.outbox.emit(session, SomethingV1(...), occurred_at=now)` writes an `outbox` row and NOTIFYs the relay; a rollback drops both. Payload models subclass `tumnis.core.events.EventPayload`, declare `schema_version: Literal[1] = 1` and register with `@event_type("<name>", 1)`; `emit` refuses a payload that fails its model. Subscribers live in the module's `events.py`: `@subscribe("<event>", name="<module>.<handler>")` on `async def handler(envelope: EventEnvelope) -> None`. Each (event, subscriber) runs once as its own `deliver_event` DBOS workflow with ID `<event_id>:<subscriber>`, retried with full-jitter backoff (5 attempts by default), then dead-lettered (`/v1/dead-letters`: retry or discard) without holding up other subscribers. Every handler must be idempotent: a crash inside one re-runs it. Never rename a subscriber: its name is part of the workflow ID, so a renamed one receives old events again; add a new one and delete the old. Kill-and-resume tests put `tumnis.core.faults.killpoint("<step>")` where a crash must be survived; only the worker arms it (from `TUMNIS_KILLPOINT`, never in prod), and the `worker_killer` fixture drives it.

**Payload schemas.** A payload that crosses a process or agent boundary (events, packets, runner messages, results, digests) subclasses `tumnis.core.schemas.VersionedPayload`, declares `schema_version: Literal[<n>] = <n>` and registers with `@versioned("<family>", "<name>", <n>)` (events get it from `@event_type`). Add its example payload at `backend/tests/contract/fixtures/<family>/<name>/v<n>.json` and run `make gen`, which writes `schemas/<family>/v<n>/<name>.json`, `schemas/openapi.json`, the generated contract tests and the openapi-ts client. Readers call `parse_versioned(family, name, data)`: it accepts the latest version and the one before it (through the `@upgrader` from N-1, kept one release with its fixture) and raises `UnsupportedSchemaVersion` (422) otherwise. A request body names a row version as `tumnis.core.versioning.Version`. Operation IDs are `<tag>_<function>` (R-19), so renaming a route function renames its generated hook.

**Audit.** A SEC-3 action calls `tumnis.core.audit.record(session, action, ...)` in the action's own transaction (never from an event subscriber), and a new SEC-3 action adds a case to `backend/tests/audit_cases.py`.

**Caches and settings.** Every cache registers a `CacheSpec(name, scope, ttl_s, invalidated_by)` with `tumnis.core.cache.register_cache` (a spec without an invalidation rule is refused) and gets the shared write-visible test in `backend/tumnis/core/tests/unit/test_cache.py` for free. Keys are `CacheKey.for_workspace(workspace_id, "<name>", ...)`; `CacheKey.system` only for namespaces registered with `scope="system"`. Writers call `invalidate_on_commit(session, key)` inside their transaction, so every process drops the entry after the commit (LISTEN/NOTIFY) and a rollback drops nothing. Per-workspace settings and secrets go through `tumnis.core.settings_store.get_setting`/`put_setting` (sealed with the workspace data key, versioned writes); a module's routes and subscribers are switched off through `tumnis.core.modules` flags, never by hand.

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

**Backend fixtures** (in `backend/tests/fixtures/` and `backend/tests/_services.py`, loaded for every test folder by `backend/conftest.py`; never redefine them): `clock` (`FixedClock` at 2026-03-09T12:00Z, `advance()`, `set()`), `fakes` (`TUMNIS_ADAPTERS=fake`, `fakes["<adapter>"]`), `recordings(provider)` (`(raw, expected)` pairs from `tests/recordings/<provider>/*.json`); integration only: `pg_container`, `pg_base`, `db_template`, `db` (a fresh clone of the migrated template per test, `DbUrls` with `.owner`, `.app`, `.libpq(role)`), `owner_session`, `app_role_session`, `query_counter`, `dbos_sys_db`, `dbos` (launched on the test Postgres, system tables emptied per test), `dbos_client` (a `DBOSClient` on the same system database, as the api enqueues), `worker_killer(killpoint, *, events=5, event="test.ping")` (a subprocess worker killed at a kill point, then `restart_and_drain()`), `seed` and `load_fixture` (through each module's api, once the writers exist), `workspace` (with its owner user), `session_client` (signed in with password and TOTP; adds `X-CSRF-Token` and an `Idempotency-Key` to writes; P0-13), `key_client(scopes, projects=None)` (awaitable: an httpx client sending `Authorization: Bearer tmn_...` for a fresh key made through `create_key`; P0-14), `pepper_file`, `app_factory(**settings)`, and the session services `minio`, `sftp_server`, `clamd`. Unit tests cannot open network sockets; the `integration` marker opens them.

## Adapters and fakes

Every outside dependency sits behind an adapter with a fake beside it; `TUMNIS_ADAPTERS=fake` selects the fakes. A new adapter ships as these six pieces, in this order:

1. **Port**: a `Protocol` in `modules/<m>/adapters/port.py`. Callers depend on the port only.
2. **Real adapter**: `adapters/<provider>.py`, subclassing `tumnis.core.adapters.base.Adapter` with `name = "<m>.<provider>"` (its registry name). Every outside call goes through `await self.call(op, fn, idempotent=...)`, which applies the timeout, the circuit breaker and bounded jittered retries from its `CallPolicy(timeout_s, retry=RetryPolicy(...), breaker=BreakerConfig(...))`. Inside `fn`, translate the library's exceptions into the four adapter errors:

   | Error | When | Retried | Counts against the breaker |
   | --- | --- | --- | --- |
   | `AdapterTimeout` | raised for you when `timeout_s` runs out | yes | yes |
   | `AdapterUnavailable` | connection refused, 5xx, 429 (pass `retry_after_s` from `Retry-After`) | yes | yes |
   | `AdapterRejected` | 4xx, validation: the provider answered no | no | no |
   | `CircuitOpen` | raised for you while the breaker is open; nothing is sent | no | no |

   Only `idempotent=True` calls are retried. Adapters used inside DBOS workflow steps set `RetryPolicy(max_attempts=1)` and let the workflow retry; plain worker loops keep the default. Build one instance per (adapter, workspace connection) where credentials differ, so one workspace's broken token does not open the circuit for another. Take the time from the injected `Clock`; tests inject `sleep` and `rand`.
3. **Fake**: `adapters/fake.py`, implementing the port directly (it does not subclass `Adapter`), with scripting hooks (`fake.script(...)`) and a record of calls (`fake.calls`). A fake that defines `health_state()` shows up in `fakes.adapter_health()`.
4. **Registration**: `register_adapter("<m>.<provider>", port=..., real=..., fake=...)` in `adapters/__init__.py`; `tumnis.wiring` imports it.
5. **Contract suite**: `tests/contract/test_<provider>_contract.py`. One base class per port, without a `Test` prefix, subclassing `tumnis.core.adapters.contract.AdapterContract[Port]` and holding the cases; then one `Test*` class per implementation, marked `@pytest.mark.contract`, setting `impl` (`"fake"`, `"real"` or `"recorded"`) and overriding the `subject` fixture. Every registered adapter needs a `fake` class and a `real` or `recorded` class (a unit meta-test enforces it). `contract.py` imports pytest, so only tests may import it (import-linter `contract-base-test-only`).
6. **Recordings**: when the real side is recorded, scrubbed payloads go under `tests/recordings/<provider>/` and the `recordings("<provider>")` fixture loads them.

```python
class JevContract(AdapterContract[DecisionsPort]):
    port, adapter_name = DecisionsPort, "decisions.jev"

    async def test_choice_answer_has_probabilities(self, subject: DecisionsPort) -> None: ...


@pytest.mark.contract
class TestJevFake(JevContract):
    impl = "fake"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> DecisionsPort:
        return fakes["decisions.jev"]
```

An adapter whose breaker is not closed reports `health_state() == "degraded"`; `tumnis.wiring.register_adapter_health` registers each adapter as the non-critical readiness check `adapter:<name>`.

**Connectors** (integrations, calendar, knowledge, github, coolify) are adapters of one shape: the `tumnis.modules.integrations.api.Connector` protocol (`kind`, `provider`, `capabilities`, `async sync(cursor) -> SyncPage`, pure `map(raw) -> list[CanonicalRecord]`, `async health()`). Register with `register_connector("<provider>", "<kind>", real=..., fake=...)` in the module's `adapters/__init__.py` (adapter name `integrations.connector.<provider>`; no fake, no registration). `map` returns canonical records (`tumnis.core.canonical`, schema family `entities`) and calls `tumnis.core.sanitize.sanitize_html` for HTML; it never reads the clock or the network. Records are upserted on `(workspace_id, connection_id, external_id)` by the owning module: `integrations.api.ingest_page` for people, threads, messages, notes and artifacts (it raises `RecordTypeNotOwned` for anything else), `calendar.api.upsert_events`, `knowledge.api.upsert_synced_documents`; raw payloads go through `integrations.api.store_raw_payloads`. A new canonical table takes `canonical_columns()` from `tumnis.core.migration_helpers` (the meta-test `backend/tests/meta/test_canonical_keys.py` checks every table with `external_id`). The contract test is a folder of recordings plus a subclass of `tumnis.modules.integrations.connector_contract.ConnectorContract` (test-only; the one import across modules besides `api`) setting `provider` and `recordings_dir`: one `tests/recordings/<provider>/<case>.json` per case, `{"raw": <RawItem>, "expected": [<record JSON>], "notes": "Scrubbed: ..."}`. Scrub every recording before committing it: real names, addresses, tokens, ids and URLs are replaced with invented values on `example.com`/`example.org` (`scripts/scrub_recording.py` once it exists), and `notes` says what was replaced. Dropping in one more file adds one case.

## Time

- Rules take `now: datetime` (UTC, timezone-aware) and `tz: ZoneInfo` as arguments. Nothing in `rules.py` calls `datetime.now()`, `datetime.utcnow()`, `date.today()`, `time.time()`, `random`, `uuid.uuid4()`, `os`, `subprocess` or `socket`; a Ruff banned-API rule (TID251) enforces it.
- Code that does I/O gets the time from the `Clock` protocol in `tumnis/core/clock.py` (`SystemClock` in production, `FixedClock` in tests), never from the system directly.
- Store timestamps as `timestamptz` in UTC; show them in the workspace timezone. Durations are whole minutes (`estimate_minutes`), sizes are bytes.
- Local times go through `local_to_utc(day, local_time, tz)` in `tumnis/core/clock.py`: a time in a DST gap shifts forward by the gap, an ambiguous time takes the first occurrence.
- DBOS timeouts that tests must reach take a configurable duration, not a `FixedClock`.

## Third-party libraries and services

- Before writing or changing code that uses a third-party library, framework, SDK, service API or CLI, look up its current documentation in Context7; do not work from memory. If Context7 cannot be reached in your environment, go straight to the first-party sources below and say so in the PR body. Use the docs for the version pinned in `backend/uv.lock`, `daemon/uv.lock`, `profiles/uv.lock` or `frontend/package-lock.json`. The installed, pinned version is the truth: if it differs from what the docs describe, follow that version's docs.
- Confirm on the web when Context7 has no coverage, is ambiguous or contradicts itself, or when the behaviour is security-, auth-, protocol- or data-loss-sensitive. Use first-party authoritative sources only: the vendor's official docs and API reference, the project's own repository, its release notes and changelog, and standards bodies (IETF RFCs, W3C, WHATWG). Blogs, Q&A sites, forums and AI-generated summaries are never the authority.
- Never put secrets, credentials, tokens, personal data or proprietary code in a Context7 query or a web search.
- Cite the documentation you relied on for any non-obvious integration choice in the PR body. If current docs contradict the plan or an ADR, stop and ask Scott; do not silently diverge.

## Commands

| Command | Does |
| --- | --- |
| `make check` | Lint (Ruff check and format, mypy, import-linter, ESLint, `tsc`) and the unit tests. Run before every commit; must finish under 60 s |
| `make test-int` | Integration tests (Postgres, DBOS, containers) |
| `make e2e` | Playwright end-to-end suite against the seeded app with fakes |
| `make gen` | Regenerate OpenAPI, JSON Schemas and the TypeScript client; commit the result |

Some targets are filled in by later WPs; if a target is still empty, say so rather than working around it.

## Never

- Never hand-edit `frontend/src/api/`, `schemas/` or `backend/tests/contract/generated/`: `make gen` writes them, and the Contract job fails on any diff.
- Never call out to the network from the api process; it writes and enqueues, and the worker makes every outbound call. Exception: knowledge storage (P1-14) calls a location from the api process when a user saves or tests it, checks a project folder, or saves a note. These calls are short, user-initiated and bounded by the adapter's timeouts, and they go only through the storage port (`knowledge.api.open_backend`) (approved by Scott, 2026-09-29). The same terms cover reading a stored file to serve it (`GET /v1/files/{id}`, P1-16; approved by Scott, 2026-09-30).
- Never read or write another module's tables, or import anything but another module's `api`.
- Never use `datetime.now()` (or any clock, randomness or I/O) in `rules.py`.
- Never store secrets in environment variables; the only secret-related variables are file paths (`MASTER_KEY_FILE`, `API_KEY_PEPPER_FILE`, `METRICS_TOKEN_FILE`). Per-workspace secrets live encrypted in Postgres.
- Never edit an assertion in, or delete, an existing test; never add the `spec-change` label.
- Never build an `httpx` client outside `tumnis.core.net`: outbound calls use `guarded_client(settings.net_policy(), timeout=...)` (SSRF guard, pinned IP, redirects re-checked by `follow_redirects`), and non-HTTP clients connect to the address `resolve_and_check` returns. Never import `requests` or `urllib.request` in `tumnis/`. Exception: the Jev SDK (`typesafe-sdk`) builds its own client to its third-party API in `tumnis/modules/decisions/adapters/jev.py` (approved by Scott, 2026-09-29). Exception: `S3Storage` (`tumnis/modules/knowledge/adapters/s3.py`) talks to S3 through aioboto3's own HTTP stack. Its endpoint is checked against the same `NetPolicy` with `resolve_and_check` when a location is saved or tested (a blocked endpoint is refused with 422 `ssrf_blocked`, and nothing is sent), and every connection resolves through that check (approved by Scott, 2026-09-29). Exception: the ClamAV client (`tumnis/modules/knowledge/adapters/clamav.py`) connects to the operator-configured clamd address (`KNOWLEDGE__CLAMD_HOST` and `KNOWLEDGE__CLAMD_PORT`; loopback in tests) without `resolve_and_check`; nothing else may (approved by Scott, 2026-09-30).
- Never render HTML with `dangerouslySetInnerHTML` outside `frontend/src/components/common/SafeHtml.tsx`; outside HTML goes through `tumnis.core.sanitize.sanitize_html` on ingest and again on every read.
- Never rely on memory alone for a third-party API's current behaviour; check it in Context7 and first-party docs (see Third-party libraries and services).
- Never import `backend/` (the `tumnis` package) from the runner daemon in `daemon/`: it keeps its own copies of the protocol models, and its contract test checks them against `schemas/runner/v1/`.
- Never log request or message bodies, prompts or tokens (no `body=` or `prompt=` on a logger call); log structured fields that describe them. The `redact` processor is a backstop, not permission. The Security job's Semgrep rules (`.semgrep/tumnis.yml`) enforce these three.
