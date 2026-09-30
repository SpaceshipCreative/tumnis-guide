# HANDOFF: P2-01 (MCP server and REST twins), continuation 2

Stopped on "HANDOFF NOW" from the context watcher. **PR 1 is open: #81** (`[P2-01] impl: MCP server and REST twins`, branch `wp/P2-01`, pushed as `HEAD:wp/P2-01`).

## Commits on wp/P2-01 (after main)

- `9783b64` test(core): P2-01 spec tests (red)
- `1bbc1af` wip(core): agent surface, tasks ops (made with --no-verify; now covered: make check green since `0b634b2`)
- `0b634b2` feat(core): project context, search and task-token ops; a write is located before its version
- `8a7a53a` test(core): MCP spec tests green; parity normalizer and session-manager helper
- `f3e7051` merge of main (45a4f00)
- `815d40c` chore: remove the handoff notes
- `95ae7f2` fix(auth): semgrep nosemgrep on the task-token id filter
- `cceb711` fix(auth): a revoked task token has no run (CodeRabbit)
- (this commit) chore: P2-01 handoff

## State of PR #81

- CodeRabbit: 1 review, 2 threads, **both answered and resolved**. The `agent_surface._project` 404-for-missing-project suggestion was declined, with the reason given in the thread. The revoked-token filter was fixed in `cceb711`. CodeRabbit's review quota was used up ("used all 10 included reviews"), so a re-review may not come.
- CI on `815d40c`: every job green except `security`. That was semgrep `tumnis-secret-eq` on `auth/api.py`, fixed in `95ae7f2`. `integration`, `unit`, `contract`, `e2e` and `lint` all passed. `preview` stays pending, as expected.
- CI on `cceb711`: **`spec-guard` fails** on `frontend/e2e/journeys/J2.spec.ts :: A0.1 ... edited_test`. The cause is that main moved: decision 16's spec PR changed J2 on main. Our branch lacks that change, so the diff looks like an edit. **The fix is to merge main** (next step 1). No test was edited by us.

## Exact next steps

1. `/usr/bin/git fetch origin`, then `/usr/bin/git merge <origin/main or FETCH_HEAD sha>`. `origin/main` may fail to update ("unable to update local ref"); if so, merge the sha that fetch prints. Resolve conflicts keeping both sides. Generated files: `frontend/src/api/*.gen.ts` and `schemas/openapi.json`. Take theirs, then run `cd backend && uv run tumnis gen all --out ..`, then `cd frontend && npx --no-install openapi-ts && npx --no-install prettier --write src/api`. `frontend/src/lib/live-map.ts` must keep `"projectsGetProjectContext"` in the project details. Then `uv sync --frozen --all-extras`, `bash check.sh` (below), `npm --prefix frontend run typecheck`, and push `HEAD:wp/P2-01`.
2. Watch CI on #81 until green (`gh pr checks 81`). Comment `@coderabbitai review` once after the push. Resolve any new threads (`gh api graphql` `reviewThreads`, then reply and `resolveReviewThread`).
3. Update the PR body (`gh pr edit 81 --body-file ...`) with the final CI per job. The draft body is in `/tmp/claude-1002/P2-01-c1/pr-body.md`. Integration is green in CI, so replace the "third local run in progress" sentence.
4. **PR 2** on `wp/P2-01-impl-2`, branched from wp/P2-01. The isolated worktree cannot switch branches: commit on HEAD after #81 is clean and push `HEAD:wp/P2-01-impl-2`, or ask the coordinator. Contents:
   - `backend/tumnis/core/mcp_stdio.py`: `forward(lines: AsyncIterable[str], out: TextIO, http, *, key, url="/mcp")`. POST each non-empty line with `Authorization: Bearer <key>`, `Accept: application/json, text/event-stream` and `Content-Type: application/json`. A 202 or an empty body writes nothing. A JSON body is written compact as one line. An SSE body is written one line per `data:` event. A non-2xx HTTP answer to a request with an `id` becomes a JSON-RPC error line `{"jsonrpc":"2.0","id":id,"error":{"code":-32000,"message":...,"data":problem}}`.
   - `tumnis mcp-stdio` in `backend/tumnis/cli.py`. With no `TUMNIS_API_KEY`: `typer.echo(..., err=True)` and `raise typer.Exit(2)`, with nothing on stdout. The base URL comes from `TUMNIS_URL` (default `http://127.0.0.1:8000`). Read stdin with `asyncio.to_thread(sys.stdin.readline)`. **semgrep `tumnis-raw-httpx` forbids `httpx.AsyncClient(...)` outside `core/net.py`**: use `tumnis.core.net.guarded_client` with a NetPolicy that allows the configured host (check `NetPolicy`, `guarded_client` at `core/net.py:83` and `:256`), or put a justified `# nosemgrep` on it. Say which one in the PR.
   - T-P2-01-20 catalogue: in `backend/tumnis/gen.py` add a `mcp` part (`What` literal, `_parts`, `generate`, `check`), writing `schemas/mcp/v1/tools.json` from `tumnis.core.mcp_server.catalogue_json()`. Add `"mcp"` to `GenTarget` in `cli.py` (`all` includes it). Run `make gen` and commit the file.
   - README agents section (how to point Claude Code at `/mcp` with a scoped key, and the stdio shim).
   - Remove the markers of T-P2-01-12, -13 and -20 once each passes: `python3 /tmp/claude-1002/P2-01-c1/unmark.py <file> <test>`.
   - Open PR 2: `[P2-01] impl-2: MCP stdio shim and tool catalogue`, then comment `@coderabbitai review`, then run the review loop.
5. When everything is done, delete HANDOFF.md in a chore commit.

## Decisions and deviations (keep them in the PR bodies)

1. SDK: `mcp==1.30.0` (v1) low-level `Server`, with explicit schemas and `call_tool(validate_input=False)`. A refusal is `CallToolResult(isError=True, structuredContent=problem)`. No new dependency.
2. One `StreamableHTTPSessionManager` per app, entered in the lifespan through an `AsyncExitStack`. The test helper `tests/_mcp.py::mcp_running` runs it in its own task, because anyio cancel scopes must exit in the task that entered them. Without that, teardown errors turned passes into "XFAIL".
3. `handler(SurfaceCall, input)`; `project_resolver(ctx, raw)`.
4. REST twins keep the existing routes and operation IDs. New endpoints: `POST /v1/tasks/{task_id}/estimate` and `GET /v1/projects/{project_id}/context`. Writes answer `TaskWithLayoutOut` (additive `layout`). The plan's literal `CreateTaskIn` was not adopted: it would break P0-18 REST.
5. `invoke` order: scope, then project/row (for writes, always located, for every caller), then master_only, then schema_version, then idempotency_key, then validation. The reason is A0.3: polyfactory bodies carry a random `schema_version`, and a write at another workspace's row must be 404. Additions: the `projects` project lookup (`projects/api.py`) and `core.routing.project_lookup`.
6. Master key: the `register_caller_facts` seam (P2-02 fills it). A task token's run comes from `auth.api.task_token_run` (additive, revoked tokens excluded).
7. R-31: every API-key write with no run is tainted, on both doors. Checked: no existing test asserts `tainted == false` for a key-created task, and the integration job is green.
8. `_schema_norm.py`: resolves a nullable `$ref` and drops a null `default`.
9. The brief comes into the project context through `projects.register_brief_source`, which `knowledge/mcp.py` registers.

## Scott items

- FYI, not blocking: P2-08's planned `TaintSource.kind` literal lacks P2-01's `keyless_write` source. It is noted in the #81 body.

## Scratch (`/tmp/claude-1002/P2-01-c1/`)

`check.sh` (ruff, format, mypy, lint-imports, unit `-n 3`, daemon; prints CHECK-OK), `unmark.py`, `parity.py` (DB-free T-02/T-03 reproduction), `semgrep.sh <paths>`, `watch_pr.sh <PR>` (for Monitor), `threads.sh <PR>`, `reply.sh <PR> <comment_id> <thread_id> <body_file>`, `pr-body.md`.

## Verify

```
bash /tmp/claude-1002/P2-01-c1/check.sh
npm --prefix frontend run typecheck
make test-int    # bare, from the worktree root; the VM is loaded, so CI is the judge
gh pr checks 81
```
