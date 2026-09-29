# HANDOFF 2: P1-04 (AgentAdapter, runner protocol v1 and fake runner)

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p1-04`, branch `wp/P1-04`. It has NOT merged main since bcbae03; main has moved on (P0-23 follow-up, 881a28e). Before you start, read the prompt footer rules again (`scratchpad/prompt-footer.md`), AGENTS.md, and the WP section (`#### P1-04`, plan lines 8624 to 9082; the daemon parts are at 8912 to 8996).

## Done (commits on wp/P1-04)

| SHA | What |
| --- | --- |
| 57ffea9 | Backend spec tests red; interface stubs; `mcp==1.30.0` |
| 0aa33a8 | `protocol._parse` and `negotiate`, `rules.runner_status`: T-01, 02, 04 green |
| 6bbadca | `agents/migrations/0001_agents.py` (revision `agents_0001`, branch `agents`, depends on `auth_0001`); row_factory COLUMN_VALUES; isolation alias `profiles`→`agent_profiles`: T-21 green |
| 810c4a0 | FakeAgent (optional injected Clock): T-09 green |
| 2bd6bd2 | api (runners, profiles, health-check enqueue), `ws.py` (RunnerHub and socket), app.py wiring: T-06, 07, 08 green |
| 1a684ab | DaemonTransport (dispatch, LISTEN stream, health): T-10 and T-20 green |
| 3d38beb | workflows `run_skill`, `runner_sweep`, `check_profile_health`; worker queue `runs` (partition_concurrency 2) and the `runner-sweep` schedule; `wiring.load_workflows()`; `worker_killer` fixture migrates its DBOS system db: T-05, 12, 13 green |
| 93e092f | router (`/v1/runners`, `/v1/agents/profiles`), audit cases, `make gen`, live-map entries `runner` and `agent_profile`: T-18 green |
| 1c758ee, 11c3894 | Frontend `AgentsSection` (Settings > Agents), its queries, section, route and loader: T-19 green (Vitest, phone and laptop) |
| a1dfb39 | McpEndpointTransport: T-11 green |
| 707ea74, def2d59 | Daemon spec tests red (T-03, 14, 15, 16, 17), daemon package with protocol copies, config, and runner/main stubs |

`make check` was green at 11c3894 (855 backend unit, 51 Vitest). The P1-04 tests were green one by one. The FULL backend unit, contract and integration layers have NOT been run since the start.

## Spec test status

- Green: every backend test (T-01, 02, 04 to 13, 18, 20, 21) and frontend T-19. No `spec:P1-04` marker is left in `backend/` or `frontend/`.
- Red (strict xfail `spec:P1-04`, 14 cases): daemon `tests/contract/test_daemon_schemas.py` (T-03), `tests/unit/test_runner.py` (T-14, T-15 x10), `tests/unit/test_main.py` (T-17), `tests/integration/test_runner_with_stub_hermes.py` (T-16).

## Remaining steps

1. **Daemon runner** (`daemon/tumnis_daemon/runner.py`, now stubs), then remove the markers one by one:
   - `hermes_argv(cfg, msg, query_file)`: `fullmatch` NAME_RE on the profile and SKILL_RE on the skill, else `InvalidProfile`. Return `[hermes_bin, "-p", profile, "chat", "--query-file", str(q), "-s", skill, "--format", "stream-json", "--source", "tool"]`.
   - `write_query_file(run_dir, text)`: mkdir with mode 0700 (chmod it), write the file 0600, `text.encode()` byte for byte, and return the path.
   - `clean_env(cfg, environ=None)`: PATH, HOME, LANG and `HERMES_*` only.
   - `extract_json_object(text)`: strip; if the whole text is ONE fence (```` ```json\n…\n``` ```` or bare ```` ``` ````), take its inner text; `json.loads`; return a dict or None. The two_fences, two_objects, text_plus_object, array, empty and prose cases give None.
   - `read_recording(path)` gives `(events, final)`: parse the JSONL lines; `final` is the last record with `type == "result"`.
   - `build_result(msg, events, final, *, exit_code, timed_out, duration_ms)`:
     - timed_out gives `timed_out`.
     - No final record gives `failed` with `no_result_record`.
     - `is_error` or `subtype != "success"` gives `failed` with error = the final text.
     - No JSON gives `failed` with `no_json`.
     - Otherwise `succeeded`. `tokens` comes from `usage` (`input_tokens` and `output_tokens` become `input` and `output`) or `tokens`. `hermes_session_id` is `final.session_id`. `duration_ms` is `final.duration_ms` if it is an int, otherwise the measured value. Truncate `text` to 65,536 and `error` to 4,096.
   - `execute(msg, cfg, *, timeout_s=None)`: `create_subprocess_exec(*argv, cwd=run_dir, stdout=PIPE, stderr=PIPE, env=clean_env(cfg), start_new_session=True)` in `cfg.state_dir/"runs"/run_id`. Read stdout lines until `timeout_s or msg.timeout_s`. On timeout, `os.killpg(proc.pid, SIGKILL)` and wait. Then `build_result`.
   - Also `run_skill(ws, msg, state, cfg)` (execute, then `state.send_reliably`) and `check_health(ws, msg, cfg)`: run `hermes profile show <p>` (exit 0 means it exists), `hermes version`, `hermes -p <p> mcp list` (the first column gives the names) and `hermes -p <p> status` (its exit code gives `authenticated`). Parsing is best effort; a field that cannot be parsed is None.
2. **state.py**: `StateStore(state_dir)` persists unacked outbound frames under `state_dir/unacked/<message_id>.json` and has `send_reliably`, `ack`, `replay_unacked`, `running` and `protocol_version`.
3. **main.py**: `refuse_root()` exits with `SystemExit(EX_CONFIG)` when `os.geteuid() == 0`, BEFORE it reads the token. Implement `main(cfg)` as in plan lines 8915 to 8946, with `connect(f"{server_url}/ws/runner", additional_headers=..., ping_interval=20, max_size=8 MiB)`, `heartbeat_loop` and `receive_loop` (ack each non-ack message, dispatch Run and HealthCheck, and `state.ack` on Ack). Add `cli()` (`run --config PATH`) calling `load_config` and `asyncio.run(main(cfg))`. The test monkeypatches `daemon_main.os.geteuid` and `daemon_main.connect`, so keep `import os` and `from websockets.asyncio.client import connect` at module level.
4. Add `daemon/systemd/tumnis-daemon.service` with the unit text verbatim from plan lines 8974 to 8996. Update `daemon/README.md`. Add a line to AGENTS.md that the daemon package never imports `backend/` (done checklist). Then run `uv run --directory daemon pytest` and `uv run --directory daemon mypy`. Consider adding the daemon to `make check` (Makefile is a shared file: edit it minimally and report it).
5. **Refactor** (plan TDD step 10): one `ProtocolCodec` shared by `ws.py` and `tests/fakes/fake_runner.py`. This is optional; keep it small. Add the P1-04 names to Part A (A12) of the plan: RunnerHub, `/ws/runner`, RUNNER_CHANNEL `runner_mailbox`, RUN_EVENTS_CHANNEL `agents_run_events`, the queue `runs`, the schedule `runner-sweep`, `run_skill:<run id>`, `profile-health:<request id>`, the topics `run:<id>` and `health`, and the audit actions `runner.created` and `runner.token_rotated`.
6. Check whether P1-09 has landed on main. Its WorkerKiller adds `imports=`, `enqueue_until_killed`, `restart_until_done` and `close_client`. My additive edits to `backend/tests/fixtures/__init__.py`: `WorkerKiller.start` and `stop` (57ffea9), plus a `run_dbos_database_migrations` call in the `worker_killer` fixture right after CREATE DATABASE (3d38beb). Then run `git merge main`, keeping both sides. For generated client files take theirs, then run `make gen`. Watch `frontend/src/lib/live-map.ts`, `sections.ts` and `settings.$section.tsx` for conflicts.
7. **Finish green.** Move `frontend/dist` aside first; it is currently at `frontend/dist.aside`, so restore it at the end. Run `make check`, then from `backend/`: `uv run pytest -q -n auto -m "not integration and not contract"`, `-m contract`, and `-m integration -n auto`. Also run Vitest and the daemon suite. Known load flakes: `core/tests/integration/test_relay.py::test_notify_wakes_relay_before_poll` and `frontend/src/lib/optimistic.test.tsx`; rerun each alone. Run the kill test T-13 about 20 times in a row (done checklist). Do not push.

## Decisions and deviations (all, for the PR body)

Carried over from HANDOFF 1:

- `RunKind`, `RunStatus`, `NAME_RE`, `SKILL_RE`, `RunnerDTO` and `AgentProfileDTO` live in `rules.py`, and api re-exports them, because otherwise api and packet_builder would import each other. The port is in `adapters/port.py`.
- `SchemaRef` and `ProfileInfo` are strict parts without `schema_version`.
- The Hermes distribution version is stored as `profile_version`; the plan's `version` column clashes with the row's own `version`.
- The golden examples are P0-11's `backend/tests/contract/fixtures/runner/<type>/v1.json`, not `tests/contract/examples/runner/`.
- `run_skill` takes `(workspace_id, packet dict)`. `adapter_for` and `agent_for_project` are async and take the ctx.
- In fakes mode, `adapter_for` returns FakeAgent while the runner has never registered.
- The health check runs on the `runs` queue with partition `profile:<id>`. A9 names no queue for it.
- `auth.failed` for a device is audited only in self-hosted mode with exactly one workspace; otherwise it is only logged.
- There is no CI job for `daemon/` tests. **Scott.**

New in this session:

- `api_key_id` was dropped from the `AgentProfile` model; it is not in the plan.
- The migration adds these beyond the plan: `ck_runners_name`, `ck_runners_status`, `ck_agent_profiles_name`, `ck_run_events_kind` (which already allows log, tool_call and file for P2-07), `runner_messages.runner_id` FK, and a few indexes.
- `/ws/runner` and `agents.workflows` are imported by name (importlib) in `tumnis/app.py` and `tumnis/worker.py`. A static import broke the import-linter contract `modules-api-only` through the chain module tests → tumnis.app.
- The RunnerHub owns the api's DBOSClient (built lazily from `settings.dbos_system_url`, destroyed when the hub stops). `request_health_check` takes a `client=` keyword.
- `wiring` now imports every module's `workflows` at import time (`load_workflows`). The worker therefore registers every workflow before `DBOS.launch()`.
- DaemonTransport and McpEndpointTransport import the module's own `models`. HermesAgent does not subclass `core.adapters.base.Adapter`: there is no breaker or retry wrapper, because the daemon transport only writes rows and a retried MCP run could run twice. Flag this for review.
- The `worker_killer` fixture now creates DBOS's tables up front with `run_dbos_database_migrations`, so T-13 can enqueue before the first worker starts.
- I edited the T-11 contract fixture `endpoint` in `test_agent_adapter_contract.py`, not an assertion. The fake MCP server's session manager now runs in its own task, because pytest-asyncio sets a fixture up and tears it down in different tasks, and anyio refused the cancel scope.
- The frontend lists render only once loaded. The new `runner` and `agent_profile` live entities were added.
- The daemon's stream-json recordings are SYNTHETIC. **Scott:** capture real `enrich_ok`, `plan_ok` and `failed_turn` from the Hermes VM and scrub them.
- **Scott:** the real Hermes VM check (the daemon registers, a health check shows version and authenticated), and the Settings screen on the PR preview at 375 px.

## Shared-file edits so far

- `backend/pyproject.toml` and `uv.lock`: `mcp==1.30.0`.
- `backend/conftest.py`: the plugin `tests.fakes.fake_runner`.
- `backend/tests/fixtures/__init__.py`: `WorkerKiller.start` and `stop`, and the DBOS migration in `worker_killer`.
- `backend/tumnis/app.py`: the runner hub, the `/ws/runner` route and the lifespan task.
- `backend/tumnis/worker.py`: the `runs` queue, `register_runner_sweep()` and `_agents()`.
- `backend/tumnis/wiring.py`: `load_workflows`.
- `backend/tests/audit_cases.py`: two cases.
- `backend/tests/acceptance/_isolation.py`: the alias.
- `backend/tumnis/core/tests/integration/row_factory.py`: the COLUMN_VALUES entries.
- `frontend/src/lib/live-map.ts`.
- `daemon/pyproject.toml`: websockets 17.1 and pydantic 2.13.5; dev jsonschema 4.26.0, mypy 2.3.1, pytest 9.1.1, pytest-asyncio 1.4.0 and ruff 0.16.9.
- New `daemon/uv.lock`.

## Gotchas

- The PreToolUse "Fact-Forcing Gate" hook blocks Bash heredocs containing strings like `rm -rf`. Write such test data with the Write tool.
- `git stash` and `git reset --hard` are forbidden.
- rtk shortens tool output. Use `rg`, not `grep --include`.
- The ws tests need `filterwarnings("ignore:Using `httpx` with `starlette.testclient`")`.
- The daemon's ruff isort needs `known-first-party` (done).
- When the ws code updates an outbound row to `sent`, it does so only `WHERE status = 'queued'`, so an early ack is not overwritten.

## Verify

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy tumnis && uv run lint-imports
uv run pytest -q -p no:randomly tumnis/modules/agents -m "not integration"
uv run pytest -q tumnis/modules/agents -m integration
cd ../daemon && uv run pytest -q && uv run ruff check . && uv run mypy
cd .. && make check && make gen && git diff --exit-code schemas frontend/src/api
```
