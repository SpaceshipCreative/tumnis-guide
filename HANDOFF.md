# P2-17 handoff (c2 -> c3)

PR: **#132** https://github.com/SpaceshipCreative/tumnis-guide/pull/132 (`wp/P2-17`), with the `spec-change` label (the coordinator added it for decision 46).
Push only with `/usr/bin/git push origin HEAD:wp/P2-17`. Setup: `/usr/bin/git fetch origin; /usr/bin/git merge origin/main; /usr/bin/git merge origin/wp/P2-17`, then `make gen` after any merge (never hand-merge `frontend/src/api/*`, `schemas/*`).
Scratch from c2: `/tmp/claude-1002/P2-17-c2/` (`pr-body.md`, `int.clean.log` = CI run 36829059154 integration log, `check_link.py`, `resolve_both.py`). Static parity: `cd backend && uv run python /tmp/claude-1002/P2-17-c1/parity.py` should print `bad 0`, and it does at 005ad04.

## Coordinator notes (binding standing note (d), keep for every continuation)
- Scott decision 46: done in c15e775 `test(agents): approval.auto audit rows carry project_id (Scott decision 46)`, a spec change to the locked `agents/tests/integration/test_default_policy.py`. Plus code in human.py and human_flows.py. Listed in the PR body under "Spec changes". Don't add the label yourself.
- Scott decision 47: done. `GET /v1/files/{id}` serves a sniffed PDF (version `mime` = application/pdf) inline as `application/pdf`, keeping nosniff and the security headers. Test: `knowledge/tests/integration/test_file_inline_pdf.py` (XPASSed in CI, now unmarked). No locked test pins attachment for PDFs (T-P1-16-10 uses .docx).
- Scott decision 48: add_document keeps writing from the API (no change).
- The planned deviations from c0 stand (body_md shape, routes on the agents/tasks routers, search as a page, WORKSPACE_ROW, the AgentRail pause slot, ActivityFeed.tsx).

## Commits of c2 (on top of c1's 3893a8e / 8f24ec9)
- c15e775 test(agents): decision 46 spec change
- 68089ea test(knowledge): inline PDF test (red)
- 47fc4ee feat(agents): frontend (Composer ask toggle, AgentRail, Inbox/Activity views, ActivityFeed, Citation), decision 46 and 47 code, live-map, MSW defaults; T-P2-17-07..09 unmarked
- prettier commit, merges of main (f07adbc, 71e479f, 005ad04)
- 5af9714 test: unmark T-P2-17-01..06 + inline PDF (all XPASS(strict) in CI run 36829059154)
- 90a9ff5 fix(tasks): ResultLink is now a RootModel over WebResultLink | DocumentResultLink (005ad04 made it a plain anyOf, no discriminator, so the MCP/REST parity holds). Fixes A0.3 and the authz-matrix POST /runs/{id}/result failures: polyfactory made tumnis:// + kind=pull_request.
- 97e2f5e fix(knowledge): `passages_for` excludes agent-written documents (`search_knowledge(..., agent_written=False)`). Fixes T-P2-08-06 `test_add_document_from_tainted_run_is_tainted` and the T-P2-08 hypothesis taint graph (`run_doc`): agent docs are now indexed (T-P2-17-04 needs "searchable at once"), so a tainted agent doc fed the next clean run's packet.
- 1822d97 test(meta): sweep anonymous bucket. Superseded by main's identical fix (#103); the merge took main's conftest.
- 5079f15 perf(core): core_0010_audit_project_index (partial index on audit_log for Activity). Queries add `details ? 'project_id'`. Fixes CodeRabbit thread 4152792173.
- 6b64de4 chore: removed the old HANDOFF.md (fixes CodeRabbit thread 4152792181, stale approval.auto notes). This file re-adds a fresh handoff; delete it again when done.

## State at handoff
- Local: ruff, mypy, lint-imports, backend unit (1753 passed), Vitest (114 files, 256 tests), typecheck, eslint/prettier: all green at 005ad04 (before this handoff commit). Alembic heads: core_0010_audit_project_index, agents_0007 (main), single per module.
- CI on the pushed head before c2's fixes (f07adbc): everything green except `integration` (13 failures: 8 were XPASS, fixed by unmarking; the other 5 are fixed by the commits above). The new head has **not had CI yet**.
- CodeRabbit: 2 threads (audit index, HANDOFF stale notes), both addressed in code but **not yet replied to or resolved** on GitHub. Reply on each with the fixing SHA (5079f15, 6b64de4) and resolve them.
- GitHub API rate limit was exhausted at ~07:15Z: check `gh api rate_limit --jq .resources.core` before polling, and poll at most every 60 s.

## Next steps
1. Fetch, merge main, run `make gen`, push.
2. Watch CI (`gh pr checks 132`). If `integration` fails, read `gh run view <run> --job <id> --log-failed` (needs `allowed_domains: productionresultssa8.blob.core.windows.net`). Watch especially: T-P2-08-06 and the taint graph test (passages exclusion); A0.3 and the authz matrix (ResultLink); the T-P2-01 parity tests; T-P2-17-01..06; and migrate up/down with core_0010.
3. Reply to and resolve the 2 CodeRabbit threads (see above). Don't request a new full review.
4. Update the PR body (`/tmp/claude-1002/P2-17-c2/pr-body.md`, `gh pr edit 132 --body-file ...`). Add: the markers removed after CI XPASS; the core_0010 migration; the ResultLink schema change (WebResultLink/DocumentResultLink anyOf); the passages exclusion (deviation + Scott item below); integration results.
5. When CI is green and no threads are open: delete HANDOFF.md (`chore:` commit), push, and send main "#132 MERGE-READY at <sha>".

## Scott items (send to main if not yet sent; c2 did not send them yet)
- **Agent outputs and packets**: P2-17 indexes agent-written documents (T-P2-17-04: searchable at once). Without a change, a tainted agent document would flow into the next clean run's packet through `passages_for`, which breaks the locked P2-08 tests (T-P2-08-06 and the taint-graph property test). The build-around keeps agent-written documents (`source='agent'`) out of packet passages; agents still find them with `search_knowledge`. Options: (a) keep the exclusion (current); (b) spec-change the P2-08 tests to accept knowledge-path taint propagation and let agent outputs feed packets.
- Decision 47 caveat: the PDF response keeps the global CSP (`object-src 'none'`). Browsers' built-in PDF viewers open top-level PDFs, but no CI browser can check it, so a one-time manual check in Scott's browser is worth doing.
- The earlier c0 deviations are listed in the PR body.
