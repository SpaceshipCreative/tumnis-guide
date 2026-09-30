# HANDOFF: P2-01 (MCP server and REST twins), after continuation c2

Stopped on "HANDOFF NOW" from the context watcher. This file sits on `wp/P2-01-impl-2`, which already deleted the c1 HANDOFF in 71c12aa. **Delete this file again in a chore commit before #84 is merge-ready.**

## PRs

| PR | Branch | Base | Tip |
| --- | --- | --- | --- |
| #81 `[P2-01] impl: MCP server and REST twins` | `wp/P2-01` | main | **0352a41** |
| #84 `[P2-01] impl-2: MCP stdio shim and tool catalogue` | `wp/P2-01-impl-2` | `wp/P2-01` | cd691c6 before this handoff commit |

#84 is stacked on #81. The tip of `wp/P2-01` is always an ancestor of `wp/P2-01-impl-2`.

## State at handoff

### #81 at 0352a41

- CI: every job green (lint, unit, contract, integration, e2e, security, daemon, performance, red-proof, skills, traceability, spec-guard, version-skew, GitGuardian, CodeRabbit). `preview` is pending, as expected.
- Threads: 0 unresolved.
  - HANDOFF.md SSRF thread: declined with reasons and resolved (operator_client, decision 23).
  - `update_estimate` log thread: fixed in 45acbf7 and resolved.
- **Merge-ready, except that the PR body still needs its final update** (step 3 below).

### #84 at cd691c6

- CI: all green except `integration`, which was still pending. `preview` is pending, as expected.
- The first thread (malformed 2xx body) is fixed in 2dad647 and resolved.
- **4 NEW unresolved CodeRabbit threads, all on `backend/tumnis/core/mcp_stdio.py`.** Their thread ids and first comment ids (from `threads.sh 84`):
  - PRRT_kwDOUx-vCc6nahLK 4141577795
  - PRRT_kwDOUx-vCc6nahLP 4141577805
  - PRRT_kwDOUx-vCc6nahLU 4141577813
  - PRRT_kwDOUx-vCc6nahLZ 4141577820

  Not yet read.

## Commits this continuation (c2)

### On `wp/P2-01`

- 93f6b30: main merge (5a61fca).
- 5ea328e: main merge (5ad1057, #75 P2-07).
  - Conflicts in `projects/api.py` and `tests/integration/__init__.py`; both sides kept.
- 45acbf7: `fix(tasks)`. `update_estimate` logs after the write (CodeRabbit).
- 78473cb: main merge (79909cc, #65 P0-25).
- 0ef91f8: `fix(tasks)`. The REST twin body `CreateTaskBody` is renamed back to **`TaskCreate`**.
  - #65's quick-add queue imports the generated `TaskCreate`. Frontend lint failed on 45acbf7 once #65 was merged.
  - Same fields, so the change stays additive. make gen regenerated openapi and frontend/src/api.
- 0352a41: main merge (2b04f58, #77 P1-12).
  - Conflicts in `projects/api.py` (P1-12's `project_links` kept) and `live-map.ts` (one comment naming both).
  - Generated files taken from main, then `make gen`.

The commits after 93f6b30 were built WITHOUT a checkout, because this isolated worktree can't switch branches. They were pushed as `<sha>:refs/heads/wp/P2-01` (fast-forwards, never forced). The scripts are in `/tmp/claude-1002/P2-01-c2/`:
- `make_pr1_merge.py` builds the merge commit: HEAD's tree with #84's diff reversed. Edit its PR1_TIP, PR2_TIP, MAIN and MSG constants first.
- `make_pr1_commit.py MSG PATHS...` builds a commit on origin/wp/P2-01 from the worktree versions of PATHS.

Each time, I checked that `git diff <new> HEAD` is byte-identical to #84's own diff.

### On `wp/P2-01-impl-2`

- dd9592a: the shim, `tumnis mcp-stdio`, operator_client, `gen mcp` producing tools.json, README, CHANGELOG, unit tests.
- d20d48e: unmarks T-12; README `--env` order.
- 71c12aa: deletes HANDOFF.md.
- 2dad647: malformed 2xx bodies get a JSON-RPC error (CodeRabbit); adds `_fail` and `_messages`.
- Merges of every `wp/P2-01` commit above, and of main.

## Exact next steps

1. Read the 4 new #84 threads (`gh api repos/SpaceshipCreative/tumnis-guide/pulls/comments/<id> --jq .body`). Fix each valid one in `mcp_stdio.py` with a unit test in `backend/tumnis/core/tests/unit/test_mcp_stdio.py`. Run `bash /tmp/claude-1002/P2-01-c2/check.sh`, commit on HEAD, and push `HEAD:wp/P2-01-impl-2`. Reply and resolve with `bash /tmp/claude-1002/P2-01-c2/reply.sh 84 <comment_id> <thread_id> <body_file>`.
2. `git rm HANDOFF.md` in a chore commit on the #84 line, then push.
3. PR bodies: the drafts are `/tmp/claude-1002/P2-01-c2/pr-body.md` (#81) and `pr2-body.md` (#84).
   - Replace the `CI_81` and `CI_84` placeholders with the final CI per job.
   - Add the #65 and #77 merges and the `TaskCreate` rename (0ef91f8) to #81's "Main merges" and "Review fixes" sections.
   - Then run `gh pr edit 81 --body-file …` and `gh pr edit 84 --body-file …`.
4. Before declaring merge-ready, run `git fetch origin` and `git merge-tree --write-tree --name-only <#81 tip> origin/main`. Main has moved four times this session. On a conflict, merge on HEAD, resolve keeping both sides, and regenerate (`git checkout --theirs` the generated files, then `make gen`). Then build #81's commit with `make_pr1_merge.py`.
5. Report `#81 MERGE-READY at <sha>`, then `#84 MERGE-READY at <sha>`, to "main" with SendMessage.
   - **Merge order:** #84's base is `wp/P2-01`, so #84 merges into `wp/P2-01` first, then #81 goes to main. The other way, #84 would need retargeting to main after #81 merges.

## Decisions and deviations (keep them in the PR bodies)

From c0 and c1:

1. SDK: `mcp==1.30.0` low-level `Server`, with explicit schemas and `call_tool(validate_input=False)`. A refusal is `CallToolResult(isError=True, structuredContent=problem)`.
2. One `StreamableHTTPSessionManager` per app, entered in the lifespan. Tests use `tests/_mcp.py::mcp_running` in their own task.
3. `handler(SurfaceCall, input)`; `project_resolver(ctx, raw)`.
4. REST twins keep the existing routes and operation IDs. New endpoints: `POST /v1/tasks/{id}/estimate` and `GET /v1/projects/{id}/context`. Writes answer `TaskWithLayoutOut` (additive `layout`). The create body's published name stays `TaskCreate` (0ef91f8).
5. `invoke` order: scope, then project/row, then master_only, then schema_version, then idempotency_key, then validation (A0.3).
6. Master key: the `register_caller_facts` seam. A task token's run comes from `auth.api.task_token_run`.
7. R-31: every API-key write with no run is tainted. No existing test changed.
8. `_schema_norm.py`: resolves a nullable `$ref` and drops a null default.
9. The brief comes into the context through `projects.register_brief_source`.

From c2:

10. The stdio shim uses `core.net.operator_client`, not `guarded_client`, because loopback is always blocked. **Approved by Scott, decision 23.**
11. The shim returns JSON-RPC errors for refused, unreachable, non-JSON or unparseable input, and echoes `MCP-Protocol-Version` and `Mcp-Session-Id`.
12. The shim's unit tests were written after the code; the spec tests were red first.
13. #81's commits were built via plumbing and pushed as `<sha>:wp/P2-01`, not the literal `HEAD:wp/P2-01`.

## Scott items

1. **The AGENTS.md entry for operator_client is NOT added.** Decision 23 says the P2-01 PR does it. The permission classifier denied the AGENTS.md edit as "Instruction Poisoning", and I did not work around it. Scott or the coordinator must add it. Suggested text is in the #84 body draft.
2. spec-guard stale base: decision 24 is fixing it in its own PR. #81 passes spec-guard now.
3. FYI: P2-08's `TaintSource.kind` lacks `keyless_write`.
4. I couldn't save `~/tumnis-coordinator/agent-reports/REPORT-P2-01-c2.md`: the harness blocks subagents from writing report files ("Subagents should return findings as text"). The report is in the final message instead.

## Spec tests

All 21 P2-01 spec tests are unmarked, each after it passed:
- #81: T-01 to T-11, T-14 to T-19, T-21.
- #84: T-12 (XPASS strict in `make test-int`), T-13 and T-20 (passed with `--runxfail`).

## Verify

```
bash /tmp/claude-1002/P2-01-c2/check.sh      # prints CHECK-OK
npm --prefix frontend run typecheck && npm --prefix frontend run lint
cd backend && uv run tumnis gen all --out .. --check
gh pr checks 81; gh pr checks 84
bash /tmp/claude-1002/P2-01-c2/threads.sh 81; bash /tmp/claude-1002/P2-01-c2/threads.sh 84
```
