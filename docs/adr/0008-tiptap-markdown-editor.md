# ADR-0008: Tiptap editor with Markdown as the stored form
Status: Accepted (2026-09-27) | Supersedes: none

## Context
One editor serves the project brief, knowledge-base text entries, task descriptions and agent draft review. Notes are saved into project folders as Markdown with a `tumnis_id` frontmatter key, where they can be edited outside Tumnis (FR-15). The editor needs mentions (`@` for tasks and people, `#` for documents), a slash menu, task lists, and must not rewrite a file just because it was opened.

## Options considered
- **Tiptap (MIT core) with its Markdown extension.** Rich editing, Mention and suggestion extensions for mentions and slash commands, a Markdown serializer. Deal-breaker only in that the Markdown extension is beta.
- **CodeMirror 6.** Exact Markdown. Deal-breaker: shows raw syntax and lacks mentions and slash commands out of the box.

## Decision
The editor is Tiptap with StarterKit, TaskList and TaskItem, Link, Mention with two triggers, a slash menu on `@tiptap/suggestion`, and the Markdown extension. Markdown is the stored form. Frontmatter is split off before the body loads and re-attached unchanged on save. Mentions serialize as readable links (`[Send invoice](tumnis://task/<id>)`). A canonical style (`-` bullets, `**` bold, `#` headings, one blank line between blocks) applies, and a file changes format at most once, on its first edit. Saving happens only when the serialized Markdown differs from what was loaded.

## Consequences
- The Markdown extension is beta: the Tiptap version is pinned and upgraded only through Renovate as manual-merge (label `needs-suite`) with the round-trip suite green.
- A round-trip fixture suite (`frontend/src/test/fixtures/roundtrip/*.md`: load, save, compare) runs in CI.
- The editor bundle loads only on screens that edit text, to protect the 200 KB initial JavaScript budget (PERF-2).

## Sources
- [Tiptap Markdown](https://tiptap.dev/docs/editor/markdown)
- [Tiptap Markdown installation](https://tiptap.dev/docs/editor/markdown/getting-started/installation)
