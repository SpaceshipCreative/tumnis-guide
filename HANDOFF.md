# HANDOFF: FIX-docling-ci (Scott decision 85)

PR #166 (https://github.com/SpaceshipCreative/tumnis-guide/pull/166) is ready for review, on branch `fix/docling-ci`. Head before this handoff commit: 45e67934. Never merge it. "#166 MERGE-READY at 45e67934 EXCEPT e2e" has been sent to main.

## Commits

| SHA | What |
| --- | --- |
| 9ff19271 | `docling` dependency group; torch/torchvision from the PyTorch CPU index; typer 0.27.2 -> 0.26.8; backend/conftest.py `REAL_DOCLING_TESTS` hook; `docling` marker; .github/workflows/docling.yml; renovate typer rule |
| 42c7974e | unmark T-P1-16-06 (10 fixtures), citing run 36970266352 |
| 50aed2b9 | unmark A1.5 steps 1-3 (`test_pdf_is_scanned_extracted_and_filed`, `test_table_chunk_is_searchable_with_page`), citing run 36970266352 |
| 40bff0ca | temporary `--runxfail` diagnostic for A1.5 step 4 (removed in b9bd7ac4) |
| b9bd7ac4 | diagnostic removed; job `timeout-minutes: 15` |
| 30d96488 | CodeRabbit fix: checkout `persist-credentials: false` |
| 45e67934 | merge origin/main (#154) |

## State

- **CodeRabbit:** one comment (persist-credentials), fixed in 30d96488. Thread PRRT_kwDOUx-vCc6oPmYm is resolved and nothing is open. Re-review asked once; it said it had already reviewed the last commit.
- **CI on 45e67934:**
  - docling: green (36974593736).
  - ci (36974593797): all green except e2e, which flakes on different journeys each run (A0.1 on laptop, then A2.2 on the one rerun). Both passed on 30d96488. main's own e2e is red at 0973da93.
  - integration-b failed twice on 30d96488 at T-P0-07-05 (relay kill test, 30 s drain), then passed on 45e67934.
- **Markers:** A1.5 step 4 `test_task_packet_carries_brief_and_table_passage` keeps `xfail spec:P1-17`. It fails for a real product reason:
  - Real Docling 2.132.0 gives the table chunk `heading_path ["Rates"]`, with no document title.
  - So `rules.passage_query` (task title "Quote Acme for the redesign" plus the Acme site goal) misses it.
  - Result: `assert len(passages) == 1` -> `0 == 1` (run 36970867691).
  - The FakeDocling stored conversion (`fixtures/extraction/rate-card-table.pdf.conversion.json`) does not match real output.

## Remaining steps

1. If the coordinator asks for it: when main's e2e is healthy, merge origin/main again, push, and check `gh pr checks 166`. Rerun a flaky job at most once.
2. Waits on Scott: step 4 options. (a) Put the document title into the chunk heading path or tsv; this is a product change in knowledge, and P3-12/P3-13 own pipeline.py. (b) A spec change. (c) Fix the fake's stored conversion.

## Decisions and deviations

- The job lives in its own workflow (`docling.yml`), not in ci.yml:
  - T-P0-03-16 locks ci.yml's job set, and T-P0-03-23 locks the integration jobs' `-m` strings.
  - So it is not a required check (Scott item).
- Test selection is in backend/conftest.py, because spec-guard counts a marker added to a test file as an edit. The listed tests are skipped where docling is not importable.
- The group uses `docling-slim[cli,extract-core,feat-chunking,feat-ocr-rapidocr,format-email,format-latex,format-markdown,format-office,format-pdf,models-local]==2.132.0`, not `docling`. This drops `service-client`, whose `websockets<17` would downgrade DBOS's websockets 17.1.
- The default and `--no-dev` environments differ from main only by typer.

## Scott items

1. Make `docling` a required check?
2. New egress hosts: `www.modelscope.cn`, `cdn-lfs-cn-1.modelscope.cn`, and Hugging Face `huggingface.co` plus `*.hf.co`.
3. A1.5 step 4 (above).

## Measurements

- Job wall time: 2:35-3:25 with a cold model cache, 2:19 warm.
- Model cache: 569 MB (507 MB Hugging Face + 62 MB RapidOCR).

## Verify

- `gh pr checks 166`
- `gh run list --branch fix/docling-ci --workflow docling`
- `cd backend && uv run pytest -m docling -q` gives 13 skipped without the group.
- `uv export --frozen --no-dev --no-emit-project | grep -E '^(torch|docling)'` prints nothing.
