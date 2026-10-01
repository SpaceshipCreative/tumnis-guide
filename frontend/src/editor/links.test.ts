// T-P1-17-03 (ADR-0008): picking a task after `@` and a document after `#` inserts plain
// Markdown links, `[Title](tumnis://task/<id>)` and `[Title](tumnis://doc/<id>)`, so
// mentions round-trip as ordinary links with no custom node.
import { Editor } from "@tiptap/core";
import { waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { buildExtensions } from "./extensions";
import type { SuggestHandlers, SuggestItem, SuggestOpen } from "./links";

const TASK = {
  id: "0192f3a4-1111-7000-8000-000000000001",
  title: "Send invoice",
};
const DOC = {
  id: "0192f3a4-2222-7000-8000-000000000002",
  title: "Brand guide",
};

let editor: Editor | null = null;
afterEach(() => {
  editor?.destroy();
  editor = null;
});

test.fails("[P1-17][ADR-0008] mentions serialize as tumnis links", async () => {
  const menu: { open: SuggestOpen | null } = { open: null };
  // Read through a function: the menu opens inside the editor, out of TS's sight.
  const current = (): SuggestOpen | null => menu.open;
  const suggest: SuggestHandlers = {
    tasks: (query) =>
      Promise.resolve([TASK].filter((t) => t.title.startsWith(query))),
    docs: (query) =>
      Promise.resolve([DOC].filter((d) => d.title.startsWith(query))),
    onOpen: (state) => {
      menu.open = state;
    },
  };
  editor = new Editor({
    extensions: buildExtensions({ suggest }),
    content: "Next:",
    contentType: "markdown",
  });

  const pick = async (trigger: string, query: string, item: SuggestItem) => {
    if (!editor) throw new Error("no editor");
    menu.open = null;
    editor.commands.focus("end");
    editor.commands.insertContent(` ${trigger}${query}`);
    await waitFor(() => {
      expect(current()?.items).toEqual([item]);
    });
    current()?.select(item);
  };

  await pick("@", "Send", TASK);
  await pick("#", "Brand", DOC);

  expect(editor.getMarkdown()).toBe(
    `Next: [Send invoice](tumnis://task/${TASK.id}) [Brand guide](tumnis://doc/${DOC.id})`,
  );
});
