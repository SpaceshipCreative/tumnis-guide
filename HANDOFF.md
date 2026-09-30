# HANDOFF: P2-13 · GitHub status on task cards (2nd handoff)

Handoff requested by the coordinator's context watcher ("HANDOFF NOW", ~352k tokens). **No PR is
open yet.** Branch `wp/P2-13`, pushed with `/usr/bin/git push origin HEAD:wp/P2-13` (never
force-push). Based on main `d92412b`. Work in an isolated worktree on a throwaway branch: first
`/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`, `/usr/bin/git merge origin/wp/P2-13`
(no `git switch`, `reset --hard` or `symbolic-ref`; the classifier denies them). Setup already
done in the previous worktree is not shared: run `cd backend && uv sync --frozen --all-extras` and
`npm ci --prefix frontend` in a new one.

## Commits on the branch

- `b020310` test(github): P2-13 spec tests (red)
- `4571d58` chore: P2-13 handoff (first handoff)
- `e8dffe0` feat(github): rules, read-only status adapter and fake, refresh workflows, webhook,
  PR links on tasks (everything below marked DONE), pushed.
- This handoff commit (`chore: P2-13 handoff (2)`).

## DONE (in `e8dffe0`)

Green, markers removed (Scott approved removal once tests pass): T-P2-13-10, 06, 03 (unit),
T-P2-13-05, 09 (contract). `uv run pytest -q tumnis/modules/github -m "not integration"`: 20
passed (incl. the new `GitHubStatusContract` classes, `TestGitHubStatusFake`,
`TestGitHubStatusRecorded`, added to `test_adapter_contract.py`).

Built (files under `backend/tumnis/`):

- `modules/github/rules.py` (views, `PrRef`, `Snapshot`, `parse_pr_url`, `allowed_repo`,
  `combine_checks`, `review_state`, `pr_state`, `pr_status`), `signature.py`
  (`verify_signature`).
- `modules/github/adapters/{port,github_status,fake,__init__}.py`: `GitHubStatus` port +
  `Fetched[T]`; `GitHubStatusApi` (GET only, api.github.com only, ETags, per_page=100, no
  pagination beyond 100); `FakeGitHubStatus` (dicts `pulls/statuses/check_runs/reviews`, `calls`,
  `not_modified`, `pause()/resume()`); lazy registration in `adapters/__init__.py`
  (`build_status` via importlib); import-linter `api-never-calls-out` now also forbids
  `tumnis.modules.github.adapters.github_status`.
- `modules/github/{api,payloads,events,models,router,workflows}.py`, migration
  `github/migrations/0001_github.py` (revision **`github_0001`**, `webhook_deliveries`).
  api: `GitHubSettings` section `github` (`token`, `allowed_repos`, `webhook_secret`),
  `fetch_pull_request`, `artifact_record`, `track_pull_request` (raises `NotAPullRequest` /
  `RepoNotAllowed`), `pull_requests`, `request_refresh` (60 s freshness, dedup id
  `refresh:<artifact_id>` on queue `github`), `enqueue_refresh`, `refresh_artifact`
  (one tx: `set_artifact_status`, raw payload with ETags, `github.fetched`, `mark_changed("task")`
  for linked tasks when changed), `pollable`, `accept_webhook`, `pull_request_key`.
  workflows: `use(factory, clock)`, plain `refresh(ws, artifact)` (tests call it directly) +
  step `refresh_read` + workflow `github_refresh_artifact`, `schedules()` (`github-poll`,
  `*/5 * * * *`, queue `github`), `github_poll_tick`, `open_pull_requests` step.
  router: `POST /v1/github/webhook/{workspace_id}` (auth none; 404 off, 401
  `invalid_signature`, 400 `missing_delivery_id`, 409 `duplicate_delivery`, 202).
- `modules/integrations/{api,payloads,events}.py`: `ArtifactUpdatedV1` (`artifact.updated`),
  `ArtifactOut`, `upsert_artifact`, `get_artifacts`, `find_artifacts`, `list_artifacts`,
  `set_artifact_status` (emits only on change), `get_raw_payload`, `context_owners`,
  `context_targets`.
- `modules/tasks/`: migration `migrations/0004_review_flags.py` (revision **`tasks_0004`**, after
  `tasks_0003`, `review_items.flags text[] NOT NULL DEFAULT '{}'`), `models.py` field,
  `review.py::flag_pull_request_results`, `api.py` (`PullRequestOut`, `link_pull_request`,
  `pull_requests`, `flag_red_checks`), `router.py` (`POST` and `GET /v1/tasks/{id}/pull-requests`),
  `events.py` (subscriber `tasks.flag_red_checks` on `artifact.updated`).
- `modules/usage/rules.py`: `COUNTERS["github.fetched"]` -> `github_requests`,
  `github_not_modified`. `worker.py`: queue `github` (`worker_concurrency=2`, limiter 15/60 s).
- `.importlinter`: the client edge above, plus test-only ignores for github tests importing
  tasks (`modules-api-only`: `github.tests.** -> tasks.events`; `module-graph-acyclic`:
  `github.tests.** -> tasks.api` and `tasks.events`). The contract name was left unchanged (a
  locked meta test asserts it).
- `backend/tests/contract/fixtures/events/{artifact.updated,github.fetched}/v1.json`, `make gen`
  run and committed (schemas, openapi, client, generated contract tests);
  `frontend/src/lib/live-map.ts` has `tasksListPullRequests` in `task.details`;
  `github/tests/recordings/.gitkeep` (repo-layout meta test).

`make check`: ruff, format, mypy, lint-imports and backend unit tests pass. Run it with
`SEMGREP_SETTINGS_FILE=/tmp/claude-1002/semgrep_settings.yml SEMGREP_LOG_FILE=/tmp/claude-1002/semgrep.log SEMGREP_VERSION_CACHE_PATH=/tmp/claude-1002/semgrep_ver make check`
(`~/.semgrep` is read-only). `frontend/src/components/project/ProjectList.test.tsx` failed once
under machine load and passes alone (flaky, unrelated).

## NOT DONE, exact next steps

1. **Integration spec tests (T-P2-13-01, 02, 04, 07, 08) are still marked `xfail(strict=True)`
   and have never run against the new code.** One file can be run sandboxed:
   `cd backend && rtk proxy uv run pytest -p no:cacheprovider -m integration <path> -x` (plain
   output is condensed to "No tests collected"; `rtk proxy` shows it). Remove one marker at a
   time (`@pytest.mark.xfail(strict=True, reason="spec:P2-13")` line), run, fix the code (never the
   test bodies/assertions), commit. Order: T-08 `test_allow_list.py`, T-01/02 `test_refresh.py`,
   T-07 `test_flags.py`, T-04 `test_webhook.py`. Then run the whole layer bare: `make test-int`.
   Things to watch: the `github` fixture patches `workflows.use(...)`; the tests call
   `workflows.refresh(...)` and `workflows.github_poll_tick(T0, None)` directly; the GET
   `pull-requests` route enqueues through `deadletter.dbos_client()` (the `dbos_client` /
   `app` fixtures must have configured it); `checked_at` is `fetched_at` only once `state` is set;
   the webhook enqueue happens inside the delivery transaction before commit; a review-item
   `flags` column must exist in the per-test DB (migration `tasks_0004`); `webhook_deliveries`
   RLS with `WorkspaceContext(ws, SYSTEM_ACTOR)`; DBOS step `refresh_read` retries on
   `AdapterUnavailable` only.
2. **Frontend T-P2-13-11**: `frontend/src/components/common/PrStatus.tsx` is still the stub
   (renders nothing; the test is `test.fails`). Build the chip per `PrStatus.test.tsx`: link
   (`target=_blank rel="noopener noreferrer"`, aria-label per the test: `Pull request
   <repo>#<n>, <title>: <state>, checks <passing|failing|running|...>, <review>` or `...#<n>:
   status not read yet`), words Open/Merged/Closed/Draft, Checks passing/failing/running, No
   checks, Approved/Changes requested/Review required/No review, "Checking…", `data-checks`
   attribute on the wrapper. Then remove `test.fails`. Add a "Pull requests" section (list + a
   link form posting `{url}`; surfaces 422 `not_a_pull_request` / `repo_not_allowed`) in
   `components/project/drawer/TaskDrawer.tsx` using the generated `tasksListPullRequests` /
   `tasksLinkPullRequest` hooks from `frontend/src/api/@tanstack/react-query.gen.ts`, check at
   375 px. Cards/rows and the result review item (P2-04 has no UI) are a noted deviation unless
   time allows. Run `npm --prefix frontend run typecheck` and Vitest.
3. Part A of `docs/IMPLEMENTATION-PLAN-DETAILED.md`: add the new names (A12 row: revisions
   `github_0001`, `tasks_0004`; events `artifact.updated` payload and `github.fetched`; queue
   `github`; schedule `github-poll`; settings section `github`; routes; usage counters
   `github_requests`, `github_not_modified`; the api names above).
4. Re-run `make gen` if any API shape changes; `make check`, `make test`, `make test-int`,
   Vitest all green. Delete this file in a `chore` commit before the final push, unless you hand
   off again.
5. Open the PR per `~/tumnis-coordinator/prompts/P2-13.md`: title `[P2-13] impl: GitHub status on
   task cards`, body file in `$TMPDIR` (summary, per-layer results, shared-file edits, every
   deviation, "For Scott") ending with a blank line and
   `🤖 Generated with [Claude Code](https://claude.com/claude-code)`;
   `gh pr create --base main --head wp/P2-13 --title ... --body-file <file>`, then
   `gh pr comment <url> --body "@coderabbitai review"`. Run the review loop
   (`~/tumnis-coordinator/pr-review-loop.md`). Never merge.

## Shared-file edits so far (list them in the PR body)

`backend/.importlinter`, `backend/tumnis/worker.py` (queue), `backend/tumnis/modules/usage/rules.py`
(counter map), `backend/tumnis/modules/tasks/{models,api,router,events,review}.py` and migration
`tasks_0004`, `backend/tumnis/modules/integrations/api.py`, generated `schemas/`, `frontend/src/api/`,
`backend/tests/contract/generated/`, `frontend/src/lib/live-map.ts`. No change to `pyproject.toml`,
`uv.lock`, `Makefile`, `AGENTS.md`, `package.json`.

## Deviations from the plan (reasons)

- `verify_signature` is in `github/signature.py`, not `rules.py`: the rules meta-test allow-list
  has no `hmac`/`hashlib` (adding them needs Scott, cf. decision 2).
- Webhook path is `POST /v1/github/webhook/{workspace_id}`: GitHub sends no identity but the
  signature, so the path names the workspace whose secret verifies it.
- Own `github` DBOS queue with a limiter (15/min, 2 workers) instead of the shared `sync` queue:
  DBOS limiters are per queue. The poll tick runs on it too.
- The refresh-on-open is `GET /v1/tasks/{id}/pull-requests` (what the drawer calls), not
  `GET /v1/tasks/{id}` (every live task message refetches that one).
- `track_pull_request` raises `NotAPullRequest` / `RepoNotAllowed` (tasks maps them to 422) rather
  than returning None; nothing is written and no call is made either way.
- Lists (statuses, check runs, reviews) are read as the first 100 entries; no pagination.
- `PullRequestOut.checked_at` is `artifacts.fetched_at` once the state is set, else None (the
  column is NOT NULL; a stored, never-read artifact has no state).
- Import-linter: test-only ignore edges for github's integration tests that drive tasks' api and
  subscriber (precedent: usage/search tests -> tasks.api).
- `PrRef` lower-cases owner and repository (GitHub matches without regard to case), so the
  artifact's canonical URL and `pr_status.repo` are lower case.

## Scott items (the coordinator relays any change)

- Webhook path carries the workspace id (`/v1/github/webhook/{workspace_id}`).
- `verify_signature` lives in `signature.py` instead of `rules.py` (rules allow-list lacks
  `hmac`/`hashlib`); alternatively approve adding them like `unicodedata` (decision 2).
- Own `github` DBOS queue with a limiter instead of the shared `sync` queue.
- The PR body must also list: the GitHub token is Tumnis's own read-only fine-grained token
  (Settings > GitHub, `github` section), the webhook stays off until a secret is set, and the
  homelab needs no relay yet (polling is the tested path).
