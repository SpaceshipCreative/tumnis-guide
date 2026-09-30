# HANDOFF: P1-16 impl-2 (c0 -> c1)

PR: #102 https://github.com/SpaceshipCreative/tumnis-guide/pull/102, branch `wp/P1-16-impl-2`, base main.
Push with `/usr/bin/git push origin HEAD:wp/P1-16-impl-2` (never force). Scratch files: `$TMPDIR/P1-16-impl-2-c0/` (the PR body is `pr-body.md`; `t06/` is a venv with this lock plus docling 2.131.0 and CPU torch; `hf/` is the model cache).

## Commits (on top of main 12956ea, then main e65187c merged)
- 349554b `test(knowledge): P1-16 impl-2 spec tests (red)`: T-13 moved from handoff/ unchanged; handoff/ removed
- eea2c14 `feat(knowledge): register the vision model as knowledge.vision (T-P1-16-13 fake)`
- ca3f0a9 `feat(knowledge): VllmVision reads a page image to Markdown (T-P1-16-13)`: adapters/vision.py, lazy `build_vision`, importlinter `api-never-calls-out` entry, synthesized recordings `tests/recordings/vllm_vision/`, unit `test_vision_errors.py`
- 756133e `feat(knowledge): DoclingExtractor and the extraction set's expected chunks (T-P1-16-06)`: extraction.py, adapters/docling.py, pipeline `_extractor`/`_vision` real wiring, 10 `*.expected.yaml`, pyproject mypy override + 3 filterwarnings
- 1226101 merge of origin/main (e65187c)
- f70381b `test(knowledge): wire A1.5's knowledge_app; the EICAR step goes green`: `_phase1.knowledge_app` + `_WithMinioLocation`, `upload()` uses `fixture_bytes`, new `tests/acceptance/conftest.py` (master key before seed; undo pipeline state), A1.5 EICAR marker removed, plan Part A row, settings docstring

## State
- CI on the head: everything green EXCEPT `integration`: 1 failed, 1084 passed, 16 xfailed.
  - FAILED `tests/acceptance/test_a1_5_pdf_to_packet.py::test_eicar_upload_is_quarantined`, with `LookupError: 0 projects named 'Acme site'`. The seed (`backend/fixtures/seed/projects.yaml`) has `Acme brand refresh`, `Authenticity course` and `Tumnis dogfood`. The plan's phase 1 seed additions (P1-04/P1-06) that add `Acme site` aren't on main.
  - So the marker removal was premature. NEXT STEP: restore `@pytest.mark.xfail(strict=True, reason="spec:P1-16")` on `test_eicar_upload_is_quarantined` exactly as it was (spec-guard allows a marker to be re-added; never touch the assertions), and note in the PR body that it waits for the seed's `Acme site`. Alternatively ask main whether adding `Acme site` to the seed belongs here. Don't edit the test body.
  - Keep the conftest's master-key fix: without it, sign-in 401s because `seed` skips the user when no master key is loaded.
- CodeRabbit: 1 review posted (check pass). Its comments have NOT been read or answered yet. Next: read the inline comments, review bodies and GraphQL `reviewThreads`, then fix or reply and resolve each, per ~/tumnis-coordinator/pr-review-loop.md.
- `preview` stays pending (expected).

## Spec tests
- T-13 (TestFakeVision, TestVllmVision): markers removed, green locally and in CI (contract).
- T-06: marker KEPT. It passes 10/10 locally against real Docling (`--runxfail` in the t06 venv); in CI it xfails because Docling isn't installed. The coordinator said to keep it on (option c).
- A1.5 `test_pdf_is_scanned_extracted_and_filed`: marker KEPT (needs real Docling).
- A1.5 `test_eicar_upload_is_quarantined`: see above (restore the marker).

## Decisions (binding, from the coordinator)
- Option (c): ship DoclingExtractor, extraction.py and the expected files. Keep the T-06 and A1.5 steps 1-2 markers ON. Don't touch ci.yml, pyproject pins (typer), uv indexes or the lock for Docling. The measurement block and options sit under "Scott items" in the PR body (done).
- The P1-15 scan agent owns `pipeline.place`, `_is_note` and notes identification: don't change them. Expect a main merge after #99.

## Deviations (already in the PR body)
The vision call is a direct chat completion (no `vlm_converter`); `to_chunk_rows` lives in adapters/docling.py (import cycle); Docling isn't a registered adapter; the recordings are synthesized; plain text converts as Markdown.

## Scott items (in the PR body)
1. Docling in CI (numbers and options a/b/c). 2. Re-record vllm_vision against the homelab vLLM. 3. granite-docling answers DocTags, not Markdown, so the vision model choice needs a decision. 4. On real Docling, handwriting.pdf page 2 grades fair/fair, so the low-confidence rule may need tuning. 5. The "kill test 20 runs" item isn't done: there's no bare single-test Docker run on the VM.

## Remaining steps
1. Restore the EICAR marker (above), then run `make check`, commit and push.
2. Update the PR body's "Spec tests" and "Test results" sections (`gh pr edit 102 --body-file ...`). The body currently says the EICAR marker was removed and passing, which is wrong.
3. Work the CodeRabbit threads until none is open; comment `@coderabbitai review` after pushes (the quota is tight).
4. When CI is green (except `preview`) and there are no open threads: SendMessage to main "#102 MERGE-READY at <sha>". Never merge.

## Verify
- `cd backend && uv run pytest -m contract tumnis/modules/knowledge/tests/contract/test_vision_contract.py` (4 pass)
- `cd backend && uv run pytest tumnis/modules/knowledge/tests/unit/test_vision_errors.py` (10 pass)
- Real Docling: `cd backend && PYTHONPATH=. HF_HOME=$TMPDIR/P1-16-impl-2-c0/hf HF_HUB_OFFLINE=1 $TMPDIR/P1-16-impl-2-c0/t06/bin/python -m pytest -p no:randomly -m integration --runxfail -n 3 tumnis/modules/knowledge/tests/integration/test_extraction_set.py` (10 pass)
- `gh pr checks 102`; `gh run view <id> --log-failed`
