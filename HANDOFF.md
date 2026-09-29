# HANDOFF: P1-04 (AgentAdapter, runner protocol v1 and fake runner)

Worktree `/Users/sjordan/Projects/Tumnis-Guide-wt/p1-04`, branch `wp/P1-04` (merged main at bcbae03, which includes P0-18 tasks). Read the prompt footer rules again (`scratchpad/prompt-footer.md`), AGENTS.md, and the WP section (plan lines about 8623-9082).

## Done

- `57ffea9 test(agents): P1-04 spec tests (red)`: every backend spec test is written and red (strict xfail `spec:P1-04`), with interface stubs so ruff, mypy and lint-imports pass. Unit: 853 passed, 2 xfailed. Contract (not integration): 72 passed, 34 xfailed. Integration was not run yet; those tests are red by construction (stubs raise NotImplementedError, and there is no migration yet).
- `mcp==1.30.0` is added to backend runtime deps (pyproject and uv.lock). mcp 2.x is not used because it runs on httpx2.
- `make gen` has run: `schemas/runner/v1/*.json` (11 files), `schemas/packet/v1/task_packet.json`, and generated contract tests `test_schema_runner.py` and `test_schema_packet.py`. The frontend client is unchanged because there are no routes yet. `frontend/node_modules` is installed (npm ci).

## Spec tests: all still RED

| ID | File | Status |
| --- | --- | --- |
| 01, 02 | `agents/tests/contract/test_runner_schemas.py` | red (`protocol._parse` and `negotiate` are stubs) |
| 04 | `agents/tests/unit/test_runner_status.py` | red (`rules.runner_status` stub) |
| 21 | `agents/tests/unit/test_run_vocabulary.py` | red (no migration; the test AST-parses `migrations/[0-9]*.py` for `CheckConstraint(..., name="ck_runs_kind"/"ck_runs_status")`, where the SQL must be ONE string constant) |
| 09, 10, 11 | `agents/tests/contract/test_agent_adapter_contract.py` (cases in `base.py`) | red |
| 06, 07, 08, 20 | `agents/tests/integration/test_ws_runner.py` | red |
| 05 | `agents/tests/integration/test_runner_sweep.py` | red |
| 12, 13 | `agents/tests/integration/test_run_skill.py` | red |
| 18 | `agents/tests/integration/test_profiles_api.py` | red |
| 19 | frontend `AgentsSection.test.tsx` | NOT WRITTEN yet |
| 03, 14 to 17 | daemon tests | NOT WRITTEN yet (daemon package is empty) |

## Remaining TDD steps (in order)

1. **Protocol (T-01, 02).** Implement `protocol._parse`: json.loads, then refuse `schema_version != 1` with `InvalidMessage("unsupported_schema_version")` before validating; any other ValidationError or bad JSON becomes `invalid_message`. Implement `negotiate(versions)` as `max(set(versions) & set(SERVER_PROTOCOL_VERSIONS))` or None. Remove the markers.
2. **Rules (T-04).** Restore `runner_status`: None gives never_seen; `now - last > timedelta(seconds=45)` gives offline; otherwise online. The stub body is the only change.
3. **Migration (T-21).** Add `agents/migrations/0001_agents.py`: revision `agents_0001`, `branch_labels=("agents",)`, `depends_on="auth_0001"`, `phase="expand"`. Build the tenant tables with `create_tenant_table`, following `models.py`:
   - `runners`: name with a `NAME_RE` check, unique `(workspace_id, name)`, inventory jsonb default `[]`, status default `never_seen`.
   - `agent_profiles`: FK runner_id to runners. Checks on role, transport and status (`registered|provisioning|ready|not_provisioned|paused`). Use `profile_version` (text); the plan's `version` column clashes with the base row `version`. Add partial unique indexes `ux_agent_profiles_one_master (workspace_id) WHERE role='master' AND deleted_at IS NULL`, `ux_agent_profiles_one_project_agent (workspace_id, project_id) WHERE role='project' AND deleted_at IS NULL` and a unique name.
   - `runs`: add `workflow_id`, `output`, `error` to the plan's columns. `ck_runs_kind` and `ck_runs_status` must be single string literals.
   - `run_events`: unique `(workspace_id, run_id, message_id)`.
   - `runner_messages`: direction `in|out`, status `queued|sent|acked`, unique `(workspace_id, message_id)`.
   - Add `row_factory.COLUMN_VALUES` entries (issue #31) for runs.kind/status, agent_profiles.role/transport/status, run_events.kind, runner_messages.direction/status/type, and runners.name if checked.
   - Add `TABLE_ALIASES["profiles"] = "agent_profiles"` in `backend/tests/acceptance/_isolation.py`, so the A0.3 sweep maps `/v1/agents/profiles/{id}`.
4. **FakeAgent (T-09).** Scripts are per skill; an unscripted skill answers `{}`. `offline(profile_id=None)` makes dispatch raise `AgentUnavailable`. `stream` yields `dispatched`, then one `result` whose payload is `{"status","output_json","error"}`; it replays for an already finished run. `cancel` is a no-op. `health()` gives `AgentHealth(status="ok", reachable=True)`. `capabilities()` gives transport "fake", skills {"enrich","plan"}, and False for stream and cancel. The contract asserts `handle.transport == capabilities().transport`.
5. **ws.py (T-06, 07, 08, 20).** In `create_app`, add the `/ws/runner` route on the app itself (like `/ws`): `app.add_api_websocket_route("/ws/runner", agents_ws.runner_socket)`. Add a `RunnerHub` to `app.state` (LISTEN `runner_mailbox`, payload `{"runner": id, "close": bool}`) and start it in the lifespan beside `live_hub`. These are minimal edits to `tumnis/app.py`; list them in the report.
   - Principal: `websocket.state.principal` with kind `device` (the auth bearer resolver already handles `tmd_`; `subject_id` = runner_id). On failure, close 1008 before accept.
   - Audit: record `auth.failed` with the actor `device:<runner id or nil uuid>` and details `{reason: missing_token|invalid_token}`. In self-hosted mode with exactly one workspace (from `audit.workspace_ids`, the SECURITY DEFINER `app.list_workspace_ids()`), write it into that workspace. Otherwise only log it, because there is no workspace to attribute it to. This is a deviation.
   - Register: the first frame must arrive within `getattr(app.state, "runner_register_timeout_s", 10.0)`. Anything else, such as a heartbeat, gets `error{not_registered}` and a close with 1008.
   - Negotiation: when `negotiate()` returns None, send `error{unsupported_protocol_version}` and close with 1008. If `runner_name` differs from the row, send `error{unknown_runner}` and close.
   - On register: update the runner (host, os, versions, protocol_version, inventory = profile list, status `online`, `last_heartbeat_at = app.state.clock.now()`). Send `registered`, then `ack{register}`. Forward queued rows and unacked `sent` rows, oldest first.
   - Reader: parse each frame with `parse_daemon`. An invalid frame gets `error` (with its code); three in a row close the socket. Insert inbound rows into `runner_messages` (direction `in`) with ON CONFLICT DO NOTHING to dedupe. Ack every daemon message. Heartbeat updates `last_heartbeat_at` and sets status online. An Ack marks the out row `acked` and sets `acked_at`.
   - Result: insert `run_events(kind=result, message_id=msg.message_id)` ON CONFLICT DO NOTHING and NOTIFY the run events channel. If `runs.workflow_id` is set, call `DBOSClient.send_async(workflow_id, result_dict, topic=f"run:{run_id}", idempotency_key=str(message_id))` every time, even for a duplicate, and ack only after that succeeds. Build the api's DBOSClient lazily from `app.state.settings.dbos_system_url`.
   - HealthReport: send to workflow `profile-health:{request_id}`, topic `health`.
   - Forwarder: wait on a per-socket asyncio.Event set by the hub, falling back to a 1 s poll. Send queued rows, then mark them `sent`. Guard socket sends with an asyncio.Lock. Do NOT mark the runner offline on disconnect; only the sweep does that. The fixed clock makes the heartbeat test deterministic.
6. **DaemonTransport, mailbox, run_skill, fake_runner (T-10, T-12).**
   - `dispatch`: in `tenant_session(ctx)`, check `runners.status == 'online'` and the profile name in the runner inventory; otherwise raise `AgentUnavailable`. Do not compare clocks, because the worker runs on the system clock. Then insert the `runs` row (id = packet.run_id, status running, `workflow_id = DBOS.workflow_id` when inside a workflow), all with ON CONFLICT DO NOTHING. Insert the mailbox row: message_id `uuid5(run_id, "run")`, type run, payload = `protocol.Run(...)` JSON with `packet.model_dump(mode="json")`. Insert the `run_events` dispatched row with message_id `uuid5(run_id, "dispatched")`, and `pg_notify('runner_mailbox', ...)` in the same transaction.
   - `stream`: LISTEN the run events channel and read rows until a terminal event (`result` or `failed`).
   - `capabilities`: transport "daemon", skills {"enrich","plan"}.
   - `health`: runner online and profile listed.
   - `workflows.run_skill(workspace_id: str, packet: dict) -> dict`, with `dispatch_step` → `DBOS.recv_async(topic=f"run:{run_id}", timeout_seconds=timeout_s + RECV_GRACE_S)` in the workflow body → `finish_step`. Put `faults.killpoint("agents.dispatch_step")` after the commit inside `dispatch_step`. `finish_step` does the following: None means timed_out; `{"status": "runner_lost"}` passes through. It validates `output_json` against `output_schema` only if registered in `tumnis.core.schemas.registry()`. It updates `runs` (status, finished_at, output, error) and writes a `failed` run event for non-result endings. It returns `RunOutcome(...).model_dump(mode="json")`.
   - `start_run_skill` uses `SetWorkflowID(f"run_skill:{run_id}")` + `DBOS.start_workflow_async`. `enqueue_run_skill(client, ws, packet)` enqueues on queue `runs` (register in `worker.register_queues` with `partition_concurrency=2`; enqueue with `queue_partition_key=str(profile_id)`), `workflow_name="run_skill"`, same workflow id.
7. **Kill test and sweep (T-13, T-05).**
   - Add `wiring.load_workflows()` (import every module's workflows), called in `worker.main` before `DBOS.launch()`.
   - Register schedule `runner-sweep` (`* * * * *`) in `worker.py`.
   - `runner_sweep(scheduled_at, context) -> list[str]`: a step lists workspaces (`app.list_workspace_ids()`) and, per workspace, marks runners offline via `rules.runner_status(last_heartbeat_at, scheduled_at) == "offline"`. It collects `runs` with status running whose profile's runner went offline and sets them `runner_lost`. The workflow then calls `DBOS.send_async(workflow_id, {"status":"runner_lost"}, topic=f"run:{run_id}")` and returns the lost run ids.
   - The kill test uses the new `WorkerKiller.start/stop`, and `app.state.clock = SystemClock()`.
8. **REST and Settings (T-18, T-19).**
   - Put one router in `router.py`: `v1_router("agents", tags=["agents"])` (not prefixed), paths `/runners`, `/runners/{id}/rotate-token`, `/agents/profiles`, `/agents/profiles/{id}` (PATCH), `/agents/profiles/{id}/health-check` (202). All are `auth="session"`, and writes are `idempotent=True`. Runner create and rotate use `redact_on_replay=("token",)`, and lists return `Page[...]` with `paginated=True`.
   - Look up the row (404) before body rules (issue #28). Refuse a second master with 409 `master_exists`, a duplicate name with 409 `profile_exists`, and a bad name with 422 `invalid_profile_name`. Validate that `runner_id` and `project_id` exist (projects.api) or answer 404.
   - The device token comes from `auth.api.issue_device_token(ctx, runner_id=, now=)`, which opens its own transaction.
   - Audit `runner.created` and `runner.token_rotated`, and add `AuditCase`s to `backend/tests/audit_cases.py`. On rotate, NOTIFY `runner_mailbox` with close:true, so the live socket closes.
   - Health check: enqueue `check_profile_health(workspace_id, profile_id, request_id)` with workflow id `profile-health:{request_id}` on the `runs` queue. That workflow: a step writes the HealthCheck mailbox row and NOTIFYs; the workflow body calls `recv(topic "health")`; a step writes `agent_profiles.health` (`ProfileHealth` JSON with version = hermes_version) and `health_checked_at`. An MCP profile calls `McpEndpointTransport.health()` instead.
   - Run `make gen`. Add `agentsListRunners` and `agentsListProfiles` to LIVE_MAP (new entities `runner` and `agent_profile`) or to NOT_LIVE in `frontend/src/lib/live-map.ts`.
   - Frontend: `components/settings/AgentsSection.tsx` plus its test (MSW; online and offline runner chips, profile health chips, token shown once; `viewport` phone and laptop). Add an `agents` section to `sections.ts`, the `settings.$section.tsx` SCREENS and the loader, and `queries.ts`.
9. **McpEndpointTransport (T-11).** Use `mcp.client.streamable_http.streamable_http_client(url, http_client=factory())` with `ClientSession.call_tool("run_skill", {profile, skill, packet_json})`. The tool returns the result JSON text `{status, output_json, text, error, duration_ms}`; keep events in memory per transport. On connect errors (httpx.ConnectError, or an ExceptionGroup containing one), raise `AgentUnavailable`. The default factory is `guarded_client(net_policy, timeout=...)`. `health()` lists tools: `ok` if `run_skill` is present, otherwise `unsupported`. The prototype that proved this works in-process with FastMCP(host="fake-hermes.test", stateless_http=True, json_response=True) is `scratchpad/mcp_proto.py`. The host must not be localhost, or DNS-rebinding protection returns 421.
10. **Daemon (second impl PR in the plan; T-03, 14 to 17).** Build `daemon/tumnis_daemon/{main,protocol,runner,config,state}.py`, `daemon/systemd/tumnis-daemon.service` (unit text in the plan), `daemon/tests/{unit,contract,integration}`, the stub `daemon/tests/stubs/hermes` and recordings `daemon/tests/recordings/stream_json/*.jsonl`. Deps (pin exactly): websockets 17.1, pydantic 2.13.5; dev: pytest, pytest-asyncio, jsonschema. The daemon never imports `backend/`. Add that line to AGENTS.md (done checklist). T-03 validates the daemon's messages against `../schemas/runner/v1/*.json`.
11. **Refactor.** Build one `ProtocolCodec` shared by ws.py and fake_runner. Add the P1-04 names to Part A (A12) of the plan.

## Decisions and deviations (so far)

- `RunKind` and `RunStatus`, plus `NAME_RE`, `SKILL_RE`, `RunnerDTO` and `AgentProfileDTO`, live in `rules.py` (pure), and `api.py` re-exports them. This avoids an import cycle between api and packet_builder. The AgentAdapter port lives in `adapters/port.py` (AGENTS.md) and is re-exported by api.
- Nested protocol parts (`SchemaRef`, `ProfileInfo`) are strict BaseModels without `schema_version`. Only messages are VersionedPayloads.
- The profile's Hermes distribution version is stored as `profile_version`, because the base `version` is the row version.
- The golden examples are P0-11's fixture files `backend/tests/contract/fixtures/runner/<type>/v1.json`, not `tests/contract/examples/runner/`, so there is one copy.
- `run_skill` takes `(workspace_id, packet dict)`, since a workflow needs the workspace for RLS. `adapter_for` and `agent_for_project` are async and take the ctx.
- `adapter_for` in fakes mode returns FakeAgent while the profile's runner has never registered (compose.test e2e); otherwise it returns HermesAgent over the profile's transport. `run_skill` dispatches through the daemon transport.
- The health check is enqueued on the `runs` queue, with partition key `profile:<id>`. A9 names no queue for it.
- `auth.failed` for a device is audited only when the workspace is known (see step 5).
- There is no CI job for `daemon/` tests. Flag this for Scott.

## Gotchas

- The `starlette.testclient` import warns (httpx); filterwarnings is error. Test modules carry `filterwarnings("ignore:Using `httpx` with `starlette.testclient`")`, and `tests/fakes/fake_runner.make_test_client` suppresses it on import.
- The `dbos` fixture must be requested before the workflows run. The api's DBOSClient uses `settings.dbos_system_url`, which is `dbos_sys_db` in the `app` fixture.
- mypy: import jsonschema in tumnis tests with `# type: ignore[import-untyped]`.
- The unit meta-test T-P0-09-13 imports every contract suite and needs fake plus real contract classes for `agents.hermes` (present).
- Move `frontend/dist` aside before running backend contract and integration layers.
- Do not run `git stash` or `git reset --hard`.

## Verify

```bash
cd backend
uv run ruff check . && uv run ruff format --check . && uv run mypy tumnis && uv run lint-imports
uv run pytest -q -n auto -m "not integration and not contract"
uv run pytest -q -m contract
uv run pytest -q -m integration -n auto tumnis/modules/agents
cd .. && make check && make gen && git diff --exit-code schemas
uv run --directory daemon pytest
```
