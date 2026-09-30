# HANDOFF: P1-06 Project provisioning

Branch `wp/P1-06` (pushed with `/usr/bin/git push origin HEAD:wp/P1-06`). No PR yet, so
there are no review threads and no CI runs. Handoff requested by the context watcher.

## Commits (on top of main 27bef71)

| SHA | What |
| --- | --- |
| 1dd1f18 | `test(agents): P1-06 spec tests (red)`: T-P1-06-01..10, all strict xfail / `test.fails`; fake runner gets `script_provision` + `provisions()` and answers `provision` |
| 9c3eea5 | `feat(agents): P1-06 profile_name_for and provision_outcome`: T-P1-06-08 green, marker removed; extra unit test `test_provision_outcome` |
| (next) | `feat(projects): P1-06 AgentProfileChoice on project.created`: WIP, see below |
| (next) | `chore: P1-06 handoff` (this file) |

## Spec test state

- GREEN, marker off: T-P1-06-08 (`agents/tests/unit/test_profile_name.py`).
- RED (strict xfail): T-P1-06-01..07 (`agents/tests/integration/test_provision.py`),
  T-P1-06-09 (`daemon/tests/integration/test_provision_stub.py`), T-P1-06-10
  (`frontend/src/components/project/CreateProjectDialog.test.tsx`, `test.fails`).
- Red was confirmed locally for 08, 09 and 10. 01..07 were not run (Docker layer). They fail
  on missing names. Their imports of not-yet-existing names carry
  `# type: ignore[attr-defined]` so mypy passes. Remove each ignore when its name exists,
  or mypy flags it as unused.

## WIP in the last feature commit (not finished)

`backend/tumnis/modules/projects/events.py` gains `AgentProfileChoice` (mode `create`|`link`,
`name` matching `PROFILE_NAME_PATTERN`, validator) and `ProjectCreatedV1.profile`, which
defaults to create. **`make gen` has NOT been run.** Run it, then commit
`schemas/events/v1/project.created.json`, `schemas/openapi.json`, the generated contract
tests and `frontend/src/api`. Optionally add `"profile": {"mode": "create"}` to
`backend/tests/contract/fixtures/events/project.created/v1.json`.

## Remaining steps (design decided; follow it)

1. **projects** (`api.py`, `router.py`): add `ProjectCreateIn(ProjectCreate)` with
   `profile: AgentProfileChoice | None = None`. Re-export `AgentProfileChoice`. Keep
   `ProjectCreate`/`ProjectOut` unchanged, or reads would grow a field. `create_project`
   takes `ProjectCreate`, reads `profile` when it gets a `ProjectCreateIn`, excludes
   `profile` from the insert values and puts it on `ProjectCreatedV1`. The route body
   becomes `api.ProjectCreateIn`. Add `project_names(s, ids) -> dict[UUID, str]` for the
   registry.
2. **settings**: add `AgentsSettings(provision_timeout_s: int = 300)` as
   `Settings.agents` (env `AGENTS__PROVISION_TIMEOUT_S`). `worker.main` calls
   `agents.api.configure_provisioning(timeout_s=...)` (import by name, as
   `configure_generation` does).
3. **migration** `agents_0002` (down_revision `agents_0001`, `phase = "expand"`): add
   `agent_profiles.provision_attempts int NOT NULL DEFAULT 0` and
   `provision_mode text NOT NULL DEFAULT 'create'` (CHECK in create|link). Mirror them in
   `models.py`. Report the revision id.
4. **agents/api.py**:
   - `TEMPLATE_NAME = "project-template"` and `TEMPLATE_VERSION = "1.0.0"`, plus a unit test
     that it equals `profiles/project-template/VERSION`.
   - Re-export `ProjectAgentEntry` (from `skill_io`) and `PlanningRequest` (already there).
     `async def master_registry() -> list[ProjectAgentEntry]` covers live project profiles,
     runner name and project name, sorted by project name, in `tenancy.current()`.
   - `ProvisioningFailedPayload(profile, mode, error_code, error: str|None, attempt: int)`.
     Register it with `tasks.register_review_kind(ReviewKindSpec(kind="provisioning_failed",
     owner_module="agents", payload_schema=..., actions=("accept","reject","snooze"),
     impact_scope="project"))`. agents -> tasks makes no import cycle.
   - `configure_provisioning(timeout_s)` and `provision_timeout_s()` (module variable, 300).
   - `provision_workflow_id(project_id, attempt)`: `provision:{pid}` for attempt 0, else
     `provision:{pid}:{n}`. `provision_topic(project_id)` = `provision:{pid}`.
     `provision_request_id(workflow_id)` = uuid5(fixed namespace, workflow_id).
     The mailbox message id is uuid5(request_id, "provision").
   - A seam for starting the workflow: `register_provision_starter(fn)`, which
     `workflows.py` calls at import. The api cannot import workflows.
   - `retry_provision(project_id, *, ctx=None)`, in a tenant session with the row locked:
     `not_provisioned` bumps `provision_attempts`, sets `provisioning` and enqueues attempt
     n. `provisioning` re-enqueues its current attempt (idempotent). `ready` and others do
     nothing. No row enqueues attempt 0. Enqueue after commit.
   - **Enqueue from a subscriber**: DBOS forbids start/enqueue inside a step (docs; in 3.1
     `create_start_workflow_child` asserts not-in-step), and handlers run inside the
     `run_handler` step. So enqueue in a fresh contextvars context:
     `asyncio.get_running_loop().create_task(coro, context=contextvars.Context())`, under
     `SetWorkflowID(wf_id)` + `SetEnqueueOptions(deduplication_id=dedupe)` +
     `suppress(DBOSQueueDeduplicatedError)`, on `RUNS_QUEUE` with
     `queue_partition_key=f"provision:{pid}"`. That mirrors `core/events._enqueue`.
     The first run uses dedupe `project.created:{event_id}:agents` (plan). A retry uses its
     workflow id.
5. **agents/events.py**: `PROVISION_SUBSCRIBER = "agents.provision_project"`,
   `@subscribe("project.created", name=PROVISION_SUBSCRIBER)`. It reads the payload dict
   (`profile` missing means create) and starts attempt 0. Add
   `@subscribe("human.decided", name="agents.retry_provisioning")`: when
   `item_kind == "provisioning_failed"` and `decision == "accept"`, call
   `retry_provision(target_id)`.
6. **agents/workflows.py**: `@DBOS.workflow(name="provision_profile")
   provision_profile(workspace_id, project_id, mode, link_name) -> str`. It takes
   workspace_id as its first argument, a deviation from the plan's signature, because
   steps need the tenant.
   - `choose_name_step`: reuse the project's live profile row if there is one. Otherwise
     compute the name: create uses `profile_name_for(project name, taken)`, where taken is
     every agent_profiles name, deleted ones included, plus every runner's inventory name.
     Link uses `link_name`, unless that is reserved, the master's or taken, which yields
     `invalid_name` with a generated name and mode create. Then insert the row with status
     `provisioning` and the mode.
   - `pick_runner_step`: the runner of the master profile, else runners ordered online
     first, then by created_at. None means `no_runner`, finished at once without waiting.
   - `send_provision_step`: set the row's `runner_id` and insert the `Provision` mailbox row
     (correlation_id = workflow id, message id deterministic,
     `on_conflict_do_nothing`). NOTIFY the runner channel. Then
     `faults.killpoint("agents.send_provision_step")` after commit.
   - `reply = await DBOS.recv_async(topic=provision_topic(pid),
     timeout_seconds=provision_timeout_s())` in the body.
   - `finish_provision_step`: `provision_outcome(ProvisionResult or None, mode=...)`. On
     ready: status ready, `profile_version` = distribution_version, and the name appended
     to `runners.inventory` if missing (else `agent_for_project`/dispatch say offline);
     `mark_changed` profile and runner. Otherwise: `not_provisioned` plus
     `tasks.add_review_item("provisioning_failed", target=TargetRef(type="project",
     id=pid), project_id=pid, payload=..., dedupe_key=f"provisioning_failed:{pid}",
     session=s)`, with error_code = reply.error_code, `timeout`, `no_runner` or
     `invalid_name`.
   - Plan refactor step 9 (`send_and_wait` shared helper) is optional.
7. **agents/ws.py** (keep it additive: P2-07 edits this file in parallel): in `_handle`,
   handle `ProvisionResult`. Mark the outbound row (message id
   uuid5(request_id,"provision"), this runner) acked, returning its payload. If there is
   no row, drop it and ack. The workflow id is `payload["correlation_id"]`. Then
   `hub.client().send_async(wf_id, message.model_dump(mode="json"),
   provision_topic(pid), str(message.message_id))`, where pid comes from the workflow id.
   Return False on a send error so the daemon resends.
8. **daemon**: `tumnis_daemon/provision.py` with `handle_provision(msg, cfg) ->
   ProvisionResult` and `provision(msg, state, cfg)` (via `state.send_reliably`). It
   checks the version against `cfg.bundled_template_version` (reads
   `template_dir/VERSION`) before running anything. `hermes profile show <n>` gives
   existence. Link answers linked or not_found. An existing create answers exists.
   Otherwise it runs `hermes profile install <template_dir> --name <n> --yes`: rc 0 means
   created, else failed with `hermes_error`. After created, it copies
   `cfg.profile_env_file` (default `/etc/tumnis/profile.env`) to
   `cfg.hermes_profiles_dir/<n>/.env` with mode 0600, if the file exists. The stub needs
   `HERMES_STUB_LOG` (append tab-joined argv) and `HERMES_STUB_PROFILES_DIR` (install
   makes the dir; show checks it; fall back to the acme-site rule when unset, so P1-04
   tests hold). Config gains `template_dir`
   (default `/opt/tumnis-daemon/share/profiles/project-template`), `hermes_profiles_dir`
   (default `~/.hermes/profiles`) and `profile_env_file`. `main.py` dispatches `Provision`
   to it (replacing `_no_provision`) and advertises `"provision"`. Remember provisioned
   names in `state_dir/profiles.json` and add them to register's profiles. Use `--yes`
   rather than the plan's `-y`: the Hermes v2026.6.5 docs list `[--yes]`.
9. **frontend**: move `NewProjectForm` from `ProjectList.tsx` into
   `components/project/CreateProjectDialog.tsx`. Add a fieldset "Agent" with radios
   "Create a new agent profile" (default) and "Link an existing profile", and a required
   "Profile name" input (pattern) shown only for link. Send `profile` ONLY for link: the
   locked ProjectList test expects the body `{name, client, goal}`. `ProjectPage` queries
   `profilesQuery()` (settings/queries.ts), finds the profile for the project and passes
   `agent` to `ProjectHeader`. With no profile it keeps "No agent yet" (a locked test).
   Otherwise it shows "Agent: setting up" (registered/provisioning), "Agent: ready" or
   "Agent: not set up". The live `agent_profile` already invalidates `agentsListProfiles`.
   Add a default `/v1/agents/profiles` (empty) to `ProjectFake.handlers`.
10. Remove markers one at a time as each passes (approved), then `make check`, `make test`,
    `make test-int` (bare), vitest `--maxWorkers=4`, then open the PR (see the prompt,
    ~/tumnis-coordinator/prompts/P1-06.md, steps 4-5) and run the review loop.

## Decisions and deviations

- The workflow signature adds `workspace_id` (steps need the tenant).
- The frontend file `CreateProjectDialog.tsx` does not exist on main. The form lives in
  `ProjectList.tsx`; extract it.
- `daemon/packaging/` does not exist. Put a note in `daemon/README.md` that the template
  must sit at `/opt/tumnis-daemon/share/profiles/project-template` (a Scott item for the
  VM install).
- T-P1-06-06's "Jev labels still arrive" cannot be asserted: labels on `task.created` are
  P1-07, not merged. The test asserts that the project is unchanged and that a task
  create works.
- T-P1-06-05's "next planning request": no builder exists yet (P1-11). The test validates
  a `PlanningRequest` built from `master_registry()`.
- `RESERVED_PROFILE_NAMES` is unchanged (P1-04's set). `tumnis-master` is avoided through
  `MASTER_PROFILE_NAME` in `profile_name_for`, because reserving it would break master
  registration.
- In fake-adapter stacks with no runner, provisioning fails at once with `no_runner` and
  a review item per seeded project. That is expected. No e2e test counts review items.

## Scott items

- Done check "creating a project creates its Hermes profile on the VM" and
  `hermes profile list` shows it: manual on the Hermes VM (never reached from here).
- Install the template on the VM at `/opt/tumnis-daemon/share/profiles/project-template`,
  and `/etc/tumnis/profile.env` (mode 0600, owned by tumnis-agent).
- Confirm that the pinned Hermes accepts `profile install <dir> --name <n> --yes` without
  prompting.

## Context7 docs checked

- DBOS (`/dbos-inc/dbos-docs`, pinned dbos 3.1.0): `SetEnqueueOptions(deduplication_id)`
  raises `DBOSQueueDeduplicatedError` while one is queued or running. `SetWorkflowID`
  reuse returns the existing workflow. Python steps cannot start or enqueue workflows
  (step tutorial). This matches the installed source (`_context.create_start_workflow_child`).
- Hermes (`/nousresearch/hermes-agent/v2026.6.5`): `hermes profile install <source>
  [--name] [--alias] [--force] [--yes]` from a local dir. Profile homes are
  `~/.hermes/profiles/<name>/` with a per-profile `.env`. `hermes profile show <name>`.
  Distributions carry SOUL/config/mcp/skills/cron, never .env or memories
  (profile-distributions.md).

## Verify commands

- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/agents`
- `cd daemon && uv run pytest -q tests`
- `cd frontend && npx vitest run --maxWorkers=4 src/components/project`
- From the worktree root, bare: `make check` (set `SEMGREP_SETTINGS_FILE`,
  `SEMGREP_LOG_FILE` and `SEMGREP_VERSION_CACHE_PATH` under /tmp/claude-1002/), then
  `make test` and `make test-int`.
- Under the VM load, vitest in `make check` times out on rail tests. Run it with
  `--maxWorkers=4`: all 52 files pass.
