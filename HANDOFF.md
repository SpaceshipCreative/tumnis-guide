# HANDOFF: P2-02 (Task tokens and the packet builder), c0 -> c1

Branch `wp/P2-02` (pushed with `/usr/bin/git push origin HEAD:wp/P2-02`). No PR yet, no CI
run yet, no CodeRabbit review yet. The local branch is also named wp/P2-02 (the rename
succeeded; .git/config could not record upstream, which is fine).

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
  (NotImplementedError) in agents rules/packet_builder/api keep mypy green.
- `139386e` feat(agents): escape untrusted text into nonce-tagged blocks. Real
  `escape_untrusted`, `escape_attr`, `unescape_untrusted`, `render_block`, `packet_tainted`,
  `truncate_utf8`, `render_task_prompt`, `RUN_TOKEN_SCOPES`, `run_token_scopes`, `Block` in
  `agents/rules.py`. Markers removed: T-04 (12 cases), T-05, T-06; all pass.
- `chore: P2-02 handoff` (this commit): WIP, not yet exercised by tests:
  - `tasks/api.py`: `context_item_ids(s, task_id)`, `EstimateSample`, `estimate_history`.
  - `integrations/api.py`: `ContextItemText`, `owned_context_item_ids`, `context_item_texts`.
  - `agents/packet_preamble.md` (the trusted preamble).
  - `HANDOFF-packet_builder_section.py.txt`: the full packet builder section, written but
    NOT yet spliced or run. Splice it into `backend/tumnis/modules/agents/packet_builder.py`
    in place of the stub block that starts at `# --- The task packet builder (P2-02); spec
    stubs` (to end of file), then fix imports (see below). Delete the .txt files before the PR.
  - `HANDOFF-unmark.py.txt`: helper that removes a test's spec marker:
    `python3 unmark.py <file> <test name>...`.

## Remaining TDD steps (in order)

1. Splice the packet builder section. Imports it needs in packet_builder.py: `functools`,
   `hashlib`, `secrets`, `Path`, `uuid5`, `Annotated`, `Final`, `cast`, `get_args`,
   `StringConstraints`, `select`, `AsyncSession`, `NotFound` (tumnis.core.versioning),
   `tenancy` and `session_for`/`WorkspaceContext` (tumnis.core.tenancy), rules names
   (`Block`, `BlockSource`, `NONCE_RE`, `PACKET_MAX_BYTES`, `CONTEXT_ITEM_MAX_BYTES`,
   `packet_tainted`, `render_block`, `render_task_prompt`, `truncate_utf8`),
   `tasks`, `integrations`, `knowledge` apis, `AgentProfile` table as `_profiles`.
   Watch for import cycles (agents.api imports packet_builder; knowledge imports tasks and
   projects, none import agents). Add TaskPacket fields `block_nonce: str | None = None`
   (pattern NONCE_RE), `policy: PolicySection | None = None`, `callback: Callback | None =
   None` (tainted already added). Do NOT add a validator requiring policy for kind task:
   the locked P2-07 test T-P2-07-13 builds a task packet without one.
   The section's `_LT_ESCAPED` is built as "\\" + "u003c" on purpose: the Write tool turns
   backslash-u escapes into literal characters (it did to rules.py; fixed with an
   ascii-escape pass). Check any file you write for literal non-ASCII (`grep -nP "[^\x00-\x7F]"`).
2. Unmark T-07 (test_untrusted_block.py), T-08/T-09 (test_packet_taint.py). Then add the
   `task_run_request` fixture `backend/tests/contract/fixtures/packet/task_run_request/v1.json`
   (a golden body), run `make gen` (schemas/packet/v1/task_run_request.json, task_packet.json,
   openapi, generated tests, schemas/mcp/v1/tools.json, TS client; needs
   `npm ci --prefix frontend`), write the 8 goldens to
   `backend/tumnis/modules/agents/tests/contract/golden/<case>.json` from `_build(...)` output
   (review them by eye), unmark T-01..03.
3. `agents/api.py`: replace stubs. `issue_run_token(ctx, *, run_id, kind, project_id,
   api_key_id, now)` = key scopes (add `auth.api.key_scopes(ctx, key_id)`, additive) ->
   `run_token_scopes` -> `auth.issue_task_token`. `run_ended(ctx, run_id, *, now)` =
   `auth.revoke_task_tokens_for_run`. `set_profile_key(ctx, profile_id, api_key_id, *, now)`
   stores `agent_profiles.api_key_id` after checking the key is live. Also add optional
   `api_key_id` to ProfileIn/ProfilePatch (additive) and a caller-facts resolver
   (`agent_surface.register_caller_facts("agents.profile", ...)`: api_key principal ->
   profile_id, is_master = role == "master").
   Migration: new revision id `agents_p202_profile_key`, `down_revision = "agents_0002"`,
   `phase = "expand"`, adds nullable `agent_profiles.api_key_id` uuid (no cross-module FK).
   #78 (P1-06) takes `agents_0003`; whichever merges second rechains. Update models.py.
4. `auth/api.py authenticate_bearer`: a revoked `tmt` returns
   `AuthFailure("unauthenticated", "token_expired")` (T-12). T-P0-14-14 only checks 401.
5. `agents/mcp.py`: `get_task_packet` op (scope tasks:read, input `task_id` + optional
   `kind` in task/proposal/stuck, project via tasks.api.project_of like tasks/mcp.py
   `_project_of_task`, handler `packet_for_caller(call.session, task_id, run_id=
   call.caller.run_id, profile_id=call.caller.profile_id)`, output TaskPacket). REST twin
   `GET /tasks/{task_id}/packet` on agents/router.py (router is `v1_router("agents")`, not
   prefixed) with `RoutePolicy(auth="session_or_key", scopes=frozenset({"tasks:read"}),
   project_param="lookup:tasks")` calling `surface.rest_twin`. Remove `get_task_packet`
   from `PENDING_TOOLS` in core/agent_surface.py; add a `SAMPLES["get_task_packet"]` entry
   in backend/tests/_mcp.py (`{"task_id": str(world.parents[project])}`). The nonce is
   content-derived so both doors answer identically (T-P2-01-04 parity sweep).
6. Dispatch (T-15): in workflows.dispatch_step, before DaemonTransport.dispatch: read the
   profile's api_key_id and project (body["project"]["id"], else the profile's
   project_id); if both, `issue_run_token` and set `packet.callback` (create one with
   MCP_PATH/REST_BASE for enrich/plan packets). Keyless profile or no project: dispatch
   without a token (P1-04's locked tests use keyless profiles). In finish_step, call
   `api.run_ended` (idempotent). Then unmark T-10..T-15. T-15's `plan` case (master
   profile, no project) cannot get a token because `task_tokens.project_id` is NOT NULL:
   leave that case xfail (use pytest.param with a spec marker) and raise it for Scott.
   T-15's `_packet` helper calls `build_packet(...)` without `ctx=`: pass
   `ctx=workspace.ctx` (a call-signature fix in our own unmerged test, not an assertion).
7. Coverage: 100% line coverage on escaping and taint functions in agents/rules.py.
8. Docs: add P2-02 names to Part A of docs/IMPLEMENTATION-PLAN-DETAILED.md; fake_runner
   docstring mention of strict mode.
9. Finish: `make check` (semgrep env below), `make test`, `make test-int`, merge origin/main,
   open the PR per the prompt, `@coderabbitai review` once, run the review loop.

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
- TaskPacket's policy/callback are optional (no kind-conditional validator; see step 1).
- Profiles get a key only when linked (`set_profile_key`, ProfileIn/Patch api_key_id);
  nothing mints one yet. Scott item: who creates a profile's key (P1-06 provisioning?).
- Scott item: a plan run on the master profile has no project; task tokens need one
  (task_tokens.project_id NOT NULL). Options: project-less token for master runs, or no
  token for plan/notify runs.
- The token sits in runner_messages.payload (the daemon needs it); never logged.
- P2-08 follow-up: TaintSource.kind has no value for P2-01's keyless_write (note only).
- Shared-file edits so far: `backend/.importlinter` (one ignore line + comment).

## Verify commands

- Unit (no Docker): `cd backend && uv run pytest -q -p no:randomly tumnis/modules/agents/tests/unit`
- make check needs semgrep env (sandboxed): `SEMGREP_SETTINGS_FILE=/tmp/claude-1002/P2-02-c0/semgrep-settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/P2-02-c0/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/P2-02-c0/semgrep-version make check`
- Docker layers: bare `make test` and `make test-int` from the worktree root.
- Heredocs (`cat <<EOF`) and compound shell with globs are refused by the worktree guard;
  write scratch scripts to /tmp/claude-1002/P2-02-c0/ and run them with `python3 <file>`.
