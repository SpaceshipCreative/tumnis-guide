# HANDOFF: P1-16 Upload safety and extraction

Stopped on the coordinator's "HANDOFF NOW" (context watcher) before the red spec commit.
No PR is open yet. No CodeRabbit threads, no CI runs.

## State of branch `wp/P1-16`

Base: `main` at e5e5d36 (P1-14 merged). One commit on top: `chore: P1-16 handoff` (this
file plus the pieces below). Nothing of P1-16 is implemented yet.

Committed in place (safe for `make check`):
- `backend/pyproject.toml`, `backend/uv.lock`: new pinned runtime deps `python-magic==0.4.27`
  (libmagic sniffing in the extract worker; ships type hints) and `python-multipart==0.0.26`
  (streaming multipart parser for the upload route). Shared-file edits: report them.
- `backend/fixtures/extraction/`: the fixture set, generated (see the script below):
  `text-2p.pdf`, `rate-card-table.pdf` (3 pages; page 2 heading "Rates" and a gridded table
  with the row `Senior designer | 160`), `two-column.pdf`, `scanned-1p.pdf` (image only, no
  text layer), `handwriting.pdf` (page 1 typed, page 2 scribble image), `brief.docx`,
  `budget.xlsx` (sheets Summary, Details), `kickoff.pptx` (3 slides), `notes.md`,
  `rates.csv`, `receipt.png`, `html-named.pdf` (HTML saved as .pdf). `file --mime-type`
  sniffs every one correctly (docx/xlsx/pptx as their OOXML types, notes.md as text/plain,
  rates.csv as text/csv). `eicar.txt` is never committed; it is built at test time.
  No `*.expected.yaml` yet (impl-2, once real Docling output can be checked).
- `backend/tumnis/modules/knowledge/tests/_samples.py`: `eicar()` (built from two halves),
  `fixture_bytes(name)`, `stream(data)`.

Parked (NOT in place: they import names that do not exist yet and would break collection,
the registry test T-P0-09-13 and every knowledge integration test). Move each back to the
same path under `backend/` when the matching stubs land:
- `handoff/P1-16/backend/tumnis/modules/knowledge/tests/unit/test_classify_type.py` (T-04)
- `handoff/P1-16/backend/tumnis/modules/knowledge/tests/unit/test_low_confidence_pages.py` (T-09, plus `test_chunk_pages`)
- `handoff/P1-16/backend/tumnis/modules/knowledge/tests/contract/test_clamav_contract.py` (T-05: `TestFakeClamAV`, `TestClamAV`)
- `handoff/P1-16/backend/tumnis/modules/knowledge/tests/contract/test_vision_contract.py` (T-13: `TestFakeVision`, `TestVllmVision`; recordings dir `tests/recordings/vllm_vision/`, none written yet)
- `handoff/P1-16/backend/tumnis/modules/knowledge/tests/integration/conftest.py`: replaces
  the current conftest (keeps `knowledge_ws`; adds autouse `extract_dirs` and
  `extract_env`, `log_steps`). Its autouse fixture imports `pipeline.configure` and
  `KnowledgeSettings`, so those must work for real (not stubs) in the red commit.
- `handoff/P1-16/scripts/make_extraction_fixtures.py`: the generator (needs reportlab,
  python-docx, openpyxl, python-pptx, pillow in a scratch venv:
  `uv venv $TMPDIR/gen/.venv && uv pip install --python $TMPDIR/gen/.venv/bin/python reportlab python-docx openpyxl python-pptx pillow`,
  then `$TMPDIR/gen/.venv/bin/python handoff/P1-16/scripts/make_extraction_fixtures.py backend/fixtures/extraction`).
  Decide whether to keep it in the repo (e.g. `scripts/fixtures/`) or drop it; delete
  `handoff/` before opening the PR.

## PR split (plan: spec + impl + impl-2 (Docling); prompt: spec+impl in one PR)

- PR 1 on `wp/P1-16` (`[P1-16] impl: Upload safety and extraction`): every spec test red
  first (T-01..T-14, commit `test(knowledge): P1-16 spec tests (red)`), then green TDD
  steps 1 to 5 (T-04, 09, 05, 03, 02, 01, 10, 07, 11, 12) plus step 8 (T-14 compose; small,
  and the main worker must stop listening to `extract` as soon as the queue exists).
  A1.5 `test_eicar_upload_is_quarantined` too if `knowledge_app` is wired (see below).
- PR 2 on `wp/P1-16-impl-2` from PR 1: T-06 (real Docling, `*.expected.yaml`), T-08, T-13
  (VLM), A1.5 `test_pdf_is_scanned_extracted_and_filed`, step 9 refactor.

## Remaining TDD steps (exact)

1. Stubs so the parked tests import and type-check (P1-14 precedent: "interfaces only",
   bodies `raise NotImplementedError`), then move the parked tests back, write the other
   spec tests below, run them red, commit red.
   - `rules.py`: `MAX_UPLOAD_BYTES = 50 * 1024 * 1024`, `ALLOWED_TYPES` (plan table),
     `DocKind = Literal["pdf","docx","xlsx","pptx","markdown","text","csv","html","image"]`,
     `@dataclass(frozen=True) class Refusal: code: Literal["type_not_allowed","type_mismatch","too_large"]`,
     `classify_type`, `low_confidence_pages(grades: Mapping[int, str | tuple[str, str]], text_items)`
     (a grade is one value or a (mean, low) pair; POOR in any case, or 0 / missing text
     items, selects; sorted), `chunk_pages`, `upload_file_name(name)` (basename only,
     `/ \ : * ? " < > |` and control chars -> `-`, keeps the extension, passes
     `safe_rel_path`; collisions `stem 2.ext`, `stem 3.ext`, matching P1-15's
     `names_sanitized` scenario).
   - `adapters/port.py`: `ScanResult(infected: bool, signature: str | None)`,
     `Scanner.scan(stream) -> ScanResult`; `Vision.page_markdown(image: bytes, *, page: int) -> str`
     and `health()`; the extractor port (`convert(path, kind) -> Conversion`,
     `chunk(doc_json) -> list[ChunkRow]`, `chunk_markdown(md) -> list[ChunkRow]`,
     `page_image(path, page) -> bytes`), `Conversion(doc_json: bytes, markdown: str,
     grades, text_items)`, `ChunkRow(ordinal, text, context_text, heading_path, page_from,
     page_to, extractor="docling")`.
   - `adapters/clamav.py` `ClamAV(Adapter)` name `knowledge.clamav`, ctor
     `(host, port, *, clock, policy=...)`; `adapters/vision.py` `VllmVision(Adapter)` name
     `knowledge.vision`, ctor `(base_url, model, *, clock, net_policy, resolver, transport)`;
     `adapters/fake.py` `FakeClamAV` (flags EICAR only, `calls`), `FakeVision`
     (`script(pages={n: md})`, `calls` = pages), `FakeDocling` (stored conversions keyed by
     sha256 of fixture files with a `<name>.conversion.json` sibling; unknown content -> one
     chunk of decoded text). Register `knowledge.clamav` and `knowledge.vision` in
     `adapters/__init__.py` (both need fake + real/recorded contract classes, which the
     parked contract tests provide). Do NOT register `knowledge.docling` in PR 1 (it would
     need a real contract class); PR 2 registers it with a docling contract suite.
   - `settings.py`: `KnowledgeSettings(BaseModel)` (`spool_dir="/var/lib/tumnis/spool"`,
     `scratch_dir="/scratch"`, `clamd_host="clamd"`, `clamd_port=3310`,
     `chunk_tokenizer="sentence-transformers/all-MiniLM-L6-v2"`, vision base_url/model for
     PR 2), `Settings.knowledge` (env `KNOWLEDGE__*`).
   - `knowledge/pipeline.py` (new; step bodies live here so a probe can wrap them):
     `STEPS = ("read","scan","quarantine","sniff","place","convert","vlm","store","chunk","index","emit","fail")`,
     `configure(KnowledgeSettings) -> previous`, `use(scanner=, extractor=, vision=) -> previous dict`.
2. Spec tests still to write (names from the plan table; my design in brackets):
   - `tests/integration/test_upload_safety.py`: T-01 `test_eicar_quarantined_never_extracted`
     [upload eicar via `session_client` + `dbos` + `extract_env`; 202 `pending_scan`; ends
     `quarantined`; no chunks; `extractor.calls == []`; nothing under the project folder
     (`open_backend` list); one `audit_log` row `upload.quarantined` targeting the document;
     spool file gone]; T-02 `test_type_sniffed_from_content[case]` [html-named.pdf as
     `invoice.pdf` with client Content-Type application/pdf -> failed `type_mismatch`, never
     placed or extracted; rate-card bytes as `rate-card.bin` -> failed `type_mismatch`;
     brief.docx -> ready, kind docx, path `uploads/brief.docx`]; T-03
     `test_51_mb_refused_50_mb_accepted` [hand-built multipart body from an async
     generator counting bytes; 51 MiB -> 413 `too_large`, no document row, spool empty,
     bytes consumed <= MAX + 1 MiB; exactly MAX -> 202; request `dbos` so the enqueue has
     system tables].
   - `tests/integration/test_file_serving.py`: T-10 `test_attachment_and_nosniff`
     [ready docx -> 200, `Content-Disposition: attachment; filename*=UTF-8''<pct basename>`,
     `application/octet-stream`, nosniff, P0-16 headers (reuse
     `tests.meta.test_headers_sweep.security_header_problems`), body == original bytes;
     `pending_scan`, `extracting`, `quarantined` -> 409 `not_available`, headers still set].
   - `tests/integration/test_extract_workflow.py`: T-07 kill test [`worker_killer("extract.vlm_step", events=0, imports=(step-log probe,), queues=("extract",))`,
     `enqueue_until_killed(queue_name="extract", workflow_name="knowledge_extract_document", args=(ws, version_id, "spool"))`
     -> 137 with log `read,scan,sniff,place,convert`; `restart_until_done` -> SUCCESS,
     full log each step once, one `document.added` in outbox, doc ready, chunks];
     T-08 (PR 2) VLM fake page 2 only; T-11 folder file [`api.ingest_folder_file` on a file
     written through `open_backend`; steps without `place`; path unchanged; tainted; an
     EICAR file in the folder ends quarantined and stays in the folder]; T-12 queue
     isolation [two harness workers on one system DB, probe workflow
     `knowledge_queue_probe` in `tests/integration/_queue_probe.py`; extract worker runs
     only the `extract` probe; main worker runs the others and leaves `extract` ENQUEUED].
     Probes: `tests/integration/_step_log.py` (wraps `pipeline.<step>` to append to
     `$KNOWLEDGE_STEP_LOG`), imported by the subprocess with `--import`.
   - `tests/integration/test_extraction_set.py`: T-06 `test_expected_chunks[fixture]`,
     explicit list of the 10 fixtures, `slow` + `integration`, tolerant matching per plan.
   - `backend/tests/meta/test_compose.py`: add T-14
     `test_worker_extract_has_memory_limit_and_clamd` (new function only; the file is
     locked for existing assertions).
3. Green, one marker at a time, in plan order.

## Design decisions (so far; keep or revise, but report every deviation)

- Tables (revision `knowledge_0004` after `knowledge_0003`, `phase = "expand"`):
  `documents` + `status` (pending_scan|extracting|ready|quarantined|failed, default
  'ready'), `status_reason`, `current_version_id`; `document_versions` + `status`,
  `status_reason`, `source_name`, `mime`, `docling_json bytea`; new tenant tables
  `extraction_artifacts(version_id, stage, data bytea)` unique (workspace_id, version_id,
  stage) and `chunks(document_id, document_version_id, ordinal, text, context_text,
  heading_path text[], page_from, page_to, extractor, tsv)` with `tsv` generated as
  `to_tsvector('english', coalesce(array_to_string(heading_path,' '),'') || ' ' || text)`
  and a GIN index (P1-17's search SQL uses `c.document_version_id`,
  `d.current_version_id`, `d.status`). Update `tests/acceptance/_phase1.py` `chunks_of`
  from `c.version_id` to `c.document_version_id` (helpers are not locked).
- Workflow `knowledge_extract_document(workspace_id, version_id, source)` on queue
  `extract`: deviation, the plan's signature has no workspace_id, but steps need a
  workspace context for RLS (calendar workflows take it the same way). Workflow id
  `extract:<version_id>`. Kill point `extract.vlm_step` at the start of the VLM step.
  Large outputs go to `extraction_artifacts`, never step return values.
- Upload route `POST /v1/knowledge/documents`: `session_or_key`, scope `knowledge:write`,
  `idempotent=False` (reason: the idempotency layer buffers the whole body to hash it,
  and a retry is a new document), `max_body_bytes=MAX_UPLOAD_BYTES + 1 MiB` (the
  middleware is a backstop; the handler's own counter answers 413 `too_large` first), no
  `project_param` (multipart body; the handler checks `principal.project_ids` itself and
  answers 404). Stream-parse with `python_multipart` into `spool/<tmp>`, rename to
  `spool/<version_id>` after the rows exist; commit, then enqueue with
  `deadletter.dbos_client()`; the client's Content-Type is ignored. Needs the project
  folder's location online (409 `location_offline`), 409 `no_location` without one.
  Router: make `router` unprefixed (`v1_router("knowledge", tags=["knowledge"])`) and
  prefix the existing paths with `/knowledge`, so `/files/{document_id}` fits the same
  router (paths and operation ids unchanged).
- `GET /v1/knowledge/documents/{document_id}` (deviation: minimal read, P1-17 owns CRUD, but
  A1.5 steps 1-2 poll it) and `GET /v1/files/{document_id}?version=`: `session_or_key`,
  `context:read`, `project_param="lookup:knowledge"` with
  `register_project_lookup("knowledge", project_of_document)` and a `LOOKUP_TARGETS`
  entry in `backend/tests/meta/_authz.py`. Serving reads storage in the api process:
  NOT covered by Scott's decision 1 (location save/test, folder checks, note writes):
  add it to the AGENTS.md exception and flag it for Scott.
- Placement: `uploads/<upload_file_name>` under the project folder (workspace KB uploads
  under a `workspace` folder on the default location), create-only; a precondition
  failure whose file already holds the same sha256 counts as placed (a retried step).
  `documents.path` is relative to the project folder (A1.5 expects
  `uploads/rate-card-table.pdf`). Spool deleted after placement or refusal.
- Sniff: libmagic on the first 8 KiB; `application/zip` refined to the OOXML type by
  reading `[Content_Types].xml` from the scratch copy; size > MAX refused `too_large`
  (folder files can exceed it). Refusals end `failed` with the code in `status_reason`.
- Quarantine: version+document `quarantined`, audit `upload.quarantined` (system actor,
  details: signature, source), spool/scratch removed; a folder file is never touched.
  Not added to `tests/audit_cases.py` (not a route action; SEC-3 does not list it).
- Worker: `tumnis worker --queues extract` -> `DBOS.listen_queues(["extract"])` before
  launch, `executor_id` `worker-extract` (DBOS recovers pending workflows per executor id;
  two workers sharing "local" would re-enqueue each other's running workflows); the extract
  worker runs no relay and applies no schedules. The main worker listens to every queue
  but `extract` (explicit list in `worker.py`). Register `extract` with
  `worker_concurrency=1`. `worker.main` also calls `deadletter.configure(settings.dbos_system_url)`
  and `knowledge.api.configure_extraction(settings.knowledge, net=...)`.
  `WorkerKiller` / `worker_killer` get an additive `queues=()` parameter and
  `tumnis.testing.run_worker` a `--queues` flag.
- ClamAV is operator-configured infrastructure (like Postgres): it connects to
  `KNOWLEDGE__CLAMD_HOST:PORT` directly, not through `resolve_and_check` (tests reach it on
  loopback, which the guard always blocks). Flag for Scott. clamd's default
  StreamMaxLength (25M) is below 50 MiB: ship `deploy/clamd/clamd.conf` (or env) raising it
  to 60M; an over-limit reply is a scan failure, never "clean".
- Compose: `clamd` (the digest in `tests/_services.py`), `worker-extract`
  (`["tumnis","worker","--queues","extract"]`, `mem_limit: 4g`, spool rw + `scratch`
  named volume, depends on clamd), `spool` volume also on the api; preview/test keep
  fakes, so gate clamd with a profile there and use `required: false` on the dependency.
  Dockerfile: `apt-get install libmagic1`.
- VLM (PR 2): our own `VllmVision` adapter over `guarded_client` (page PNG in, Markdown
  out), not Docling's `ApiVlmEngineOptions` (Docling would make the HTTP call itself,
  outside `tumnis.core.net`): deviation to report.
- A1.5 `knowledge_app` helper: in fakes mode every location is a `FakeStorage`, so wire a
  server-path default location plus folders for the seed projects, the real `ClamAV` on
  the `clamd` fixture, and (PR 2) the real Docling extractor. P1-14's plan note says the
  seed has a default location `homelab-minio`; P1-14 did not add it.

## Scott items (so far)

- File serving reads storage from the api process (plan requires it); needs the AGENTS.md
  exception widened.
- ClamAV connects outside `resolve_and_check` (operator-configured, internal service).
- Docling in CI (PR 2): torch CPU wheels and prefetched models; may need the homelab
  runner if the integration budget breaks (plan's own fallback).

## Verify commands

- `make check` (root); unit: `cd backend && uv run pytest -q -n 3 -m "not integration and not contract" tumnis/modules/knowledge`
- Docker layers (bare, from the worktree root): `make test`, `make test-int`
- Before the PR: `rm -r handoff/`, regenerate nothing else.
