# HANDOFF: P1-16 Upload safety and extraction (second handoff)

Stopped on the coordinator's "HANDOFF NOW" (context watcher, 358k). No PR is open yet. No
CodeRabbit threads, no CI runs (only the red commit has been pushed).

Start: worktree on a throwaway branch at main; `/usr/bin/git fetch origin`,
`/usr/bin/git merge origin/main` (no-op), `/usr/bin/git merge origin/wp/P1-16`; push only with
`/usr/bin/git push origin HEAD:wp/P1-16`. Read `~/tumnis-coordinator/vm-agent-rules.md`,
`scott-decisions.md` (10 and 11 are new, see below) and the original prompt
`~/tumnis-coordinator/prompts/P1-16.md`. Tool gotcha in this sandbox: heredocs and
`cd X && git ...` compounds are refused; use the Write/Edit tools for files, `sed -i` for
one-liners, and `/usr/bin/git` from the worktree root.

## Commits on `wp/P1-16` (after the first handoff `d4ea654`)

- `402220b` test(knowledge): P1-16 spec tests (red): every spec test T-01..T-14 except T-13
  (needs impl-2), strict xfail; stubs raise NotImplementedError.
- `9f34944` docs(agents): AGENTS.md records Scott decisions 10 and 11 (file serving from the
  api process; ClamAV connects to clamd without resolve_and_check). Done; do not redo.
- `97d2165` feat(knowledge): rules (classify_type, low_confidence_pages, chunk_pages,
  upload_file_name, numbered_name), markers off for T-04, T-09; `test_upload_file_name.py`.
- `f35c5ce` feat(knowledge): ClamAV INSTREAM client + FakeClamAV, markers off for T-05;
  `tests/integration/test_clamav_protocol.py` (scripted loopback server, runs without Docker).
- One more commit `chore: P1-16 handoff` (this file) also carries UNVERIFIED WIP: migration
  `knowledge_0004_extraction` and the `models.py` mirror (Document/DocumentVersion columns,
  `ExtractionArtifact`, `Chunk`). Nothing has applied the migration yet: check it first
  (`make test-int` builds the template DB from it; `chunk_tsv` is an IMMUTABLE SQL wrapper
  because `array_to_string` is only STABLE and a generated column needs immutable). If it is
  wrong, fix it before anything else: it breaks every integration test.

Green so far (local): rules unit tests (109 pass in `tumnis/modules/knowledge/tests/unit`),
`TestFakeClamAV` (contract), `test_clamav_protocol.py`. `make check` was clean before the
migration (ruff, format, mypy, lint-imports pass now too); the semgrep meta test fails locally
only (read-only `~/.semgrep`; set `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`,
`SEMGREP_VERSION_CACHE_PATH` under `$TMPDIR`).

## Still red (strict xfail; remove each marker only after the test passes)

T-01, T-02, T-03 (`test_upload_safety.py`), T-10 (`test_file_serving.py`), T-07, T-08, T-11,
T-12 (`test_extract_workflow.py`), T-14 (`tests/meta/test_compose.py`), T-06
(`test_extraction_set.py`, impl-2: needs real Docling + `*.expected.yaml`). T-13 waits for
impl-2: `handoff/P1-16/.../tests/contract/test_vision_contract.py` stays parked there.

## What exists (stubs marked S must be implemented)

- `knowledge/rules.py`: done.
- `knowledge/adapters/port.py`: `ScanResult`, `Scanner`, `Vision`, `ChunkRow`, `Conversion`,
  `Extractor` (done). `adapters/clamav.py` done. `adapters/fake.py`: `FakeClamAV` done,
  `FakeVision` and `FakeDocling` done (FakeDocling reads `backend/fixtures/extraction/
  <file>.conversion.json`, written for `rate-card-table.pdf` (3 chunks, table on page 2 under
  heading `Rates`) and `handwriting.pdf` (page 2 graded poor, 0 text items); unknown content
  becomes one chunk of decoded text). `adapters/__init__.py` registers only
  `knowledge.clamav` (vision and docling register in impl-2 with their contract suites).
  `adapters/docling.py` (S, `DoclingExtractor`, impl-2).
- `tumnis/settings.py`: `KnowledgeSettings`, `Settings.knowledge` (done).
- `knowledge/pipeline.py`: `STEPS`, `Ref`, `configure`, `use` real; every step body (S):
  `read, scan, quarantine, sniff, place, convert, vlm, store, chunk, index, emit, fail`.
  The workflow must call `pipeline.<step>(...)` by attribute at call time (the conftest
  step log and `_step_log.py` wrap them).
- `knowledge/api.py`: `UploadAccepted`, `begin_upload`, `ingest_folder_file` (S).
- `worker.py`: `main(..., queues=None)` accepts the new param but ignores it (S);
  `testing/run_worker.py --queues A,B` and `WorkerKiller(queues=...)` /
  `worker_killer(..., queues=...)` in `tests/fixtures/__init__.py` done (log names now
  `worker-<queues|main>-<n>.log`).
- Tests written: see the red list above plus helpers `_upload.py`, `_step_log.py`,
  `_queue_probe.py`, and `integration/conftest.py` (autouse `extract_dirs`, `extract_env`).

## Remaining work, in order

1. Verify/fix migration 0004 and models (see above).
2. `knowledge/store.py` (new, SQL for the pipeline: create document+version rows, statuses,
   hash/mime/kind/path setters, artifacts get/put upsert, `replace_chunks`, `mark_ready`
   which sets statuses + `current_version_id` and emits in one transaction, idempotent when
   the version is already `ready`). Event payloads `DocumentAddedV1`/`DocumentChangedV1` in
   `knowledge/events.py` (`document_id, version_id, version_no, project_id|None, title,
   trust, size`), fixtures `backend/tests/contract/fixtures/events/document.{added,changed}/
   v1.json`, then `make gen` (needs `npm ci --prefix frontend`; commit schemas/, openapi,
   frontend/src/api, generated contract tests).
3. Pipeline step bodies (design below), `workflows.py`: workflow `knowledge_extract_document`
   `(workspace_id: str, version_id: str, source: str)` on queue `extract`, workflow id
   `extract:<version_id>`, steps wrap `pipeline.*` with retries (clamd may be down: 5 attempts,
   5 s, backoff 2); `faults.killpoint("extract.vlm_step")` in the WORKFLOW body right before
   `vlm_step` (so the step log does not yet contain `vlm` at the kill); vlm failures after
   retries continue without the vision pass; any other final failure calls `fail` and returns
   "failed"; infected -> `quarantine`, refusal from `sniff` -> `fail(code)`.
4. `api.py`: `begin_upload`, `ingest_folder_file`, `enqueue_extract` (via
   `deadletter.dbos_client().enqueue_async({"queue_name": "extract", "workflow_name":
   "knowledge_extract_document", "workflow_id": f"extract:{version_id}", ...}, ws, vid,
   source)`), `get_document`, `project_of_document` (+ `register_project_lookup("knowledge",
   ...)` and a `LOOKUP_TARGETS["knowledge"]` entry in `backend/tests/meta/_authz.py`),
   `open_document_file` for serving, `configure_extraction(settings, net)` (imports
   `pipeline` lazily: pipeline imports api for `open_backend`).
5. Router: make `router = v1_router("knowledge", tags=["knowledge"])` unprefixed and prefix the
   existing paths with `/knowledge` (paths and operation ids unchanged); add
   `POST /v1/knowledge/documents` (202; multipart streamed with `python_multipart` into
   `<spool>/<version_id>` with a byte counter, 413 `too_large` the moment it passes
   `MAX_UPLOAD_BYTES`, sha256 while streaming, client Content-Type ignored, `session_or_key`,
   `knowledge:write`, `idempotent=False` reason: the idempotency layer buffers the body and a
   retry is a new document, `max_body_bytes=MAX_UPLOAD_BYTES + 1 MiB`, no `project_param`: the
   handler checks `principal.project_ids` and answers 404; needs the folder's location online
   (409 `location_offline`), 409 `no_location` without one; workspace KB uploads use folder
   root `workspace` on the default location), `GET /v1/knowledge/documents/{document_id}`
   (minimal read A1.5 polls; deviation), `GET /v1/files/{document_id}?version=` (`context:read`,
   `project_param="lookup:knowledge"`; attachment + `filename*=UTF-8''<pct-encoded>`,
   octet-stream, nosniff; 409 `not_available` unless `ready`; the 409 also carries the
   headers, check how the P0-16 header middleware treats problem responses).
6. Worker: `register_queues` adds `extract` (`worker_concurrency=1`);
   `main(queues=...)`: `DBOS.listen_queues(list(queues))` before launch with
   `executor_id="worker-extract"` in `dbos_config`; with no `queues`, `listen_queues` every
   queue except `extract` (explicit list); the extract worker runs no relay and applies no
   schedules; call `deadletter.configure(settings.dbos_system_url)` (or not needed in the
   worker) and `knowledge.api.configure_extraction(settings.knowledge, net=settings.net_policy())`.
   `tumnis worker --queues` flag in `cli.py`.
7. Compose (T-14): `clamd` service (digest in `backend/tests/_services.py`; ship
   `deploy/clamd/clamd.conf` raising StreamMaxLength to 60M, mount it), `worker-extract`
   (`["tumnis","worker","--queues","extract"]`, `mem_limit: 4g`, `spool` rw + `scratch` named
   volume, `KNOWLEDGE__CLAMD_HOST: clamd`, depends on clamd, migrate), `spool` volume also on
   the api; preview/test compose: fakes, gate clamd with a profile (`required: false`
   dependency). Dockerfile: `apt-get install libmagic1` (CI backend jobs too if libmagic is
   missing on the runner: check `.github/workflows`).
8. A1.5 (`tests/acceptance/_phase1.py`): `chunks_of` join `c.version_id` -> `c.document_version_id`
   via `documents.current_version_id`; wire `knowledge_app` (server-path default location +
   folders for the seed projects, real ClamAV on the `clamd` fixture; impl-2 adds real
   Docling). `test_eicar_upload_is_quarantined` (A1.5) belongs in PR 1 if `knowledge_app` is
   wired; `test_pdf_is_scanned_extracted_and_filed` is impl-2.
9. Remove each spec marker as its test passes; run `make check`, `make test`, `make test-int`
   (bare, from the worktree root; or rely on CI). Delete `handoff/` (`/usr/bin/git rm -r
   handoff`), decide whether to keep `handoff/P1-16/scripts/make_extraction_fixtures.py` (move
   to `scripts/fixtures/` if kept). Add a P1-16 row to the "Schema, files and names" list in
   Part A of `docs/IMPLEMENTATION-PLAN-DETAILED.md` (new shared names: settings, tables,
   ports, fakes, events, routes, codes `too_large`, `type_mismatch`, `type_not_allowed`,
   `not_available`, `no_location`).
10. Open the PR `[P1-16] impl: Upload safety and extraction` (body: summary, per-layer results,
    shared-file edits, deviations, "Decided by Scott: decisions 10 and 11", no open Scott items
    except the CI budget/Docling one for impl-2), comment `@coderabbitai review`, run the review
    loop. Then PR 2 on `wp/P1-16-impl-2`: T-06 (real Docling, expected files), T-13 (vision
    adapter + recordings + registration), A1.5 `test_pdf_is_scanned_extracted_and_filed`.
    Do NOT pull Docling/torch into the default CI install in PR 1 (coordinator instruction).
11. Finish: delete HANDOFF.md in a `chore:` commit.

## Pipeline design (implement as written unless it fails)

- Paths: spool `<spool_dir>/<version_id>`; scratch `<scratch_dir>/<version_id>`.
- `read`: copy spool -> scratch hashing (or, for `source="storage"`, the document's file via
  `open_backend` at `<folder root>/<documents.path>`, at most `MAX_UPLOAD_BYTES + 1` bytes,
  size from `stat`); writes hash/size to the rows; returns `Ref`. A helper re-creates scratch
  in later steps if it was lost (from the spool, else from the placed file).
- `scan`: stream scratch through the scanner; clean -> version and document `extracting`.
- `quarantine`: statuses `quarantined` (reason = signature), `audit.record(session,
  "upload.quarantined", target=("documents", doc_id), details={"signature","source"},
  occurred_at=SystemClock().now())` under `WorkspaceContext(ws, SYSTEM_ACTOR)`, remove spool
  and scratch; a folder file is never touched. Not added to `tests/audit_cases.py`.
- `sniff`: `magic.from_buffer(first 8 KiB, mime=True)`; `application/zip` refined by reading
  `[Content_Types].xml` (docx/xlsx/pptx); size > `MAX_UPLOAD_BYTES` -> `too_large`;
  `classify_type(mime, ref["name"])`; sets `mime` on the version and `kind` on the document;
  returns `{kind, mime, refusal}`.
- `place` (uploads): `uploads/<upload_file_name(name)>` under the folder, create-only,
  `numbered_name` on collision; a precondition failure whose file has the same sha256 counts
  as placed (retried step); sets `documents.path` (relative to the folder), deletes the spool.
- `convert`: `await asyncio.to_thread(extractor.convert, ...)`; artifacts `docling`,
  `markdown`; `low_pages = low_confidence_pages(...)` for `pdf` only.
- `vlm`: per low page, `extractor.page_image` -> `vision.page_markdown`; artifact
  `vlm_pages` (JSON `{page: markdown}`); no vision configured (real mode, PR 1): skip.
- `store`: gzip docling JSON + Markdown onto the version. `chunk`: `extractor.chunk`, then each
  vlm page replaces the chunks whose range is exactly that page (`chunk_markdown`, page
  range set, `extractor="vlm"`), ordinals renumbered; artifact `chunks`. `index`: replace the
  version's `chunks` rows from the artifact. `emit`: statuses `ready`, `current_version_id`,
  `document.changed` if the document already had a current version else `document.added`,
  one transaction, no-op when already `ready`; removes scratch. `fail`: statuses `failed`,
  reason = code; removes spool and scratch.
- Pipeline defaults: fakes mode (`TUMNIS_ADAPTERS=fake`) -> `FakeClamAV`, `FakeDocling`,
  `FakeVision` (constructed directly for docling/vision, registry for clamav); real mode ->
  `ClamAV(settings.clamd_host, port, clock=SystemClock())`; the real extractor and vision
  arrive with impl-2 (until then real mode raises a clear error in `convert`).
- The kill test worker (`TUMNIS_ADAPTERS=fake`) places into its own in-process
  `FakeStorage`; the test only checks rows and the step log.

## Decisions and deviations so far (report every one in the PR body)

- Workflow signature has `workspace_id` first (plan: `(version_id, source)`): steps need a
  workspace context for RLS (calendar workflows do the same). Workflow name
  `knowledge_extract_document`, id `extract:<version_id>`.
- Kill point `extract.vlm_step` sits in the workflow body before `vlm_step`.
- `GET /v1/knowledge/documents/{id}` added (minimal; P1-17 owns CRUD).
- Router made unprefixed with explicit `/knowledge` paths so `/v1/files/{id}` fits.
- Upload route `idempotent=False`, no `project_param` (reasons above).
- Migration `knowledge_0004`: `chunk_tsv` immutable wrapper instead of the plan's raw
  expression (generated columns need immutability); `document_versions.body_md` nullable;
  `chunks.document_version_id` (P1-17's SQL) while `extraction_artifacts.version_id` follows
  the plan.
- Upload-name rules are `upload_file_name`/`numbered_name` in `knowledge/rules.py`, not
  P1-15's `sanitize_filename`/`dedupe_name` (P1-15 is not merged; merge them when it lands).
- `handwriting.pdf` scenario T-08 runs in PR 1 (it uses only the vision fake); T-13 and the
  real Docling path are impl-2.
- Shared-file edits so far: `backend/tests/fixtures/__init__.py` (additive `queues`
  parameter, log file names), `backend/tumnis/testing/run_worker.py` (`--queues`),
  `backend/tumnis/worker.py`, `backend/tumnis/settings.py`, `AGENTS.md`,
  `backend/pyproject.toml` and `backend/uv.lock` (python-magic 0.4.27, python-multipart
  0.0.26, from the first handoff), `backend/tests/meta/test_compose.py` (new T-14 function
  only). Still to come: `deploy/compose.yaml`, `deploy/Dockerfile`, `.github` if libmagic is
  needed, `docs/IMPLEMENTATION-PLAN-DETAILED.md` Part A.

## Scott items

- Decided (cite in the PR body): decision 10 (api process reads storage to serve files) and
  decision 11 (ClamAV connects to clamd directly, isolated to `adapters/clamav.py`); AGENTS.md
  already records both.
- Open: Docling in CI for impl-2 (torch CPU wheels, prefetched models; may need the homelab
  runner if the integration budget breaks; decision 8 covers raising the budget by a
  spec-change PR only if speed-ups cannot keep it under 10 minutes).

## Verify commands

- `make check` (root); unit: `cd backend && uv run pytest -q -n 3 -m "not integration and not
  contract" tumnis/modules/knowledge`; the Docker-free integration test:
  `cd backend && uv run pytest -q -m integration tumnis/modules/knowledge/tests/integration/
  test_clamav_protocol.py`.
- Docker layers (bare, from the worktree root): `make test`, `make test-int`.
