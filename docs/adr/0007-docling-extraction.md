# ADR-0007: Docling for extraction, vision fallback on the vLLM cluster
Status: Accepted (2026-09-27) | Supersedes: none

## Context
Knowledge documents (PDFs including scans, tables and multi-column layouts, DOCX, XLSX, PPTX, HTML, Markdown) must be turned into searchable, citable chunks that keep headings and page numbers, so agents can cite "document X, page 4" (FR-15.2). Extraction runs on the homelab, must be license-safe for a product that may be hosted, and must not starve dispatch or syncs.

## Options considered
- **Docling.** Layout analysis, table structure, OCR only where needed, a vision-model pipeline for hard pages, and a HybridChunker that keeps headings and page provenance and sizes chunks to the embedding tokenizer. MIT licensed. Deal-breaker only in its image size.
- **PyMuPDF4LLM.** Fast. Deal-breaker: AGPL.
- **Marker.** Good output. Deal-breaker: model weights licensed only under $5M revenue or funding.
- **MarkItDown.** Light. Deal-breaker: no layout analysis.

## Decision
`extract_document` uses Docling: Markdown and plain text read directly; DOCX, XLSX, PPTX and HTML through Docling's standard pipeline without OCR; PDFs with layout and table structure and OCR only on pages with no text layer. Pages Docling cannot read cleanly go to Docling's vision-model pipeline pointed at the vLLM cluster through its OpenAI-compatible API. Chunks come from the HybridChunker. Extraction runs only in the `worker-extract` container on the `extract` queue, after a ClamAV scan and a content-type sniff.

## Consequences
- A heavier image; extraction is isolated in `worker-extract` with its own memory limit so a 300-page PDF cannot starve other queues.
- Each pipeline stage is a DBOS step, so a crash resumes at the failed step.
- An extraction regression set (`backend/fixtures/extraction/`) with expected chunks and pages guards Docling upgrades.

## Sources
- [Docling usage](https://docling-project.github.io/docling/usage/)
- [Docling chunking](https://docling-project.github.io/docling/concepts/chunking/)
- [Docling vision models](https://docling-project.github.io/docling/usage/vision_models/)
- [Docling confidence scores](https://docling-project.github.io/docling/concepts/confidence_scores/)
