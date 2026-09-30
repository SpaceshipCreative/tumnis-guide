# HANDOFF: P2-13 · GitHub status on task cards

Handoff requested by the coordinator's context watcher ("HANDOFF NOW") right after the spec
tests were written. **No PR is open yet.** Branch `wp/P2-13` (pushed with
`/usr/bin/git push origin HEAD:wp/P2-13`), based on main `d92412b` (#57 merged).

## State

- Commit 1 (see `git log --oneline -3`): `test(github): P2-13 spec tests (red)` + this file
  (`chore: P2-13 handoff`).
- Red confirmed: unit and contract (`uv run pytest -q tumnis/modules/github -m "not integration"`:
  12 xfailed) and Vitest (`npx vitest run src/components/common/PrStatus.test.tsx`: 1
  expected fail).
- **Not yet confirmed red: the integration spec tests** (T-01, 02, 04, 07, 08). Until
  `adapters/fake.py` and `workflows.use()` exist, the `github` fixture errors at setup
  (ImportError) instead of xfailing. Build the fake and `workflows.use` first, then run
  `make test-int` (bare, from the worktree root) and check they xfail for the right reason.
- `make check`: ruff/format/eslint/tsc pass; **mypy fails only on `import-untyped` for the
  not-yet-existing `github.adapters.{port,fake,github_status}` modules** (inherent to the red
  step). It passes once those modules exist. Nothing else was changed.
- No alembic revision added yet. No shared-file edits yet.

## Spec tests written (bodies are locked from now on)

| ID | File :: test |
| --- | --- |
| T-P2-13-10, 06, 03 | `backend/tumnis/modules/github/tests/unit/test_github_rules.py` (`test_parse_pr_url`, `test_combine_checks_and_review_tables`, `test_verify_signature`) |
| T-P2-13-05 | `backend/tumnis/modules/github/tests/contract/test_recordings.py::test_recordings_map_to_artifacts[<8 recordings>]` |
| T-P2-13-09 | `backend/tumnis/modules/github/tests/contract/test_adapter_contract.py::test_fake_and_recordings_agree` |
| T-P2-13-01, 02 | `backend/tumnis/modules/github/tests/integration/test_refresh.py` |
| T-P2-13-04 | `backend/tumnis/modules/github/tests/integration/test_webhook.py` |
| T-P2-13-07 | `backend/tumnis/modules/github/tests/integration/test_flags.py` |
| T-P2-13-08 | `backend/tumnis/modules/github/tests/integration/test_allow_list.py` |
| T-P2-13-11 | `frontend/src/components/common/PrStatus.test.tsx` (stub `PrStatus.tsx` renders nothing) |

Helpers (no assertions): `github/tests/replay.py` (recorded HTTP replay, `recorded_api`),
`github/tests/integration/_github.py` + `conftest.py` (`app_db`, `github` fixtures).
Recordings: `github/tests/recordings/github/*.json` (8 cases, generated and scrubbed: invented
owners `acme-example`, `brio-example`, `cove-example`, `dune-example`; no avatars/emails).

**Add in the green step** (non-spec contract classes, kept out of the red commit because
they import modules that do not exist yet): in `test_adapter_contract.py`, import
`AdapterContract`, `AdapterRejected`, `FakeGitHubStatus`, `GitHubStatus` and `first_round`
at module level, then add, above `test_fake_and_recordings_agree`:

```python
class GitHubStatusContract(AdapterContract[GitHubStatus]):
    port, adapter_name = GitHubStatus, "github.status"
    # test_every_recorded_pull_reads_back: for each recording, _read_all(subject, **rec["pull"])
    #   matches first_round(rec) bodies (number, head.sha, html_url, statuses (context,state),
    #   check runs (name,status,conclusion), reviews (user.login,state)).
    # test_an_etag_answers_not_modified: COVE_5 get_pull(None) -> etag; get_pull(etag) ->
    #   value None, .not_modified; same for list_check_runs.
    # test_unknown_pull_is_rejected: get_pull("acme-example","site",999) and
    #   list_reviews("nobody-example","none",1) raise AdapterRejected.
    # test_health_is_ok: await subject.health() == "ok".
class TestGitHubStatusFake(GitHubStatusContract): impl = "fake"; subject -> FakeGitHubStatus()
class TestGitHubStatusRecorded(GitHubStatusContract): impl = "recorded"; subject -> recorded_api(_all())
# both marked @pytest.mark.contract, req FR-12.1, wp P2-13
```

## Design the tests fix (implement to this)

**github.rules** (pure; pydantic views): `Login(login)`, `Ref(sha)`, `PullView(number,
state: open|closed, merged=False, merged_at=None, draft=False, title, html_url, head: Ref,
requested_reviewers: list[Login]=[])`, `StatusView(context, state)`,
`CheckRunView(name, status, conclusion|None)`, `ReviewView(user: Login|None, state,
submitted_at|None)`; `PrRef(owner, repo, number)` (external id `owner/repo#n` lowercased,
URL `https://github.com/{owner}/{repo}/pull/{n}`); `Snapshot(pull, statuses, check_runs,
reviews, etags: dict)`; `parse_pr_url` (https only, `github.com`/`www.github.com`, no
userinfo/port, owner `[A-Za-z0-9-]{1,39}`, repo `[A-Za-z0-9._-]{1,100}`, n >= 1, trailing
path/query/fragment ok); `allowed_repo(owner, repo, allow_list)` (case-insensitive,
`owner/*` wildcard, empty list allows nothing); `combine_checks` (red set: failure, error,
cancelled, timed_out, action_required, startup_failure; green set: success, neutral,
skipped; anything else pending; none if empty); `review_state` (latest APPROVED /
CHANGES_REQUESTED / DISMISSED per reviewer; COMMENTED never overrides; changes wins; any
review but no standing decision -> review_required; no reviews -> none); `pr_state`;
`pr_status(ref, snapshot)` -> dict for `checks.pr_status` = {repo, number, title, draft,
head_sha, checks, review}, with review `none` + requested reviewers -> `review_required`.

**github.signature.verify_signature(secret, body, header)** lives in `signature.py`, not
`rules.py` (deviation: the rules meta-test allow-list has no `hmac`/`hashlib`; adding them
would need Scott, cf. decision 2). Case-insensitive hex, `sha256=` prefix only, empty
secret refused, `hmac.compare_digest`.

**Adapter** (`adapters/port.py`: `GitHubStatus` Protocol + `Fetched[T](value: T|None, etag:
str|None, not_modified property)`; `adapters/github_status.py`: `GitHubStatusApi(Adapter)`
name `github.status`, base `https://api.github.com` only, GET-only wrapper over
`guarded_client`, headers `Accept: application/vnd.github+json`,
`X-GitHub-Api-Version: 2022-11-28`, `Authorization: Bearer <token>`, `If-None-Match`,
`per_page=100` on status/check-runs/reviews; 304 -> `Fetched(None, etag)`; 429/5xx and 403
with `x-ratelimit-remaining: 0` -> AdapterUnavailable; other non-2xx -> AdapterRejected;
`RetryPolicy(max_attempts=1)`; ctor `(token, policy=None, resolver=system_resolver,
transport=None, clock=None)`). Register **lazily** in `adapters/__init__.py` (importlib, as
decisions does) and add `tumnis.modules.github.adapters.github_status` to import-linter
`api-never-calls-out` (the plan's "github contract" edit).
**Fake** (`adapters/fake.py`): loads round-1 exchanges of every recording into plain dicts
`pulls[(o,r,n)]`, `statuses[(o,r,sha)]`, `check_runs[(o,r,sha)]`, `reviews[(o,r,n)]`
(lowercase keys); etag = hash of the current JSON (scripting a dict changes it);
`calls: list[(op, key)]` with key `o/r#n` for pull/reviews and the sha for
status/check runs; `not_modified: int`; `pause()`/`resume()` (threading.Event awaited via
`asyncio.to_thread`); unknown -> AdapterRejected("404"); `health()` -> "ok".

**github.api**: settings section `github` = `GitHubSettings(token (secret), allowed_repos:
list[str], webhook_secret (secret))`; `fetch_pull_request(status, ref, previous: Snapshot|None)
-> Fetch(snapshot, requests, not_modified)` (pull, then status/check-runs of head sha,
then reviews; reuse previous etags only when the head sha is unchanged; 304 keeps the
previous part); `artifact_record(ref, snapshot, fetched_at) -> integrations ArtifactRecord`
(kind `pull_request`, url = provider_url = canonical URL, state, checks
`{"pr_status": ...}`); `PullRequestOut(artifact_id, url, repo, number, title, state, draft,
checks, review, checked_at)`; `track_pull_request(ctx, url, *, now, session)` -> None when
not a PR URL / outside the allow-list (no write, no call); `request_refresh` enqueues via
`deadletter.dbos_client().enqueue_async({"queue_name": "github", "workflow_name":
"github_refresh_artifact", "deduplication_id": f"refresh:{id}", "duplication_policy":
"return-existing"}, workspace_id, artifact_id)` only when `checked_at` is None or older
than a freshness window (so a live refetch after a refresh does not loop); events
`github.fetched` (`GitHubFetchedV1(requests, not_modified)`) for the usage counters.
Snapshot + etags are stored as the artifact's raw payload
(`integrations.api.store_raw_payloads`, record_type `artifact`), `checked_at` in
`artifacts.fetched_at`.

**integrations.api additions** (it owns `artifacts`): `ArtifactUpdatedV1` (`artifact.updated`:
artifact_id, kind, url, state, checks) registered here (github and later coolify emit it);
`ArtifactOut`; `upsert_artifact(ctx, *, connection_id, kind, external_id, url, now,
session)`; `get_artifacts`; `list_artifacts(kind, open_only)` (state NULL or open);
`set_artifact_status(ctx, id, *, state, checks, fetched_at, session)` emits
`artifact.updated` only when state/checks changed; `context_owners(ctx, target_type,
target_id, owner_type)`; a raw-payload reader. GitHub connection: `upsert_connection(kind
"code", provider "github", account "api.github.com")`.

**workflows**: queue `github` (register in `worker.register_queues`, limiter
`{"limit": 15, "period": 60}` plan-default-style; deviation: DBOS limiters are per queue, so
not the shared `sync` queue); `@DBOS.workflow(name="github_refresh_artifact")
refresh_artifact(workspace_id, artifact_id)` -> step `refresh(workspace_id, artifact_id)`
(callable directly in tests; skips when the module is disabled or, in real mode, no
token): fetch, `set_artifact_status`, store snapshot, emit `github.fetched`,
`mark_changed(s, "task", owner)` for every task linking the artifact (via
`context_owners`); `github_poll_tick` (`schedules()` -> `github-poll`, `*/5 * * * *`):
per enabled workspace, enqueue `refresh_artifact` for open PR artifacts
(`SetEnqueueOptions(deduplication_id=..., duplication_policy="return-existing")`);
`use(factory, clock) -> previous` seam.

**router**: `POST /v1/github/webhook/{workspace_id}` (deviation: GitHub sends no
identity but the signature, so the path names the workspace) `RoutePolicy(auth="none",
idempotent=False, not_idempotent_reason="X-GitHub-Delivery is the key; a repeat is 409",
csrf=False, csrf_exempt_reason=..., max_body_bytes=1_048_576)`: module off or no secret ->
404 `not_found`; bad signature -> 401 `invalid_signature` (nothing stored); insert
`webhook_deliveries(delivery_id, received_at)` ON CONFLICT DO NOTHING -> 409
`duplicate_delivery`; else enqueue refresh for a tracked, allowed PR in `pull_request` /
`check_run` / `check_suite` payloads; 202.
**migration** `github/migrations/0001_github.py`: revision `github_0001`, branch
`github`, depends on `auth_0001`, `create_tenant_table("webhook_deliveries", delivery_id
text, received_at timestamptz, unique (workspace_id, delivery_id))`; model in `models.py`.

**tasks**: migration `tasks_0004` (after current tasks head; check with `grep revision
backend/tumnis/modules/tasks/migrations/*.py`) adding `review_items.flags text[] NOT NULL
DEFAULT '{}'` (A12 lists the column) + model field; `api.link_pull_request(s, actor,
task_id, url, *, now)` (422 `not_a_pull_request` / `repo_not_allowed`, else
`github.track_pull_request` + `integrations.link_context(owner task, target artifact)` +
`link_context_item`, returns `PullRequestOut`); `api.pull_requests(s, task_id, *, now)`
(stored status + `request_refresh`); routes `POST /v1/tasks/{task_id}/pull-requests`
(WRITE_TASK, 201) and `GET /v1/tasks/{task_id}/pull-requests` (READ_TASK,
`unpaginated_reason`); subscriber `tasks.flag_red_checks` on `artifact.updated` in
`events.py`: open `result` review items whose `payload.links[].url` parses to the same
PR (use a github api helper for the key) get `checks_red` added (red) or removed.
Deviation: the refresh-on-open is `GET /v1/tasks/{id}/pull-requests` (what the drawer
calls), not `GET /v1/tasks/{id}` (every live task message refetches that one).

**usage**: `rules.COUNTERS["github.fetched"] = (("github_requests", requests),
("github_not_modified", not_modified))` -> visible as `tumnis_usage_total` on /metrics
(the worker serves no /metrics).

**Contract fixtures + gen**: `backend/tests/contract/fixtures/events/artifact.updated/v1.json`,
`.../github.fetched/v1.json`; run `make gen`; add `tasksListPullRequests` to
`frontend/src/lib/live-map.ts` task.details.

**Frontend**: `PrStatus.tsx` chip (link `target=_blank rel="noopener noreferrer"`,
aria-label per the test, words: Open/Merged/Closed/Draft, Checks passing/failing/running,
No checks, Approved/Changes requested/Review required/No review, "Checking…" before the
first read, `data-checks` attribute); a "Pull requests" section in
`components/project/drawer/TaskDrawer.tsx` (list + link form). Cards/rows and the result
review item (P2-04 has no UI yet) are a noted deviation unless time allows.

## Remaining steps

1. Implement in the plan's TDD order, removing one `spec:P2-13` marker at a time: rules and
   signature (T-10, 06, 03); port, adapter, fake, lazy registration + contract classes
   (T-05, 09); integrations/tasks/github api + migrations (T-08); workflows + queue +
   schedule (T-01, 02); flags subscriber (T-07); webhook route (T-04); chip + drawer (T-11).
2. Part A: add the new names (A12 row: revisions `github_0001`, `tasks_0004`, events
   `artifact.updated` payload, `github.fetched`, queue `github`, schedule `github-poll`,
   settings section `github`, routes) in `docs/IMPLEMENTATION-PLAN-DETAILED.md`.
3. `make check`, `make test`, `make test-int`, Vitest; `make gen` committed.
4. Open the PR per `~/tumnis-coordinator/prompts/P2-13.md` (title `[P2-13] impl: GitHub status
   on task cards`, body file in $TMPDIR ending with the Claude Code line), comment
   `@coderabbitai review`, run the review loop. Never merge.

## Scott items

- Webhook path carries the workspace id (`/v1/github/webhook/{workspace_id}`).
- `verify_signature` in `signature.py` instead of `rules.py` (rules allow-list lacks
  `hmac`/`hashlib`); alternatively approve adding them like `unicodedata` (decision 2).
- Own `github` DBOS queue with a limiter instead of the shared `sync` queue.
