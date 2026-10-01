// T-P1-17-02 (ADR-0008): opening a note never writes, even when its Markdown is not in
// canonical form; the first edit saves once, with the canonical Markdown and the version
// that was read. (The keystroke goes through the editor's own transaction: jsdom has no
// layout, so ProseMirror cannot read typed DOM text there.)
import type { Editor as TiptapEditor } from "@tiptap/core";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeDocument, makeProblem } from "../test/factories";
import { KnowledgeFake } from "../test/msw/knowledge";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import Editor from "./Editor";
import { SAVE_DEBOUNCE_MS, useNoteSave } from "./useNoteSave";

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

test("[P1-17][ADR-0008] a failed save keeps the edit, and closing the editor sends it (#134)", async () => {
  // CodeRabbit on #134: a save cleared the pending edit before it was sent, and a
  // failure (not a conflict) never put it back, so closing the editor after a failed
  // save lost the edit although the error said it could be retried.
  const note = makeDocument({
    title: "Moodboard",
    kind: "text",
    role: null,
    body_md: "- Logo\n",
    version: 1,
    status: "ready",
  });
  const fake = new KnowledgeFake({ documents: [note] });
  const path = `/v1/knowledge/documents/${note.id}`;
  server.resetHandlers();
  server.use(
    http.patch(
      path,
      () =>
        HttpResponse.json(
          makeProblem({ status: 500, code: "internal", title: "internal" }),
          {
            status: 500,
            headers: { "Content-Type": "application/problem+json" },
          },
        ),
      { once: true },
    ),
    ...fake.handlers,
  );

  const { result, unmount } = renderHook(() => useNoteSave(note));
  act(() => {
    result.current.onChange("- Logos\n");
  });
  await waitFor(() => {
    expect(result.current.status).toBe("error");
  });
  expect(fake.sentBodies("PATCH", path)).toEqual([]);

  unmount();
  await waitFor(() => {
    expect(fake.sentBodies("PATCH", path)).toEqual([
      { body_md: "- Logos\n", version: 1 },
    ]);
  });
});

test("[P1-17][ADR-0008] closing the editor during a save that then fails still sends the edit (#143)", async () => {
  // CodeRabbit on #143: closing the editor while a save was in flight found nothing
  // pending, and the failed save kept the edit but did not send it again, so it was lost.
  const note = makeDocument({
    title: "Moodboard",
    kind: "text",
    role: null,
    body_md: "- Logo\n",
    version: 1,
    status: "ready",
  });
  const fake = new KnowledgeFake({ documents: [note] });
  const path = `/v1/knowledge/documents/${note.id}`;
  let answer: () => void = () => undefined;
  const answered = new Promise<void>((resolve) => {
    answer = resolve;
  });
  server.resetHandlers();
  server.use(
    http.patch(
      path,
      async () => {
        await answered;
        return HttpResponse.json(
          makeProblem({ status: 500, code: "internal", title: "internal" }),
          {
            status: 500,
            headers: { "Content-Type": "application/problem+json" },
          },
        );
      },
      { once: true },
    ),
    ...fake.handlers,
  );

  const { result, unmount } = renderHook(() => useNoteSave(note));
  act(() => {
    result.current.onChange("- Logos\n");
  });
  await waitFor(() => {
    expect(result.current.status).toBe("saving");
  });

  unmount();
  answer();
  await waitFor(() => {
    expect(fake.sentBodies("PATCH", path)).toEqual([
      { body_md: "- Logos\n", version: 1 },
    ]);
  });
});
