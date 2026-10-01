# HANDOFF: P3-10 (Embeddings and hybrid search), continuation c1 -> c2

Branch `wp/P3-10` (push with `/usr/bin/git push origin HEAD:wp/P3-10`). No PR yet.
Title when opened: `[P3-10] impl: embeddings and hybrid search`.

## c1 progress (read first; the c0 sections below are kept for the details)

Done in c1 (make check green at each):
- `a297171` rules: RRF_K, CANDIDATES, EMBED_BATCH, EMBED_QUEUE, REEMBED_WORKFLOW,
  DEFAULT_EMBEDDING_MODEL/DIMS, COSINE_OPCLASS/OPERATOR, Ranked, FusedHit, rrf_merge,
  recall_at_k, valid_model_name, model_slug, hnsw_index_name. T-01..03 markers removed.
- `cb757df` decisions adapters `adapters/embeddings/{port,vllm,hosted,fake}.py`
  (`FakeEmbeddings`, `FakeHostedEmbeddings`), registry names `decisions.embeddings_vllm` /
  `decisions.embeddings_hosted`, contract classes in
  `decisions/tests/contract/test_embeddings_contract.py`, recording
  `decisions/tests/recordings/vllm_embeddings/bge_m3__batch_order.json`,
  `openai_compat.ChatEndpoint(path=, bearer=)`. T-11 marker removed. Eval vectors RECORDED
  (`backend/fixtures/search_eval/vectors/*.jsonl` + `README`; vector-only recall@5 = 1.0)
  with `backend/scripts/record_eval_vectors.py --onnx-dir`. Shared-file edits:
  `.importlinter` (two modules added to `api-never-calls-out`; ignore
  `knowledge.tests.** -> decisions.adapters.embeddings.fake` in modules-api-only).
- `ca6daab` migration `knowledge_0007_embeddings_hnsw.py` (DO-block extension assert so
  offline rendering works), models `Vector` (UserDefinedType, bind as text cast to vector),
  `EmbeddingModel`, `Embedding`, `vector_literal`; `decisions/embeddings_slot.py`
  (Embedders, use_embedders, configure_embeddings, embedders_for, embed(texts, project_id,
  model=), query_timeout_s, workspace setting section `embeddings` {local_only}) re-exported
  from decisions.api; `Settings.embeddings` (EmbeddingsSettings base_url/model/dims/
  query_timeout_ms). Slot is OFF unless EMBEDDINGS__BASE_URL is set (fake mode too), so the
  locked P2-02 golden packets are untouched.
- All `# type: ignore` comments on now-existing names were removed; the remaining ones are
  on `knowledge.embed_document`, `embedding_models`, `create_embedding_index`,
  `set_embedding_model`, `start_reembed`, `knowledge.search` import, and the `mode=mode`
  `arg-type` ones (those stay: `mode` is a `str`).

Next (c1's plan, in order):
1. `knowledge/embeddings.py`: `embed_document(ctx, document_id, version_id=None) -> int`
   (inside `tenancy.use_workspace(ctx)`; embed `chunks.context_text`; target = first
   allowed embedder (decisions.embedders_for) whose embedding_models row is `active`, else
   first allowed with NO row (insert `active`, index_name from pg_indexes), else 0; batches
   of EMBED_BATCH; INSERT ... ON CONFLICT DO NOTHING; project_id from the document),
   `search_model(project_id)` (first allowed active), `embedding_models(s)`,
   `set_embedding_model(s, model, dims, provider) -> ReembedRequest(model, replaces)` (row
   `building`, index_name from pg_indexes NOW: T-07/08 create the index before the row),
   `start_reembed(ctx, request, *, client=None)` (id `reembed:<ws>:<model>`; no client: a
   hook registered by workflows.py that does DBOS.enqueue_workflow_async in a fresh
   contextvars.Context, like `enqueue_folder_extraction`), `create_embedding_index(owner_url,
   model, dims)` (sync psycopg; convert `postgresql+psycopg://` with
   `sqlalchemy.engine.make_url(..).set(drivername="postgresql")`; CREATE INDEX IF NOT EXISTS
   with literals; UPDATE embedding_models SET index_name for every workspace). Re-export from
   knowledge.api.
2. `knowledge/search.py`: `vector_query(model, dims, vector, *, project_id, project_ids,
   candidates)` with model/dims as LITERALS (valid_model_name, quotes doubled) and only
   `CAST(:q AS vector)` bound; ORDER BY `(e.embedding::vector(<dims>)) <=> CAST(:q AS vector)`;
   `tune_session(s)` (SET LOCAL hnsw.ef_search = 100; SET LOCAL hnsw.iterative_scan =
   relaxed_order); `hybrid(...)`: FTS top CANDIDATES via search_knowledge(mode="fts"); query
   vector via decisions.embed(model=search model) under asyncio.timeout(query_timeout_s),
   any AdapterError/TimeoutError/skip -> return FTS[:limit] unchanged (T-10 needs identical
   order); else rrf_merge, hydrate vector-only ids with the FTS filters (current version,
   live, ready, `_scope`), sort candidates by distance in Python, `rank` = fused score.
   In api.search_knowledge: `mode: Literal["fts", "hybrid"]`, dispatch hybrid lazily; in
   passages_for pass `mode="hybrid"` (module-global name: T-12 monkeypatches it). Check
   router.py for a typed `mode` query param (then `make gen`).
3. pipeline.index: after replace_chunks, `embed_document(ctx, doc, version_id=vid)` in
   try/except Exception (log, never fail extraction). events.py subscribers
   `knowledge.embed_added_document` / `knowledge.embed_changed_document`.
4. workflows.py `knowledge_reembed_all(workspace_id, model, replaces)` (name =
   REEMBED_WORKFLOW): poll step until index_name set (DBOS.sleep_async), loop steps of 64
   (`faults.killpoint(f"knowledge.reembed.batch_{n}")` after each), final step: model
   active, replaces retired + its rows deleted. The no-more-chunks probe must not call
   embed (T-07 expects calls [64, 6]). worker.py: register EMBED_QUEUE
   (worker_concurrency=2), add to main_queues (check test_worker_queues/listen first);
   call `decisions.configure_embeddings(settings.embeddings, net_policy=...)` beside
   configure_generation (worker only; api-process query embedding is a Scott item).
5. cli.py `tumnis embeddings index <model> --dims N`.
6. Remove markers T-04..10, 12, 13, A3.4 x2 only after CI shows them passing.

## Done

- `0bf175d test(knowledge): P3-10 spec tests (red)`: every spec test as a strict xfail
  (`reason="spec:P3-10"`), confirmed red locally (unit + contract: 4 xfailed; the 11
  integration tests collect; CI will show them xfailed):
  - T-01..03 `knowledge/tests/unit/test_rrf.py`
  - T-11 `decisions/tests/contract/test_embeddings_adapter.py::test_contract_fake_and_recorded_vllm`
  - T-04, 05, 06, 12, 13 `knowledge/tests/integration/test_hybrid.py`
  - T-07, 08 `knowledge/tests/integration/test_reembed.py` (+ `_embed_probe.py`, imported
    by the killed worker subprocess)
  - T-09, 10 `knowledge/tests/integration/test_local_only.py`
  - A3.4 `backend/tests/acceptance/test_a3_4_hybrid_search.py` (two tests). DEVIATION:
    A3.4 belongs to P3-00, which is folded into P3-01's blocked spec PR; added here because
    it is P3-10's done-when. Say so in the PR body.
  - Eval set: `backend/fixtures/search_eval/{corpus,queries}.jsonl` (20 docs, 68 chunks,
    42 queries, 14 each keyword/paraphrase/mixed; projects "acme" = "Acme site", "beta" =
    "Beta app", null = workspace KB; each doc has `"paged"`). Generator (scratch, not
    committed): `/tmp/claude-1002/P3-10-c0/make_corpus.py <out_dir>`.
  - Helpers: `knowledge/tests/_eval.py` (`load_eval_corpus`, `recorded_embeddings`,
    `embed_corpus`, `EVAL_MODEL="BAAI/bge-m3"`, `EVAL_DIMS=1024`); fixture
    `use_embedders(*adapters, primary=None)` in `knowledge/tests/integration/conftest.py`.
- Red-phase `# type: ignore[...]` comments sit on the imports of not-yet-existing names
  (mypy `warn_unused_ignores` will flag each one once its name exists: remove them as you
  go, it is not an assertion edit). Precedent: a59c9e0 (P2-09).

## Not done (in TDD order)

1. **Recorded vectors** (`backend/fixtures/search_eval/vectors/{chunks,queries}.jsonl`,
   lines `{"text_sha256","model","vector"}`, plus `README`). The real model is available
   locally: BAAI/bge-m3 ONNX export downloaded to `/tmp/claude-1002/P3-10-c0/bge-m3/onnx/`
   and a throwaway venv `/tmp/claude-1002/P3-10-c0/venv` (onnxruntime 1.30, tokenizers,
   numpy). `probe.py` there shows usage: output index 1 (`sentence_embedding`, 1024 dims)
   then L2-normalise. Chunk text embedded = `context_text` = `"\n".join([*heading_path,
   text])`; query text = the query string. Round floats to ~6 decimals. Write
   `backend/scripts/record_eval_vectors.py` with `--endpoint/--model` (through the real
   `VllmEmbeddings` adapter) and an `--onnx-dir` local-recording mode (lazy imports);
   README states model, date, command, and that vectors came from the ONNX export on CPU
   because no vLLM was reachable (Scott item: re-record on the homelab).
   If HF download is needed again: allowed_domains huggingface.co, *.hf.co.
2. **decisions**: `adapters/embeddings/{__init__.py (empty), port.py, vllm.py, hosted.py,
   fake.py}`.
   - Port `EmbeddingsAdapter(Protocol)`: `model: str; dims: int; hosted: bool;
     async embed(texts) -> list[list[float]]; async health()`.
   - `VllmEmbeddings(base_url, model, dims, *, clock, net_policy, resolver=..., transport=None)`
     subclassing `Adapter`, name `decisions.embeddings_vllm`, POST `/v1/embeddings`
     `{"model", "input": [...], "encoding_format": "float"}`; sort `data` by `index`;
     check count and dims (AdapterRejected); empty input -> [] with no call. Generalise
     `openai_compat.ChatEndpoint` with a `path` (and optional bearer header for hosted)
     rather than copying it. `HostedEmbeddings` = same with `hosted=True`, bearer key,
     name `decisions.embeddings_hosted`.
   - `FakeEmbeddings(model=DEFAULT_EMBEDDING_MODEL?, dims=..., *, hosted=False,
     recorded: Path | None = None)`: recorded -> strict lookup by sha256(text)+model in
     `recorded/chunks.jsonl` and `recorded/queries.jsonl`, raise LookupError on a miss;
     otherwise deterministic hash-derived unit vectors. `calls: list[list[str]]`
     (one entry per embed call, only non-empty batches), `hold: asyncio.Event | None`
     (await it before answering; T-07 uses it). Defaults used by tests: `FakeEmbeddings()`.
   - Register both in `decisions/adapters/__init__.py` lazily (importlib, like
     `build_vllm`); add contract classes (`Test*` with `impl="fake"` and `"recorded"`) for
     both names so `contract_violations()` stays empty; recordings in
     `decisions/tests/recordings/vllm_embeddings/*.json` `{model, dims, exchanges:
     [{request, response: {status, body}}], recorded_at, notes}`; the T-11 test sends
     `TEXTS` then `[TEXTS[2], TEXTS[0]]` (list the second response's `data` out of index
     order). Vectors from the bge-m3 ONNX run; notes say the shape follows the vLLM docs.
   - Add the two real adapter modules to `.importlinter` `api-never-calls-out` forbidden
     list (shared file: report it).
   - `decisions/api.py`: `Embedders(adapters: tuple[EmbeddingsAdapter, ...], primary:
     str | None)` dataclass, `use_embedders(Embedders | None)` (test override, wins over
     configured), `configure_embeddings(settings, net_policy)` (worker), `EmbedResult(model,
     dims, vectors, skipped, hosted)`, `embedders_for(project_id) -> list[info]` (allowed,
     primary first; hosted dropped when `projects.local_decisions_only(project_id)` or the
     workspace setting section `embeddings` (`local_only: bool`) says so; workspace KB
     follows the workspace setting), `embed(texts, project_id, *, model=None)`.
   - `settings.py`: `EmbeddingsSettings(base_url, model="BAAI/bge-m3", dims=1024)` as
     `Settings.embeddings` (env `EMBEDDINGS__*`; plan says `EMBEDDING_DIMS`: deviation).
     Hosted real config (key storage) is NOT built: Scott item.
3. **knowledge** migration `knowledge_0007` (down `knowledge_0006`, `phase="expand"`):
   assert `vector` extension exists (initdb creates it; pgvector 0.8.6 in the test image),
   `create_tenant_table("embedding_models", model, dims CHECK 1..2000, provider, status
   CHECK building/active/retired, index_name, unique (workspace_id, model))`,
   `create_tenant_table("embeddings", chunk_id FK chunks ON DELETE CASCADE, project_id,
   model, embedding vector, unique (workspace_id, chunk_id, model))`, index
   `embeddings_ws_model_project`, default HNSW index `embeddings_hnsw_baai_bge_m3`:
   `USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WITH (m=16, ef_construction=64)
   WHERE model = 'BAAI/bge-m3'`. Models in `models.py` with a small `UserDefinedType`
   `Vector` (col spec `vector`); write vectors as text `'[...]'` with `CAST(:v AS vector)`.
   P3-14 (#107/#113) also has knowledge_0007/0008: whoever merges later re-chains.
4. **knowledge rules**: `RRF_K=60`, `CANDIDATES=50`, `EMBED_BATCH=64`, `EMBED_QUEUE="embed"`,
   `REEMBED_WORKFLOW="knowledge_reembed_all"`, `DEFAULT_EMBEDDING_MODEL="BAAI/bge-m3"`,
   `DEFAULT_EMBEDDING_DIMS=1024`, `Ranked(chunk_id, rank)`, `FusedHit(chunk_id, score,
   sources, best_rank)` (DEVIATION: the plan's FusedHit carries citation fields that a
   pure merge of `Ranked` cannot know; hits are hydrated in search), `rrf_merge` (ties:
   better best rank, then chunk_id), `recall_at_k`, `model_slug`, `hnsw_index_name(model)`
   (`embeddings_hnsw_<slug>`, <= 63 chars), one constant for opclass + operator.
5. **knowledge/embeddings.py** (new; re-export from api): `embed_document(ctx, document_id)
   -> int` (current version's chunks lacking a row for the target model; target = the
   first allowed embedder whose model row is `active`, else the first allowed embedder,
   ensured `active` with `index_name` from `pg_indexes`; catches nothing: callers do),
   `set_embedding_model(s, model, dims, provider) -> ReembedRequest(model, replaces)`
   (row `building`, replaces = current active preferred model), `start_reembed(ctx,
   request) -> workflow id` (`reembed:<ws>:<model>`, enqueue on `embed`),
   `embedding_models(s)`, `create_embedding_index(owner_url, model, dims) -> index name`
   (owner, sets index_name on every workspace's row; owner bypasses RLS).
6. **search.py** (new): `vector_query(model, dims, vector, *, project_id, project_ids,
   candidates) -> (sql, params)` (the plan's exact SQL; cast dims must match the index),
   `tune_session(s)` (`SET LOCAL hnsw.ef_search = 100; SET LOCAL hnsw.iterative_scan =
   relaxed_order`), `hybrid(s, q, *, project_id, limit, project_ids, agent_written=True)`:
   FTS top 50 via `api.search_knowledge(mode="fts")`, query embedded with the search model
   (AdapterError/skipped -> FTS only), vector top 50, `rrf_merge`, hydrate vector-only
   hits with the FTS path's filters (ready, current version, live, scope), `rank` = fused
   score. `api.search_knowledge`: widen `mode` to `Literal["fts","hybrid"]` and dispatch
   (lazy import); `passages_for` passes `mode="hybrid"`. Keep api.py edits to those lines
   (P2-17 #132 rewrites search_knowledge into `_search_stmt` + `search_page` and adds
   `agent_written`; the hybrid function already accepts `agent_written`).
7. **Indexing**: `pipeline.index` (step 8) calls `embed_document` after the chunks commit,
   catching any error (never fail extraction); `knowledge/events.py` subscribers
   `knowledge.embed_added_document` / `knowledge.embed_changed_document` on
   `document.added` / `document.changed` (text entries index in the api process, which
   must not call out). Only the search model is embedded at index time; building models
   are covered by `reembed_all`.
8. **workflows.py**: `knowledge_reembed_all(workspace_id, model, replaces)` on `embed`:
   wait (DBOS.sleep polling) until `index_name` is set; loop a step that embeds the next
   64 chunks lacking a row for `model` (`INSERT ... ON CONFLICT DO NOTHING`), then
   `faults.killpoint(f"knowledge.reembed.batch_{n}")`; final step: model `active`,
   `replaces` `retired` and its rows deleted. `worker.py`: register `embed`
   (`worker_concurrency=2`), add it to `main_queues()` (check
   `core/tests/unit/test_worker_listen.py` / `test_worker_queues.py`).
9. **cli.py**: `tumnis embeddings index <model> --dims N` (owner URL, like migrate).
10. Finish: `make check` (semgrep meta test needs `SEMGREP_SETTINGS_FILE`,
    `SEMGREP_LOG_FILE`, `SEMGREP_VERSION_CACHE_PATH` under `/tmp/claude-1002/P3-10-c0/`),
    `make test` once, rely on CI for integration (DOCKER LOAD RULE), remove each marker
    only after its test passes, merge origin/main (coordinator: #131 merged as 13e7f79),
    open the PR, request `@coderabbitai review` once, work the loop, send
    "#<PR> MERGE-READY at <sha>" to main.

## Context7 / docs consulted

pgvector README (mixed dimensions via expression + partial HNSW index; iterative scans
0.8.0+, relaxed_order), vLLM stable docs (OpenAI-compatible `/v1/embeddings` response
shape `data[].index/embedding`), DBOS 3.1 (queues persisted by register_queue). Still to
check: SQLAlchemy 2.x `UserDefinedType`, DBOS `sleep_async`.

## Scott items

- Vectors recorded from the bge-m3 ONNX export on CPU, not the homelab vLLM; re-record
  and run the model-swap check on the homelab (done-checklist item).
- Hosted embedder credential storage/config UI not built (adapter, routing and tests are).
- `knowledge.chunk_tokenizer` still defaults to all-MiniLM-L6-v2 (P1-16 area); bge-m3 is
  the picked embedding model.
- MCP `search_knowledge` tool: owned by P2-17 (#132); it should pass `mode="hybrid"` once
  both land.

## Verify

```
cd backend
uv run pytest -q tumnis/modules/knowledge/tests/unit/test_rrf.py tumnis/modules/decisions/tests/contract/test_embeddings_adapter.py
uv run pytest -q --collect-only tumnis/modules/knowledge/tests/integration tests/acceptance/test_a3_4_hybrid_search.py
```
