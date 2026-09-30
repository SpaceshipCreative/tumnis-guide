# HANDOFF: P2-02 (Task tokens and the packet builder), c1 -> c2

c1 stopped on the coordinator's "STOP NOW" (VM out of RAM). Branch `wp/P2-02`, pushed with
`/usr/bin/git push origin HEAD:wp/P2-02`. No PR yet, no CI run yet, no CodeRabbit review yet.

## Setup for c2

You are on a throwaway branch at main. `/usr/bin/git fetch origin`, `/usr/bin/git merge
origin/main`, `/usr/bin/git merge origin/wp/P2-02`, push with `/usr/bin/git push origin
HEAD:wp/P2-02`. Then `cd backend && uv sync --frozen --all-extras` (and `npm ci --prefix
frontend` before `make gen`). Scratch: `/tmp/claude-1002/P2-02-c2/`.

## Commits

- `f8525e3` test(agents): P2-02 spec tests (red). All 15 spec tests (T-P2-02-01..15) as
  strict xfail, plus: `backend/fixtures/hostile/snippets.yaml` (12 hostile cases with a
  `marker` each), `load_hostile_snippets()` and the `hostile_snippets` fixture in
  `backend/tests/fixtures/__init__.py`, strict mode in `backend/tests/fakes/fake_runner.py`
  (`fake_runner(..., strict=True)`, `strict_failures`, `callbacks`, `check_packet`),
  spec-guard now locks `**/tests/contract/golden/**` (`scripts/ci/_tests_extract.py`
  `GOLDEN_GLOBS`, test `test_golden_files_are_locked` in `backend/tests/ci/test_spec_guard.py`),
  `.importlinter` ignore `tumnis.modules.auth.tests.** -> tumnis.modules.agents.api`
  (module-graph-acyclic; the token tests live in auth as the plan says). Name stubs
  (NotImplementedError) in agents api keep mypy green.
- `139386e` feat(agents): escape untrusted text into nonce-tagged blocks (rules.py).
  T-04 (12 cases), T-05, T-06 unmarked and passing.
- `d9d5847` chore: P2-02 handoff (c0). Added `tasks/api.py` `context_item_ids`,
  `EstimateSample`, `estimate_history`; `integrations/api.py` `ContextItemText`,
  `owned_context_item_ids`, `context_item_texts`; `agents/packet_preamble.md`.
- `59f72a4` merge of origin/wp/P2-02 into current main (one import-line conflict in
  agents/rules.py, both sides kept: `Iterable, Mapping, Sequence, Set`).
- `4480f62` feat(agents): assemble task packets from untrusted blocks. The parked section is
  spliced into `agents/packet_builder.py` (PolicySection/Callback/constants sit above
  TaskPacket; TaskPacket gained `block_nonce`, `policy`, `callback`); ruff, mypy and
  lint-imports clean. `packet_for_caller` reads the project with `tasks.get_task(s, ...)`
  in the caller's transaction (not `tasks.project_of`, which opens its own). Comments have
  no `tainted` column, so `CommentInput.tainted` defaults False (trust comes from the
  author). T-07, T-08, T-09 unmarked; all 96 agents unit tests pass.
  `HANDOFF-packet_builder_section.py.txt` is deleted.
- This handoff commit. `HANDOFF-unmark.py.txt` is still at the repo root (copy it to your
  scratch folder as unmark.py; delete it before the PR). It cannot unmark a test whose
  decorators span lines (T-08's multi-line `@given`): remove those by hand.

## Scott decisions that land in this WP (binding, scott-decisions.md 30 and 31)

- **30. Workspace-scoped token for master runs.** Plan and notify runs on the master profile
  (no project) get a task token with no project: make `task_tokens.project_id` nullable
  (auth migration, expand phase; check auth's current head with `alembic heads`), null only
  for master-profile runs, never for project runs. Such a token covers only workspace-level
  tools (plan, notify: use RUN_TOKEN_SCOPES[PLAN]/[NOTIFY]), is a subset of the master
  key's scopes, and expires when the run ends. EVERY dispatch uses a token. T-15's plan case
  must go green (no xfail for it; remove its marker only after it passes). Check how
  auth's `authenticate_bearer` / `issue_task_token` and the RoutePolicy project check treat
  a null project (a project-less token must be refused on project-scoped routes/tools).
- **31. Erase at run end.** When a run ends or its token is revoked, overwrite the token in
  the stored `runner_messages.payload` with a redaction marker (e.g. `"[redacted]"`), so no
  backup or dump holds a live or recent token. Do it in `api.run_ended` (same transaction as
  `auth.revoke_task_tokens_for_run`). ADD A NEW TEST proving the stored message no longer
  holds the token after the run ends (a new test, e.g. in
  `agents/tests/integration/test_dispatch_tokens.py`; do not edit existing assertions).

## Remaining TDD steps (in order)

1. Goldens (T-01..03): add `backend/tests/contract/fixtures/packet/task_run_request/v1.json`
   (a valid TaskRunRequest body; model on `task_packet/v1.json` beside it), run `make gen`
   (regenerates schemas/packet/v1/task_run_request.json and task_packet.json, openapi,
   generated tests, schemas/mcp/v1/tools.json, TS client), then write the 8 goldens to
   `backend/tumnis/modules/agents/tests/contract/golden/<case>.json` from the test file's
   `_build(GOLDEN_CASES[case]())` output (`json.dumps(..., indent=2, ensure_ascii=True)` +
   newline), review them by eye, unmark T-01..03 (contract layer; runs in `make test`).
2. `agents/api.py`: replace stubs. `issue_run_token(ctx, *, run_id, kind, project_id,
   api_key_id, now)` = key scopes (add `auth.api.key_scopes(ctx, key_id)`, additive) ->
   `run_token_scopes` -> `auth.issue_task_token` (project_id None allowed for master runs,
   decision 30). `run_ended(ctx, run_id, *, now)` = `auth.revoke_task_tokens_for_run` +
   redact runner_messages payload (decision 31). `set_profile_key(ctx, profile_id,
   api_key_id, *, now)` stores `agent_profiles.api_key_id` after checking the key is live.
   Add optional `api_key_id` to ProfileIn/ProfilePatch (additive) and a caller-facts
   resolver (`agent_surface.register_caller_facts("agents.profile", ...)`: api_key
   principal -> profile_id, is_master = role == "master").
   Migration: revision `agents_0004`, `down_revision = "agents_0003"` (#78 merged
   agents_0003), `phase = "expand"`, adds nullable `agent_profiles.api_key_id` uuid (no
   cross-module FK). `alembic heads` must show one agents head. Update models.py docstring.
3. `auth/api.py authenticate_bearer`: a revoked `tmt` returns
   `AuthFailure("unauthenticated", "token_expired")` (T-12). T-P0-14-14 only checks 401.
4. `agents/mcp.py`: `get_task_packet` op (scope tasks:read, input `task_id` + optional
   `kind` in task/proposal/stuck, project via the task like tasks/mcp.py `_project_of_task`,
   handler `packet_for_caller(call.session, task_id, run_id=call.caller.run_id,
   profile_id=call.caller.profile_id)`, output TaskPacket). REST twin
   `GET /tasks/{task_id}/packet` on agents/router.py (`v1_router("agents")`, not prefixed)
   with `RoutePolicy(auth="session_or_key", scopes=frozenset({"tasks:read"}),
   project_param="lookup:tasks")` calling `surface.rest_twin`. Remove `get_task_packet`
   from `PENDING_TOOLS` in core/agent_surface.py; add `SAMPLES["get_task_packet"]` in
   backend/tests/_mcp.py (`{"task_id": str(world.parents[project])}`). The nonce is
   content-derived so both doors answer identically (T-P2-01-04 parity sweep).
5. Dispatch (T-15): in workflows.dispatch_step, before DaemonTransport.dispatch: read the
   profile's api_key_id and project (body["project"]["id"], else the profile's
   project_id; master profile -> project None, decision 30); `issue_run_token` and set
   `packet.callback` (create one with MCP_PATH/REST_BASE for enrich/plan packets). Keyless
   profile: P1-04's locked tests use keyless profiles, so check whether "every dispatch
   uses a token" can hold there (e.g. the profile has no key -> refuse? or mint?) and raise
   it if it conflicts with a locked test. In finish_step, call `api.run_ended`
   (idempotent). Then unmark T-10..T-15 (plan case included, decision 30). T-15's
   `_packet` helper calls `build_packet(...)` without `ctx=`: pass `ctx=workspace.ctx`
   (a call-signature fix in our own unmerged test, not an assertion).
6. Coverage: 100% line coverage on escaping and taint functions in agents/rules.py.
7. Docs: add P2-02 names to Part A of docs/IMPLEMENTATION-PLAN-DETAILED.md; fake_runner
   docstring mention of strict mode.
8. Finish: `make check` (semgrep env below), `make test`, `make test-int`, merge origin/main,
   delete HANDOFF-unmark.py.txt, open the PR per the prompt, `@coderabbitai review` once,
   run the review loop. Delete HANDOFF.md in a chore commit at the end.

## Decisions and deviations (for the PR body)

- P1-17 (build_packet, passages_for, /packet route), P1-08 and P1-11 (enrich/plan dispatch)
  are not on main. Seam: build_packet builds task/proposal/stuck only; enrich/plan packets
  keep their phase 1 shape and get the callback+token added in dispatch_step (run_skill is
  the one dispatch). Passages are [] until P1-17.
- escape_untrusted encodes CR as `&#xD;` instead of folding CRLF to LF (plan sketch), so
  T-05's round trip holds for every text. Also escapes zero-width chars and any char whose
  NFKD holds < or >.
- Policy action classes are the project policy's own vocabulary (projects.rules
  GATED_DEFAULT: proxmox_delete_guest etc.), not the plan outline's enum; `list[str]`.
- Callback URLs are relative (`/mcp`, `/v1`): the daemon resolves them against its server.
- TaskPacket's policy/callback are optional (no kind-conditional validator: the locked P2-07
  test T-P2-07-13 builds a task packet without one).
- Comments carry no taint column; a comment's block is untrusted when its author is not a
  person (`user:`), tainted only via P2-08 later.
- Profiles get a key only when linked (`set_profile_key`, ProfileIn/Patch api_key_id);
  nothing mints one yet. Scott item: who creates a profile's key (P1-06 provisioning?).
- Master runs: resolved by decision 30 (workspace-scoped token). Token at rest: resolved by
  decision 31 (redact at run end).
- P2-08 follow-up: TaintSource.kind has no value for P2-01's keyless_write (note only).
- Shared-file edits so far: `backend/.importlinter` (one ignore line + comment);
  spec-guard golden lock (`scripts/ci/_tests_extract.py`, `backend/tests/ci/test_spec_guard.py`;
  coordinator: fine, list it in the PR body).
- Docs relied on: Context7 for Hypothesis (`st.characters(codec=...)`, strategy `|`) and
  Pydantic 2.13 `model_validator(mode="after")`, `Field(default_factory=...)`.

## Verify commands

- Unit (no Docker): `cd backend && uv run pytest -q -p no:randomly tumnis/modules/agents/tests/unit`
- make check needs semgrep env (sandboxed): `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P2-02-c1/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/P2-02-c1/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/P2-02-c1/semgrep-version make check`
- Docker layers: bare `make test` and `make test-int` from the worktree root (mind VM RAM).
- `$TMPDIR` in a command and globs in plain commands are refused by the worktree guard; use
  literal paths (`/tmp/claude-1002/...`) and `find` instead of globs. `python3 - <<'EOF'`
  heredocs did work in c1 from the backend dir.
