# HANDOFF: P1-17 (Knowledge items, editor, search and passages), continuation c1

Written on a "HANDOFF NOW" from the context watcher. **PR #122 is open**:
https://github.com/SpaceshipCreative/tumnis-guide/pull/122 ("[P1-17] impl: knowledge items,
search and passages"). Branch `wp/P1-17`; push with `/usr/bin/git push origin HEAD:wp/P1-17`
(from a throwaway worktree branch: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/wp/P1-17`).
Scratch folder used: `$TMPDIR/P1-17-c1/` (`check.sh` there runs `make check` with semgrep's
files under $TMPDIR; edit its `cd` line to your worktree; copy it to `P1-17-c2`).
`pr-body.md` there is the PR body as opened.

## Commits on wp/P1-17 (on top of main 5d488a0)

| SHA | What |
| --- | --- |
| 6f5c228 | `test(knowledge)`: P1-17 spec tests (red) |
| ae7b944 | `feat(knowledge)`: trust defaults, passage selection and query rules |
| bb0490d | `feat(knowledge)`: items, versions, trust, search, passages, quota (api + routes) |
| f234181 / 4da8769 | c0 handoff, then removed |
| b30b0ed | `feat(agents)`: enrich packets carry brief + passages; `enrich_packet`, `build_packet(ENRICH)`, `plan_projects`, `?kind=enrich`; gather_inputs passages |
| 865f386 | T-07, 08, 09, 13, 14 markers removed (passed in local `make test-int`) |
| 2523632 | T-15 marker removed (passed locally) |
| 16a8052 | quota 404 for a project the caller cannot see (A0.3 found a leak) |
| c9820fc | `{document_id:uuid}` convertor: **superseded by a66b816** (it broke locked T-P0-11-10) |
| 03859be | T-10 makes its other workspace with `make_workspace` (session_client signs in to B under `two_workspaces`); assertions unchanged |
| a66b816 | 405 + `Allow: POST` for GET/PATCH/DELETE on `/knowledge/documents/{text,link}` through a `_KnowledgeRoute(TumnisRoute)` subclass; convertor and `_isolation.py` edit reverted; `core/errors.py` keeps an explicit `Allow` on a 405 |
| 1d8664d | T-10 marker removed (XPASS(strict) in CI run 36806117046) |
| 2e34964 | CodeRabbit round 1 fixes (all 5 comments), see below. **Pushed together with this handoff; CI not yet seen on it** |

No alembic migration in this PR.

## CodeRabbit round 1 (review on 03859be): 5 inline threads, all fixed in 2e34964, NOT yet replied to or resolved

| Comment id | File | Fix |
| --- | --- | --- |
| 4151284238 | agents/packet_builder.py | taint through enrichment: `enrichment_request_tainted` (task tainted or a used brief/passage tainted); enrich `TaskPacket.tainted`; workflow `enrich_request_step` returns `tainted`, packet carries it, `enrich_apply_step(..., *, tainted=False)` calls `tasks.raise_taint`. Test: `agents/tests/integration/test_enrich_packet_taint.py` |
| 4151284242 | knowledge/api.py `_dto` | `body_md` is None for a non-note document that is not `ready` |
| 4151284249 | knowledge/api.py `_write_text` | `update_versioned` first, then `add_version`, then set `current_version_id` |
| 4151284251 | knowledge/router.py | `_origin(request)`: session -> `user_text`, otherwise `agent`; POST text and PATCH pass it; `update_text_entry`/`edit_document` take `origin`, an agent edit sets `trust="untrusted"`. Test: `knowledge/tests/integration/test_text_trust.py` |
| 4151284264 | knowledge/rules.py | CommonMark fence tracking (`_fence_after`); unit test `test_headings_inside_code_fences_stay_text` |

Next: reply in each thread with the fix SHA (2e34964), resolve each with the GraphQL
`resolveReviewThread` mutation, then comment `@coderabbitai review` once.

## CI state (run 36806117046 on 03859be, before a66b816/1d8664d/2e34964)

- integration: only T-10 XPASS(strict) (fixed in 1d8664d); everything else passed, serial part too.
- contract: T-P0-11-10 KeyError from the convertor (fixed in a66b816; the test passes locally:
  `cd backend && uv run pytest -q tests/contract/test_generation.py -k operation_ids`).
- security: fails on every PR (Trivy, libpcre2-8-0 CVE in the base image). Coordinator: a separate
  agent fixes it on `fix/trivy-pcre2`; **don't touch the Dockerfile or .trivyignore**; merge main
  when the coordinator says so and re-run CI.
- performance: Lighthouse `total-blocking-time` 217 > 200 on the board page. main's own run
  36799478425 (5d488a0) fails the performance job too: pre-existing. Confirm it is the same
  TBT assertion (`gh api --allow-escape-sequences repos/SpaceshipCreative/tumnis-guide/actions/jobs/<job>/logs`,
  needs `*.blob.core.windows.net` in allowed_domains) and note it in the PR; don't chase it.
- lint, unit, e2e, skills, daemon, traceability, spec-guard, red-proof, version-skew, GitGuardian: pass.
- preview: pending (no homelab runner), expected.

## Exact next steps

1. Watch CI on 2e34964 (`gh pr checks 122`). Expect green apart from security (Trivy) and
   performance (pre-existing). New tests to watch: `test_text_trust_follows_the_caller`,
   `test_tainted_passage_taints_the_enrich_packet`, plus P1-08's `test_enrich.py` (the apply
   step gained a keyword `tainted`) and the A0.3 sweep.
2. Reply to and resolve the 5 CodeRabbit threads (above), then `@coderabbitai review` once.
3. Update the PR body (`gh pr edit 122 --body-file ...`, start from `$TMPDIR/P1-17-c1/pr-body.md`):
   - T-10 now green, marker removed; all P1-17 backend spec tests green.
   - Deviation 8: replace the `{document_id:uuid}` text with: a `_KnowledgeRoute` subclass answers
     405 with `Allow: POST` (RFC 9110 15.5.6) for GET/PATCH/DELETE on `/knowledge/documents/text|link`;
     route paths and OpenAPI unchanged (the convertor broke locked T-P0-11-10). Drop the P2-17
     `_PATH_PARAM` heads-up. Shared-file edits: remove the `_isolation.py` line (reverted), add
     `backend/tumnis/core/errors.py` (a 405 that carries `Allow` keeps it).
   - Add the CodeRabbit fixes: text trust by caller, enrich taint (workflows.py `enrich_request_step`
     and `enrich_apply_step` changed: shared agents file), version-order fix, body_md masking, fences.
   - Add the performance note (pre-existing on main, run 36799478425).
   - Docs: add RFC 9110 section 15.5.6 (405 must send Allow) and keep the Starlette routing citation.
4. When the coordinator says the Trivy fix merged: `/usr/bin/git fetch origin`, `/usr/bin/git merge origin/main`,
   `make gen` if generated files conflict, `make check`, push, re-run CI.
5. CI green (except Trivy until merged) and 0 open threads: SendMessage to "main":
   "#122 MERGE-READY at <sha>".
6. Then PR 2 (editor) on `wp/P1-17-impl-2` from wp/P1-17: Context7 first (Tiptap 3 `@tiptap/markdown`,
   `@tiptap/extension-list`, Mention `suggestions`, link sanitising); frontend spec tests red
   (T-01..05, 16..18; fixtures under `frontend/src/test/fixtures/roundtrip/`), then the lazy-loaded
   Editor, BriefSection into the editor, KnowledgeSection, PacketPreview; 200 KB initial-JS budget.

## Decisions and deviations (for the PR body; carried from c0, plus c1)

1. Session-first api signatures (`create_text_entry(s, ...)`).
2. Text entries are chunked by `rules.markdown_sections` (not Docling: not on main, not in the api
   process); chunks stored with `extractor='docling'` (check constraint allows docling|vlm only).
3. `skill_io.Passage.chunk_id` optional (`UUID | None = None`).
4. `KnowledgeHit` instead of `SearchHit` (OpenAPI name clash).
5. Planning: P1-11 not merged, so the seam is `plan_projects` only.
6. ts_headline uses `StartSel=**, StopSel=**` (never HTML; PG docs: not XSS-safe).
7. Quota: trash counts until `purge_trash`; text = UTF-8 body bytes; files = current version size; links = 0.
8. Enrich brief now from `select_passages` (cut at cap // 2 = 3,000 chars with `[brief truncated]`;
   was BRIEF_MAX 8,000).
9. `enrich_task` still builds its packet in its own step from `enrichment_request_tainted`;
   `ENRICH_SKILL`, `ENRICH_RESULT`, `ENRICH_TIMEOUT_S_DEFAULT`, `task_snapshot` moved to packet_builder.
10. 405 on literal document paths via the route subclass (see step 3).
11. T-10 fixture wiring (`make_workspace` instead of `two_workspaces`), assertions unchanged.
12. CodeRabbit round 1 fixes as listed above (beyond the plan: text trust by caller, enrich taint).

## Scott items

1. **A1.5 step 4 vs T-P2-02-14 (both locked).** A1.5 calls `GET /v1/tasks/{id}/packet` with no `kind`
   and expects `kind == "enrich"`; T-P2-02-14 expects the default `task`. We add `kind=enrich` and keep
   the default `task`. Options: (a) A1.5 passes `kind=enrich`, or (b) a session defaults to enrich.
   A1.5's markers stay on.
2. A1.5 integration steps 1-4 and Playwright A1.5 also need real Docling in CI and an `Acme site`
   seed project (Scott items on #102; no seed data from us).

## Docs consulted (cite in the PR body)

- PostgreSQL 18 text search (websearch_to_tsquery, ts_headline and its safety note, ts_rank_cd):
  postgresql.org/docs/18/textsearch-controls.html, functions-textsearch.html (Context7 `/websites/postgresql_18`).
- SQLAlchemy 2.0 PostgreSQL functions, `bool_op("@@")` (Context7 `/websites/sqlalchemy_en_20`).
- Hypothesis strategies and settings (Context7 `/websites/hypothesis_readthedocs_io_en`).
- Starlette routing: path convertors and 405 for a path matched with another method
  (github.com/kludex/starlette docs/routing.md, Context7 `/kludex/starlette`).
- RFC 9110 section 15.5.6: a 405 response must carry `Allow` (cite; not yet looked up on the web).

## Verify commands

- `bash $TMPDIR/P1-17-c2/check.sh` (make check; expect exit=0)
- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/knowledge tumnis/modules/agents`
- `cd backend && uv run pytest -q tests/contract/test_generation.py -k operation_ids`
- `cd backend && uv run python $TMPDIR/P1-17-c1/probe.py` (PATCH/GET/DELETE on /documents/text -> 405, Allow: POST)
- Integration: CI (`gh pr checks 122`); the local `make test-int` budget for c1 was used.
