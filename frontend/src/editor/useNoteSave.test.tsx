// T-P1-17-02 (ADR-0008): opening a note never writes, even when its Markdown is not in
// canonical form; the first edit saves once, with the canonical Markdown and the version
// that was read. (The keystroke goes through the editor's own transaction: jsdom has no
// layout, so ProseMirror cannot read typed DOM text there.)
import type { Editor as TiptapEditor } from "@tiptap/core";
import { waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeDocument } from "../test/factories";
import { KnowledgeFake } from "../test/msw/knowledge";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import Editor from "./Editor";
import { SAVE_DEBOUNCE_MS } from "./useNoteSave";

/** Inserts `text` right after the first occurrence of `after`, as a keystroke would. */
function typeAfter(editor: TiptapEditor, after: string, text: string): void {
  let at = -1;
  editor.state.doc.descendants((node, pos) => {
    if (at < 0 && node.isText && node.text?.includes(after)) {
      at = pos + (node.text.indexOf(after) + after.length);
    }
  });
  expect(at).toBeGreaterThan(0);
  editor.view.dispatch(editor.state.tr.insertText(text, at));
}

test("[P1-17][ADR-0008] opening a note never writes", async () => {
  const note = makeDocument({
    title: "Moodboard",
    kind: "text",
    role: null,
    body_md: "* Logo\n* Colours\n",
    version: 1,
    status: "ready",
  });
  const fake = new KnowledgeFake({ documents: [note] });
  server.resetHandlers();
  server.use(...fake.handlers);

  let editor: TiptapEditor | null = null;
  renderWithProviders(
    <Editor
      document={note}
      projectId={null}
      onReady={(e) => {
        editor = e;
      }}
    />,
  );
  await waitFor(() => {
    expect(editor).not.toBeNull();
  });
  // Longer than the save debounce: a save from opening would have gone out by now.
  await new Promise((resolve) => setTimeout(resolve, SAVE_DEBOUNCE_MS * 2));
  expect(fake.recorder.writes()).toEqual([]);

  typeAfter(editor as unknown as TiptapEditor, "Logo", "s");

  await waitFor(() => {
    expect(fake.recorder.writes()).toEqual([
      `PATCH /v1/knowledge/documents/${note.id}`,
    ]);
  });
  const sent = fake.recorder.sent.find((s) => s.method === "PATCH");
  expect(sent?.body).toEqual({ body_md: "- Logos\n- Colours\n", version: 1 });
  expect(sent?.idempotencyKey).toBeTruthy();
  await new Promise((resolve) => setTimeout(resolve, SAVE_DEBOUNCE_MS * 2));
  expect(fake.recorder.writes()).toHaveLength(1);
});
