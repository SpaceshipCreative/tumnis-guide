# HANDOFF: P1-17 (Knowledge items, editor, search and passages), continuation c0

Written on a "HANDOFF NOW" from the context watcher. No PR is open yet. Branch `wp/P1-17`
is pushed to origin (push with `/usr/bin/git push origin HEAD:wp/P1-17`). Scratch folder:
`$TMPDIR/P1-17-c0/`. `check.sh` there runs `make check` with semgrep's files moved under
`$TMPDIR`. Use `bash /tmp/claude-1002/P1-17-c0/check.sh`; copy it to a `P1-17-c1` folder
for the next continuation.

## Commits (on top of main 5d488a0)

| SHA | What |
| --- | --- |
| 6f5c228 | `test(knowledge): P1-17 spec tests (red)`: T-06..T-15 as strict xfail, plus interface stubs so mypy passes. skill_io `Passage.chunk_id` (optional) added and `make gen` run |
| ae7b944 | `feat(knowledge)`: rules `default_trust`, `select_passages`, `passage_query`, `markdown_sections` (+ `tests/unit/test_passage_query.py`). T-06, T-11, T-12 markers removed, green |
| bb0490d | `feat(knowledge)`: api + routes (see below). `make check` green. Integration markers still ON (not run yet) |
| (next) | `chore: P1-17 handoff` (this file) |

## Spec tests

| ID | File | State |
| --- | --- | --- |
| T-06 | knowledge/tests/unit/test_trust.py | green, marker removed |
| T-11, T-12 | knowledge/tests/unit/test_select_passages.py | green, markers removed |
| T-07, 08, 09 | knowledge/tests/integration/test_documents.py | xfail marker ON; code written, never run against Postgres |
| T-10 | knowledge/tests/integration/test_search.py | marker ON; code written |
| T-13 | knowledge/tests/integration/test_passages.py | marker ON; code written |
| T-14 | knowledge/tests/integration/test_quota.py | marker ON; code written |
| T-15 | agents/tests/integration/test_packet_builder.py | marker ON; **packet builder NOT implemented** (stubs only) |
| A1.5 steps 3, 4 + Playwright A1.5 | backend/tests/acceptance, frontend/e2e/acceptance | keep markers: they need real Docling in CI and an `Acme site` seed project (Scott items on #102; no seed data from us) |
| T-01..05, 16..18 (frontend) | not written | they go in the editor PR (`wp/P1-17-impl-2`) as its first red commit. `test.fails` can't sit red without Tiptap installed, because the import fails |

## What bb0490d built (knowledge.api, session-first like the rest of the module)

- `DocumentDTO` gained `label` (derived "agent" for agent-written untrusted rows, through a
  before-validator), `tags`, `source`, `status`, `status_reason`, `path`, `provider_url`,
  `current_version_id`. All are required in the schema; the frontend MSW `BriefStub` was updated to match.
  `GET /v1/knowledge/documents/{id}` now answers DocumentDTO (a superset of DocumentStatusOut).
- `create_text_entry(s, project_id, title, md, *, origin, net)`, `update_text_entry`,
  `edit_document` (PATCH: title/tags/pinned/body_md, versioned), `add_link`, `mark_trusted`
  (403 `human_only` unless the actor is `user:`; `audit.record("document.trust_changed")`; the
  case is added to `backend/tests/audit_cases.py`), `set_tags`, `set_pinned`, `trash`, `restore`,
  `purge_trash(s, cutoff, *, limit)` (hard delete with dependents), `list_documents` (Page),
  `search_knowledge(s, q, *, project_id, limit, mode="fts", project_ids)` -> `KnowledgeHit`
  (renamed from SearchHit, which clashed with the search module's OpenAPI schema), `passages_for`,
  `quota` (setting `knowledge.quota_bytes`, default 10 GiB).
- Each text write (`_write_text`): `add_version(status="ready")`, then `update_versioned` with
  `current_version_id`, then `_index_text` (store.replace_chunks from rules.markdown_sections),
  then `save_note(..., version_id=...)` when `net` is set and the project has a folder
  (`save_note` got an optional `version_id`), then `document.added`/`document.changed` is emitted.
- `project_of` (lookup:knowledge) now resolves trashed documents too, so restore works for keys.
- `store.new_document` uses `default_trust`.
- Routes (router.py): `GET /knowledge/documents?project_id`, `POST /knowledge/documents/text`
  (201), `POST /knowledge/documents/link` (201), `PATCH /knowledge/documents/{id}`,
  `DELETE /knowledge/documents/{id}` (204), `POST .../{id}/restore`, `POST .../{id}/trust`
  (auth=session), `GET .../{id}/versions`, `GET /knowledge/search?q&project_id&limit`,
  `GET /knowledge/quota?project_id`. frontend `src/lib/live-map.ts` maps the new GET ops.

## Exact next steps

1. Push, then let CI run the integration layer, or run `make test-int` ONCE (bare, from the
   worktree root). Then for T-07..T-10, T-13, T-14: remove each marker and watch it pass;
   fix what fails. Things to watch:
   - T-08: versions come out 1..3 (create = v1). The `notes/` folder_files row needs
     `net` passed (the routes pass `_net(request)`).
   - T-10: the table chunk must rank above the workspace entry. `two_workspaces` fills
     `chunks` with minimal rows; the search filters by current version, so those shouldn't match.
   - authz matrix / route registry / isolation sweep meta-tests on the new routes
     (`backend/tests/meta/test_authz_matrix.py`, `test_route_registry.py`, A0.3).
   - T-P0-15-07 audit case `document.trust_changed`.
2. T-15 packet builder (agents/packet_builder.py, keep it small and additive; P2-04/05/09 are active):
   - `enrichment_request`: `chosen = await knowledge.passages_for(s, task_id)`; brief = the
     first passage's text when its `chunk_id is None` (cut to BRIEF_MAX), else "";
     passages = the rest mapped to `skill_io.Passage(chunk_id, document_id, title[:500],
     heading_path[:12], page (None if <1), text[:8000])`, at most MAX_PASSAGES.
   - `gather_inputs`: fill `passages` (PassageInput) from passages_for (skip the brief one).
   - `plan_projects(s, ids, *, now)` -> `PlanProject(id, name, health, next_milestone,
     brief_excerpt=brief[:600])` via `projects.get_project(s, id, now=now)` + `knowledge.get_brief`.
   - `build_packet`: an ENRICH branch (needs task_id; `missing` = the task's empty fields among
     first_action / acceptance_criteria / estimate_minutes, else all three, since
     `enrichment_request` refuses an empty list); body = `enrichment_request(...).model_dump(mode="json")`,
     `prompt_text = render_prompt(ENRICH_SKILL, ENRICH_RESULT, body)` (see workflows.py:1709
     for the hand-built enrich packet and its constants), run_id defaults to
     `uuid5(task_id, PREVIEW_RUN)`, profile_id defaults to the project's first profile or UUID(int=0).
   - agents/mcp.py: add `"enrich"` to `PacketKind` (the default STAYS `"task"`, per T-P2-02-14);
     `_packet` routes kind=enrich to `build_packet(RunKind.ENRICH, ...)`.
   - Remove the T-15 marker once it passes in CI.
3. Open PR 1: `gh pr create --base main --head wp/P1-17 --title "[P1-17] impl: knowledge items,
   search and passages" --body-file $TMPDIR/P1-17-c1/pr-body.md`, then
   `gh pr comment <url> --body "@coderabbitai review"` once. Run the review loop
   (~/tumnis-coordinator/pr-review-loop.md). When CI is green with 0 threads, SendMessage
   to "main": "#<PR> MERGE-READY at <sha>".
4. Then the editor PR on `wp/P1-17-impl-2`: install pinned Tiptap 3 (Context7 first: Tiptap
   Markdown `@tiptap/markdown`, `@tiptap/extension-list`, Mention `suggestions`), write the
   frontend spec tests red (T-01..05, 16..18, fixtures under `frontend/src/test/fixtures/roundtrip/`),
   then Editor (lazy-loaded), BriefSection into the editor, KnowledgeSection, PacketPreview
   (drawer button "Packet preview", region "Packet preview", articles; passage citation
   "<title>, page N"). Keep the 200 KB initial JS budget.

## Decisions and deviations (for the PR body)

- Session-first api signatures (`create_text_entry(s, ...)`), like the rest of knowledge.api;
  the plan lists them without a session.
- Text entries are chunked by a pure Markdown heading splitter (`rules.markdown_sections`), not
  "the Docling Markdown path": Docling isn't on main (#102) and doesn't run in the api process.
  The chunks are marked `extractor='docling'` (the check constraint allows docling|vlm only).
- `skill_io.Passage.chunk_id` is optional (`UUID | None = None`), so the profiles hostile
  harness, which builds passages without one, still validates. A1.5 reads `chunk_id`, which we fill.
- `KnowledgeHit` instead of `SearchHit` (OpenAPI name clash with the search module).
- The planning request doesn't exist (P1-11 isn't merged), so the seam is `plan_projects` only.
- ts_headline uses `StartSel=**, StopSel=**` (Markdown bold; never HTML: PG docs say
  ts_headline output isn't XSS-safe).
- Quota: trashed items count until `purge_trash`. Text = UTF-8 body bytes; files = the size of
  the current version; links = 0.

## Scott items

1. **A1.5 step 4 vs T-P2-02-14 (both locked).** A1.5 calls `GET /v1/tasks/{id}/packet` with
   no `kind` and expects `kind == "enrich"` with the EnrichmentRequest body (`body.brief`
   string, `body.passages[].chunk_id/page`). T-P2-02-14 expects the same route with no
   `kind` to answer `task`. We add `kind=enrich` and keep the default `task`. Options: (a) A1.5
   passes `kind=enrich` (a spec change to A1.5), or (b) a session defaults to enrich. Needs
   Scott's choice.
2. A1.5 integration steps 1-4 and the Playwright A1.5 also need real Docling in CI and an
   `Acme site` seed project (already Scott items from #102).

## Docs consulted (cite in the PR body)

- PostgreSQL 18 text search: websearch_to_tsquery (the word "or" -> OR), ts_headline options and
  its XSS note, ts_rank_cd. Sources: postgresql.org/docs/18/textsearch-controls.html and
  functions-textsearch.html, via Context7 `/websites/postgresql_18`.
- SQLAlchemy 2.0 PostgreSQL dialect: `func.websearch_to_tsquery` / `func.ts_headline` (regconfig
  auto-cast), `bool_op("@@")`, via Context7 `/websites/sqlalchemy_en_20`.
- Hypothesis: `st.lists`, `st.one_of`, `@settings(max_examples, deadline)`, via Context7
  `/websites/hypothesis_readthedocs_io_en`.

## Verify commands

- `bash /tmp/claude-1002/P1-17-c0/check.sh` (make check; expect exit=0)
- `cd backend && uv run pytest -q -m "not integration and not contract" tumnis/modules/knowledge/tests/unit`
- `make test-int` (bare, once), or CI's integration job
