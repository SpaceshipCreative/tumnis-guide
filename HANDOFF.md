# HANDOFF: FIX-docling-image (c0 -> c1)

Task prompt: ~/tumnis-coordinator/prompts/wave1/FIX-docling-image.txt (Scott decision 97).
PR: #185 https://github.com/SpaceshipCreative/tumnis-guide/pull/185 (DRAFT, base main, head `fix/docling-image`).
Worktree: this one, on the throwaway branch at main + the commits below (the `git switch` hit the read-only .git/config).
Push with `/usr/bin/git push origin HEAD:fix/docling-image` (fast-forward; never force).

## Commits (on top of main c021414e)
- f3fba1c3 test(deploy): spec tests (red): backend/tests/deploy/test_extract_image.py, data/courier-rates.docx, `extract_image` marker in pyproject.
- a1986019 feat(deploy): Dockerfile `base` / `extract-deps` / `app-extract` / `app` (last = default) stages.
- af1f3775 test(knowledge): test_docling_memory (red) + REAL_DOCLING_TESTS entry in backend/conftest.py.
- 8162bffb fix(knowledge): extraction.PAGES_IN_FLIGHT = 8 (pdf.queue_max_size).
- c1676ac8 feat(deploy): compose.yaml worker-extract image `...:${TUMNIS_VERSION:-latest}-extract`; compose.preview.yaml pins worker-extract to slim; new deploy/compose.test-real.yaml.
- (this commit) chore: handoff. Only f3fba1c3 is pushed so far; the push of this commit pushes them all.

## Tests
- Static (unit, make check): test_extract_stage_adds_docling_and_its_models_to_the_slim_image_only, test_only_worker_extract_runs_the_extract_image: GREEN, markers removed.
- test_converter_keeps_few_pages_in_flight (docling job): GREEN locally in a docling venv.
- test_extract_image_converts_offline_as_its_user (needs TUMNIS_EXTRACT_IMAGE): the same docker run was done by hand and passed (uid 10001, --network none, --read-only, --memory 4g, peak 1.6 GiB). Not yet run through pytest with TUMNIS_EXTRACT_IMAGE set; do that next (`TUMNIS_EXTRACT_TESTS=require TUMNIS_EXTRACT_IMAGE=tumnis:fdi-extract uv run pytest -m extract_image -k offline tests/deploy`) and in CI.
- test_real_stack_extracts_uploads_and_finds_their_passages (needs TUMNIS_REAL_STACK_URL): PASSED locally in 40 s on a fresh compose.yaml + compose.test-real.yaml stack (run with the sandbox off: the sandbox has its own loopback, connection refused otherwise).
- Both docker tests skip without their variable; `TUMNIS_EXTRACT_TESTS=require` turns a missing variable into a failure (set it in CI).
- make check: EXIT 0 at c1676ac8 (needs SEMGREP_SETTINGS_FILE/LOG_FILE/VERSION_CACHE_PATH under $TMPDIR, and `npm ci --prefix frontend` once).

## Evidence gathered (for the PR body and final report)
- Image sizes (`docker image inspect -f {{.Size}}`, re-measured at handoff): slim before (main) tumnis:fdi-before 678,775,886 B; slim after tumnis:fdi-slim 678,780,974 B (+5 KB: the extraction.py/tests change; same layers otherwise); extract tumnis:fdi-extract 4,746,906,513 B (4.7 GB). From `docker history`: torch/docling venv layer 1.79 GB, models layer 766 MB, OpenCV libs + fonts apt layer ~216 MB (mesa via libgl1).
- Models: docling-tools models download layout tableformer rapidocr -> /opt/tumnis/docling-models (731 MB); tokenizer sentence-transformers/all-MiniLM-L6-v2 -> HF_HOME=/opt/tumnis/huggingface (784 KB). HF_HUB_OFFLINE=1, DOCLING_ARTIFACTS_PATH set.
- Findings fixed: (1) opencv-python needs libxcb1, libgl1, libglib2.0-0t64 (ImportError at build); (2) without fonts-liberation, PDFs with non-embedded base fonts (the rate card's Helvetica/Helvetica-Bold) lose bold in the rendered page, and headings became body text (1 chunk, heading "Acme rate card" instead of 3 chunks Overview/Rates/Terms); (3) memory: default queue_max_size=100 -> 28 pages 2.1 GiB, 105 pages 3.4 GiB (cgroup peak), 210 pages OOM-killed (exit 137) under --memory 4g; with 8 -> 105 pages 2.26 GiB / 165 s, 210 pages 2.36 GiB / 392 s (32 cores).
- E2E search responses: 'Senior designer' -> rate-card-table.pdf, heading ["Rates"], page 2; 'Zephyrine' -> courier-rates.docx, heading ["Courier rates","Rates"], page null. worker-extract log: "Finished converting document rate-card-table.pdf in 25.59 sec", "courier-rates.docx in 0.04 sec", models read from /opt/tumnis/docling-models.
- Prompt says GET /v1/search: that route searches tasks and projects only (search/api.py Scope). Passages are GET /v1/knowledge/search; the test uses that. Say so in the PR body.

## Docker state
- Compose project `tumnis-fdi`: DOWN (`down -v` done). Images kept: tumnis:fdi-before, tumnis:fdi-slim, tumnis:fdi-extract, tumnis:fdi-extract-deps (remove with `docker image rm` when done).
- Env file (not in the repo): /tmp/claude-1002/fix-docling-image/real.env (test.env passwords + TUMNIS_IMAGE=tumnis:fdi-slim, TUMNIS_EXTRACT_IMAGE=tumnis:fdi-extract, TUMNIS_REAL_PORT=18995).
- Up:   `docker compose -p tumnis-fdi -f deploy/compose.yaml -f deploy/compose.test-real.yaml --env-file /tmp/claude-1002/fix-docling-image/real.env up -d --no-build --quiet-pull --wait --wait-timeout 600`
- Down: same with `--profile backups down -v --timeout 20`
- E2E (sandbox off): `cd backend && TUMNIS_EXTRACT_TESTS=require TUMNIS_REAL_STACK_URL=http://127.0.0.1:18995 uv run pytest -p no:randomly -s -rA -k real_stack tests/deploy` (needs a fresh stack each run: setup works once).
- Build: `docker build -q -f deploy/Dockerfile --target app-extract -t tumnis:fdi-extract .` and `docker build -q -f deploy/Dockerfile -t tumnis:fdi-slim .` (bare, from the worktree root).

## Remaining steps
1. Push: `/usr/bin/git push origin HEAD:fix/docling-image`.
2. CI in .github/workflows/docling.yml (existing `docling` job, not a new job, so no spec change; raise its timeout-minutes, which is not in BUDGETS): after the pytest step, build both images (docker/build-push-action or `docker build`; use a gha cache scope of its own, e.g. `tumnis-extract`, not `tumnis-image`), run `pytest -m extract_image tests/deploy` with TUMNIS_EXTRACT_IMAGE, then bring up compose.yaml + compose.test-real.yaml (project e.g. tumnis-real, TUMNIS_IMAGE/TUMNIS_EXTRACT_IMAGE pointing at the built tags, `--no-build`), run the real-stack test with TUMNIS_REAL_STACK_URL=http://127.0.0.1:8090 and TUMNIS_EXTRACT_TESTS=require, dump `logs` on failure. Measure the job's wall time and `df -h`; if it is too slow, propose a ci.yml job to main (needs the exact BUDGETS line and an OK; coordinate with fix/unit-split).
3. Publish the extract image where production pulls it: .github/workflows/deploy.yml (build `--target app-extract`, tags `sha-$SHA-extract` and `main-extract`, push), release.yml (`<tag>-extract`, and decide on its SBOM), preview.yml needs nothing (preview pins slim).
4. Trivy: run the security job's trivy command locally on tumnis:fdi-extract; if clean, add a scan step where it is built; if HIGH/CRITICAL with a fix, Scott item.
5. Docs: deploy/README.md (two images, sizes, build-time model download, no runtime egress, CHUNK_TOKENIZER build arg, 4g measurement), README "Not yet available" (drop extraction), CHANGELOG entry, docs/OPERATIONS if it lists images; .env.example only if a variable changes (none so far).
6. Update the PR body (docs cited: docling-project.github.io usage/advanced_options "Model prefetching and offline usage" and DOCLING_ARTIFACTS_PATH; huggingface_hub environment variables HF_HUB_OFFLINE / HF_HOME / HF_HUB_DISABLE_TELEMETRY; transformers installation "Offline mode"; uv docs "Docker integration: intermediate layers with --no-install-project" and "dependency groups --no-dev/--group"; docs.docker.com multi-stage `--target` and Compose merge of volumes by target), mark ready, request CodeRabbit, run the review loop, then send "#185 MERGE-READY at <sha>" to main.

## Scott items / notes
- Extract image is 4.7 GB (torch CPU + opencv + models).
- The locked tree installs opencv-python (not -headless), which needs the X11/GL libs (~216 MB with mesa). opencv-python-headless via a uv override could drop them (lock change; verify which package pulls opencv-python first): offered, not done.
- docling-tools also fetches the layout model's ONNX variant (~164 MB) that is unused with torch; trimming it is possible: offered, not done.
- Long PDFs: 210 pages take ~6.5 min (32 cores) at 2.4 GiB peak.
- The previous Scott item "should `docling` become a required check?" still stands; the image tests ride in that non-required job.
- New egress at BUILD time only (huggingface.co, *.hf.co, www.modelscope.cn); none at run time.
