# P2-17 handoff (Inbox, Activity, Ask the agent and knowledge tools)

## Continuation c1 status (read this first; the c0 notes below still hold)

Status: **backend implemented and committed; frontend half done; no PR opened yet.** All spec markers are still in place (none removed: no layer has run them yet).

- Push with `/usr/bin/git push origin HEAD:wp/P2-17` (never switch branches; work on the throwaway branch after `/usr/bin/git fetch origin; /usr/bin/git merge origin/main; /usr/bin/git merge origin/wp/P2-17`).
- Scratch folder of c1: `/tmp/claude-1002/P2-17-c1/` (helper scripts; `parity.py` is a no-database check of T-P2-01-02/03 for every op: `cd backend && uv run python /tmp/claude-1002/P2-17-c1/parity.py` should print `bad 0`; `routes_check.py` prints route-convention violations, should be `[]`).
- Commits of c1: `3893a8e feat(agents): P2-17 knowledge tools, citations, ask, activity, feed and inbox` (backend + `make gen` output), then the WIP frontend + this handoff commit.
- A local `make test-int` was started in the background by c1 and its result was lost at handoff. Don't run it again this continuation unless needed; open the PR and use CI instead (DOCKER LOAD RULE).

### Backend done in c1 (unit layer green: 1687 passed; ruff, mypy, lint-imports green; static parity `bad 0`; route conventions `[]`)
- core: `routing.WORKSPACE_ROW = UUID(int=0)`; `project_of_request` and `agent_surface._project` treat it as "no project" (a project-limited caller may read a workspace-KB row). The three knowledge tools removed from `PENDING_TOOLS`. `audit.record(..., project_id=)` puts `project_id` in details; `audit.list_for_project(session, project_id, *, before, limit)` (two literal SQL statements, keyset on `(occurred_at, id)`).
- knowledge: `mcp.py` registers `search_knowledge` (twin `GET /v1/knowledge/search`, now `Page[KnowledgeHit]`, params spelled out so `cursor`/`limit` show; offset cursor via `api.search_page`, depth cap 500), `get_document` (twin `GET /v1/knowledge/documents/{id}` with `schema_version` query, policy `lookup:knowledge_read` -> `api.readable_scope`), `add_document` (twin `POST /v1/knowledge/documents/text`; `TextEntryIn` moved to mcp.py as a SurfaceInput with `tags`; a session keeps P1-17's trusted note path, any other caller goes to `api.add_document` -> untrusted, source agent, file under `agent-outputs/`). `api.add_document` now inserts then runs `_write_text(..., subfolder=AGENT_OUTPUTS)`; `subfolder` threaded through `_write_text` -> `save_note` -> `note_path`. `create_text_entry(tags=)`. Net for twins: `api.using_net(...)` ContextVar set by the router; MCP falls back to `api.deployment_net()` (and `sync.net()` now calls it). `api.citation_title`. SAMPLES for the three ops in `tests/_mcp.py`; `"knowledge_read": document_row` in `tests/meta/_authz.py`.
- tasks: `ResultUrl` accepts `tumnis://doc/<uuid>(#page=n)` (plain regex groups: the pattern also goes to JSON Schema/zod), `ResultLink` model_validator (tumnis:// only for kind document), `cited_document(link)`; `results_for_project`; `inbox()` + `list_review_items(project_id=)`; route `GET /v1/projects/{project_id}/inbox` (session, paginated) in tasks router.
- agents: `_cite` in `accept_result` (422 `invalid_citation`, default label "<title>, page <n>"); `ask` (+ `AskIn`, `AskOut`), `activity` (+ `ActivityItem`, merged keyset over task runs, results, audit; runs filtered to kind `task` so enrichment runs never show), `agent_feed` (+ `FeedRun`, `AgentFeedOut`, kind `task` only). Routes in agents router: `POST /v1/projects/{project_id}/ask` (session, idempotent, 201), `GET /v1/projects/{project_id}/activity` (session, paginated), `GET /v1/agents/feed` (session, unpaginated_reason). `project_id` added to `agent.gated_action` (human.py) and `approval.granted/denied` (review_kinds.py, via new `review_kinds.project_of_run`). Not to `approval.auto` (locked exact-details assert).

### Frontend state (uncommitted work committed as WIP in the handoff commit; typecheck green, lint and Vitest NOT run)
- Done: `lib/views.ts` (+inbox, activity), `ViewSwitcher.tsx` (labels; phone grid `grid-cols-5`, `text-xs sm:text-sm`; check 375 px, no sideways scroll), `project/InboxView.tsx` (exports `inboxQuery`), `project/ActivityView.tsx` (useInfiniteQuery, `initialPageParam: null`, "Load more").
- Remaining, in order:
  1. `ProjectPage.tsx`: render `InboxView` / `ActivityView` when `view` is inbox/activity (they fetch only when shown).
  2. `Composer.tsx`: "Ask the agent" toggle button (`aria-pressed`), input `aria-label` switches "New task" / "Ask the agent"; ask via `useWrite` + `apiWrite({kind:"create", method:"POST", path:`/projects/${id}/ask`, body:{question}, schema: zAskOut})`, then `invalidateTaskViews(client)` and reset to task mode, clear input; 409 `no_ready_profile` -> `uiStore.trigger.showNotice({text: "This project has no ready agent yet."})`. Task mode must still send exactly `{project_id, title}` through the offline queue (T-P0-24-14). No chat UI, no role=log/dialog.
  3. `project/AgentRail.tsx` (props `projectId, open, onToggle, pause?`) using `RailSection` title "Agent": summary `"<name> · <Healthy|Degraded|Offline|Warning|Not checked yet>"` from `projectProfileQuery(projectId)` (settings/queries), "No agent yet" without a profile; open: `Hermes <health.version>`, `<ul aria-label="Workers and tools">` of `profileToolsQuery(profile.id)` servers' names (query `enabled: open`), then the `pause` slot. Mount in `rail/RailSections.tsx` (add "agent" section). #118 (P2-09) adds `rail/AgentPauseSection.tsx`; whichever merges second passes `<PauseControl projectId>` as `pause`.
  4. `dashboard/ActivityFeed.tsx`: feed from `agentsGetAgentFeedOptions()`, groups Running / Waiting on you / Finished / Failed, keep `<details>` collapsed, summary "Agent activity", text "Nothing from the agents yet." whenever empty (locked T-P0-23-07); add a default `/v1/agents/feed` handler (all groups empty) to `test/msw/dashboard.ts` `dashboardDefaults`.
  5. `review/Citation.tsx`: `tumnis://doc/<id>#page=<n>` -> link to `/v1/files/<id>#page=<n>` (use `apiUrl`) with the label; used by the review `ResultItem.tsx` for document links; never emit `href="tumnis://…"`.
  6. MSW: default handlers for every new read the project page makes on mount in `test/msw/project.ts` (`ProjectFake.handlers`): `/v1/agents/profiles/:id/tools`, `/v1/projects/:id/inbox`, `/v1/projects/:id/activity` (MSW is `onUnhandledRequest: "error"`).
  7. Flip `test.fails` -> `test` for T-P2-17-07/08/09 only after each passes (keep the dynamic `import(/* @vite-ignore */ path)` loads). Run `npm --prefix frontend run lint`, typecheck, `npm --prefix frontend run test -- --run --maxWorkers=4`.
- Then: `make check` (pytest part with `-n 3` by hand; semgrep env vars under `/tmp/claude-1002/P2-17-c1/`), push, open the PR (body: below + Context7 refs: TanStack Query v5.104 `useInfiniteQuery` initialPageParam/getNextPageParam; pydantic 2.13 `model_validator(mode="after")` and Rust-regex patterns (no named groups in JSON Schema patterns); SQLAlchemy 2.1 `tuple_` keyset with typed `literal()` as in core.pagination), comment `@coderabbitai review` once, then let CI run the integration layer: remove the xfail(strict) markers of T-P2-17-01..06 one at a time once CI (or a local run) shows them XPASS. Watch the P2-01 meta sweeps (parity, authz matrix, write rules, taint sweep) and P1-17 knowledge tests in CI: they now cover the three new ops.

### Possible risks for the next agent to check in CI
- `test_text_trust`/`test_text_taint` (P1-17) post key-written entries to the twin: now `api.add_document` (agent-outputs/); trust/taint/label assertions should still hold.
- `test_project_limited_key...` T-P2-01-06 for `get_document`: limited-A key reading B's doc -> lookup returns B -> 404 (expected).
- T-P2-17-02 needs exactly one Acme audit row: if any other audit row with `project_id` is written during the run flow, it would fail.
- Activity `at` for runs is `created_at`; results `created_at`; audit `occurred_at`.

---

## c0 notes (still valid)


Status: spec tests committed red; no implementation yet; **no PR opened**; nothing pushed before this handoff.

- Branch: `wp/P2-17` (renamed from the throwaway; `.git/config` is read-only, so push with `/usr/bin/git push origin HEAD:wp/P2-17`).
- Base: main at `41fc850` (merge of #122). Run `/usr/bin/git fetch origin` and `/usr/bin/git merge origin/main` first; regenerate `frontend/src/api/`, `schemas/` and `backend/tests/contract/generated/` with `make gen` after any merge. Never hand-merge them.
- Commits: `66cf77a test(agents): P2-17 spec tests (red)`, then this handoff commit.
- Scratch folder: `$TMPDIR/P2-17-c0/` (`/tmp/claude-1002/P2-17-c0/`).
- Migrations: none needed (task `source="ask"` has no CHECK; audit `project_id` lives in `details`).

## Spec tests (all red)

| ID | File | State |
| --- | --- | --- |
| T-P2-17-01 | `backend/tumnis/modules/agents/tests/integration/test_ask.py` | xfail strict (not run locally, Docker) |
| T-P2-17-02 | `backend/tumnis/modules/projects/tests/integration/test_activity.py` | xfail strict (not run locally) |
| T-P2-17-03 | `backend/tumnis/modules/agents/tests/integration/test_feed.py` | xfail strict (not run locally) |
| T-P2-17-04..06 | `backend/tumnis/modules/knowledge/tests/integration/test_knowledge_tools.py` | xfail strict (not run locally) |
| T-P2-17-07 | `frontend/src/components/project/Composer.test.tsx` (new test appended) | test.fails, confirmed red |
| T-P2-17-08 | `frontend/src/components/project/AgentRail.test.tsx` (2 tests) | test.fails, confirmed red |
| T-P2-17-09 | `frontend/src/components/project/InboxView.test.tsx` | test.fails, confirmed red |

The new component tests load the component with a dynamic `import(/* @vite-ignore */ path)` (P2-05's red pattern); keep it when flipping `test.fails` to `test`. T-02 passes `project_id` to `audit.record` via `**scoped` so mypy passes before the keyword exists.

`.importlinter` (shared file) got test-only ignores: `projects.tests.** -> agents.**` (api-only and acyclic contracts) and `knowledge.tests.** -> agents.api` (acyclic).

## Design decided (implement this)

Read the transcript notes below; the advisor reviewed them.

**Knowledge ops** (`backend/tumnis/modules/knowledge/mcp.py`, register with `agent_surface.register_op`; remove the three names from `agent_surface.PENDING_TOOLS`; add SAMPLES for all three in `backend/tests/_mcp.py`):
- `search_knowledge`: `SearchKnowledgeIn(q: str 1..500, project_id: UUID|None, limit 1..50 default 10, cursor)`, scope `context:read`, `project_arg="project_id"`, output `Page[KnowledgeHit]`. Twin: rewrite `GET /v1/knowledge/search` in `knowledge/router.py` to call `agent_surface.rest_twin` (add `cursor`, `limit` query params by hand with the same bounds, `schema_version`; policy `paginated=True`, drop `unpaginated_reason`). Cursor: offset cursor via `core.pagination.Cursor((offset,), last.chunk_id)`. Locked P1-17 tests and A1.5 already accept `{"items": [...]}`.
- `get_document`: `GetDocumentIn(document_id)`, scope `context:read`, `project_resolver` = a new knowledge lookup `knowledge_read` that returns the doc's project, or `routing.WORKSPACE_ROW` for a live workspace-KB doc, or None (404). Twin: `GET /v1/knowledge/documents/{document_id}` with `schema_version` query and `project_param="lookup:knowledge_read"`. Core change (small, additive): `routing.WORKSPACE_ROW: Final = UUID(int=0)`; in `routing.project_of_request` return None when the lookup answers it; in `agent_surface._project` return None when the resolver answers it. Add `"knowledge_read": document_row` to `LOOKUP_TARGETS` in `backend/tests/meta/_authz.py`. Keep `lookup:knowledge` on PATCH/DELETE/trust/versions/files, so writes to the KB stay 404 for limited keys.
- `add_document`: input = `WriteInput` + the existing `TextEntryIn` (`project_id: UUID|None`, `title`, `body_md` max 100_000) + `tags: list[str]` (max `api.MAX_TAGS`). Twin: `POST /v1/knowledge/documents/text` (201, output `DocumentDTO`). Handler: a session caller keeps P1-17's path (`create_text_entry`, trusted, `notes/`); any other caller goes to `api.add_document` (untrusted, `source="agent"`, tainted per `call.tainted`, file `agent-outputs/<sanitized title>.md`). A project-limited caller with `project_id=None` answers 404 (as `_scoped_ctx` did). Thread `folder: str = "notes"` through `note_path` -> `save_note` -> `_write_text`; refactor `api.add_document` to insert the row then run `_write_text(..., folder="agent-outputs")`, keeping its kwargs (`body_markdown`, `now`; P2-08's locked tests call them) and adding `net: NetPolicy | None = None`, `project_id: UUID | None`, plus a `_check_project`. Add `tags` to `create_text_entry`. Net for the op: a `ContextVar` in knowledge api that the router sets from `request.app.state.settings.net_policy()` around `rest_twin`; MCP falls back to a `deployment_net()` (Settings() or self-hosted, as `sync.net()` does; make `sync.net()` call it).

**Citations**: `tasks.ResultLink.url` accepts `^https?://\S+$` or `tumnis://doc/<uuid>(#page=<n>)?`, and a `model_validator(mode="after")` allows `tumnis://` only for kind `document`. In `agents.api.accept_result`, before `tasks.post_result`: for each document link, check that the doc is live and in the run task's project or the workspace KB (else 422 `invalid_citation`), and fill `label` as `"<title>, page <n>"` (or the title alone) when absent. Only `schemas/openapi.json` and `tools.json` carry the pattern (the daemon has no copy).

**Ask / Activity / Feed / Inbox** (routes cannot live in `projects/router.py`: projects may not import agents or tasks, because the module graph is acyclic. That's a deviation to record):
- agents router: `POST /v1/projects/{project_id}/ask` (session, `tasks:write`, idempotent, path project param, 201 `AskOut{task: TaskOut, run_id}`): in the route's one transaction `tasks.create_task(source="ask", label="ai", title=question[:500], first_action="Answer the question", acceptance_criteria="An answer with sources")` (a question over 500 characters is truncated with "…" and the full text added as a comment), then `request_run(task.id, RunKind.TASK, ctx=..., session=s)`; a 409 rolls everything back.
- agents router: `GET /v1/projects/{project_id}/activity` (session, `paginated=True`, `cursor`/`limit`): `ActivityItem{kind: run|result|audit, id, at, task_id, run_id, task_title, status, summary, action, actor_type}`, merged keyset (`at desc, id desc`) over runs (agents), `tasks.results_for_project(...)` (new tasks api) and `core.audit.list_for_project(...)` (new). Fetch `limit+1` from each, merge, cut; cursor `Cursor((at,), id)`.
- agents router: `GET /v1/agents/feed` (session): `AgentFeedOut{running, waiting, finished, failed}` of `FeedRun{run_id, task_id, task_title, project_id, kind, status, at}`; running = queued+running, waiting = waiting_on_human+held, finished = succeeded, failed = failed+timed_out+runner_lost; cancelled is shown in none; the last 10 per group; task titles via `tasks.tasks_by_ids`.
- tasks router: `GET /v1/projects/{project_id}/inbox` (session, paginated): open `proposal` review items of the project (empty until P3-07).
- `core/audit.py`: `record(..., project_id: UUID | None = None)` puts `str(project_id)` into details; `list_for_project(session, project_id, *, before, limit)`. Add `project_id` to `agent.gated_action` (agents/human.py ~l.287) and `approval.granted/denied` (agents/review_kinds.py ~l.174). **Not** to `approval.auto`, because locked T-P2-05 `test_default_policy` pins its details exactly (reported to main as a Scott item).

**Frontend**:
- `lib/views.ts` + ViewSwitcher: add `inbox`, `activity` (phone segmented control is `grid-cols-3`; with 5 segments, make it scroll or wrap and check at 375 px).
- `ProjectPage.tsx`: render `InboxView` / `ActivityView` for those views; fetch only when that view is selected.
- `Composer.tsx`: an "Ask the agent" toggle (`aria-pressed`), input label switches between "New task" and "Ask the agent"; ask uses `useWrite` + `apiWrite` (not the offline queue), then invalidates the project's tasks and resets to task mode. Show 409 `no_ready_profile` as a notice. Task mode must still send exactly `{project_id, title}` (T-P0-24-14).
- `project/AgentRail.tsx` (props `projectId, open, onToggle, pause?: ReactNode`), using `RailSection`: summary `"<name> · <Healthy|Warning|Degraded|Offline|Not checked yet>"` or "No agent yet"; open: name, `Hermes <version>`, list "Workers and tools" from `profileToolsQuery` (enabled only when open), the pause slot. Mount it in `rail/RailSections.tsx`. #118 (P2-09) adds `rail/AgentPauseSection.tsx` with `PauseControl`; whichever merges second passes `<PauseControl projectId>` as `pause` and drops AgentPauseSection.
- `project/InboxView.tsx`: empty copy "No proposals yet" + "Tasks the agent suggests from this project's email, chat and notes wait here."; otherwise `<ul aria-label="Inbox">` of `target_title`.
- `project/ActivityView.tsx`: useInfiniteQuery on `/activity`, "Load more".
- `dashboard/ActivityFeed.tsx` (the plan says AgentFeed.tsx; the existing file is ActivityFeed): keep `<details>` collapsed, the summary "Agent activity" and the text "Nothing from the agents yet." whenever there are no entries (locked T-P0-23-07 checks it synchronously after a click). Add a default `/v1/agents/feed` handler to `test/msw/dashboard.ts` `dashboardDefaults`.
- `review/Citation.tsx`: renders `tumnis://doc/<id>#page=<n>` as a link to `/v1/files/<id>#page=<n>` with the label; used by `ResultItem.tsx` for document links (never emit `href="tumnis://…"`).
- Add default MSW handlers for any new reads the project page makes on mount (`test/msw/project.ts`), or every locked project-page test fails (`onUnhandledFrame: "error"`).

## Remaining steps
1. Merge main, then implement in the plan's TDD order: 04, 05 -> 06 -> 01 -> 02, 03 -> 07-09, removing one marker at a time (Scott approved marker removal).
2. `make gen` (OpenAPI, tools.json, client). `make check` (set `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`, `SEMGREP_VERSION_CACHE_PATH` under `/tmp/claude-1002/P2-17-c0/`, because `~/.semgrep` is read-only). Run the unit layer with `-n 3`, then `make test` and `make test-int` (bare, once), and Vitest.
3. Push, write the PR body (deviations below; Context7 refs: SQLAlchemy 2.x `tuple_`, TanStack Query v5 `useInfiniteQuery`/`enabled`, pydantic 2 `model_validator(mode="after")`), run `gh pr create --base main --head wp/P2-17 --title "[P2-17] impl: inbox, activity, ask the agent and knowledge tools" --body-file ...`, then `gh pr comment <url> --body "@coderabbitai review"`, run the review loop, and send "#<PR> MERGE-READY at <sha>" to main.

## Deviations and Scott items (already messaged to main)
- `add_document` twin body keeps `body_md` and an optional `project_id`, with a 100_000 cap (plan: `body_markdown`, required, 500_000), because P1-17's locked tests post that shape to the twin path.
- `approval.auto` audit rows get no `project_id` (locked exact-details assert), so they're absent from Activity.
- Routes are on the agents/tasks routers, not projects (acyclic module graph).
- Citation links go to `/v1/files/{id}#page=n`, which serves `attachment`. "Opens the page" needs an inline PDF viewer or a disposition decision (Scott).
- `add_document` writes `agent-outputs/` from the api process through the storage port, the same as P1-17's key-written text entries. Scott: should it move to the worker?
- `ActivityFeed.tsx` is edited instead of the plan's `AgentFeed.tsx`.
