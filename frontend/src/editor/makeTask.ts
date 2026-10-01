// Make task (P1-17, FR-15.1): the checklist item under the cursor becomes a task of the
// project, created with one `POST /v1/tasks` (one idempotency key, so a retry cannot make
// two), and its text becomes a mention of the new task: `- [ ] [Send invoice](tumnis://
// task/<id>)`. The item is found again after the request by its text, in case the note
// changed meanwhile.
import type { Editor } from "@tiptap/core";
import type { Node as PmNode } from "@tiptap/pm/model";

import { zTaskOut } from "../api/zod.gen";
import { apiWrite } from "../lib/fetch";
import { parseTumnisHref, tumnisHref } from "./links";

interface Item {
  /** Where the item's first paragraph starts. */
  paragraphPos: number;
  paragraph: PmNode;
}

function itemAtCursor(editor: Editor): Item | null {
  const { $from } = editor.state.selection;
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    const node = $from.node(depth);
    if (node.type.name !== "taskItem") continue;
    const paragraph = node.firstChild;
    if (paragraph?.type.name !== "paragraph") return null;
    return { paragraphPos: $from.before(depth) + 1, paragraph };
  }
  return null;
}

/** The item's task id when its whole text is already a task mention. */
function linkedTask(paragraph: PmNode): string | null {
  if (paragraph.childCount !== 1) return null;
  const href = paragraph.firstChild?.marks.find((m) => m.type.name === "link")
    ?.attrs.href as unknown;
  if (typeof href !== "string") return null;
  const parsed = parseTumnisHref(href);
  return parsed?.kind === "task" ? parsed.id : null;
}

/** The unlinked checklist item with this text nearest to `near`. */
function findItem(editor: Editor, title: string, near: number): Item | null {
  let best: Item | null = null;
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name !== "taskItem") return true;
    const paragraph = node.firstChild;
    if (
      paragraph?.type.name === "paragraph" &&
      paragraph.textContent.trim() === title &&
      linkedTask(paragraph) === null
    ) {
      const candidate = { paragraphPos: pos + 1, paragraph };
      if (
        best === null ||
        Math.abs(candidate.paragraphPos - near) <
          Math.abs(best.paragraphPos - near)
      ) {
        best = candidate;
      }
    }
    return true;
  });
  return best;
}

/** Creates the task for the checklist item under the cursor; its id, or null when the
 * cursor is not in an item with text. */
export async function makeTask(
  editor: Editor,
  opts: { projectId: string; idempotencyKey?: string },
): Promise<string | null> {
  const item = itemAtCursor(editor);
  if (!item) return null;
  const existing = linkedTask(item.paragraph);
  if (existing) return existing;
  const title = item.paragraph.textContent.trim();
  if (title === "") return null;

  const task = await apiWrite({
    kind: "create",
    method: "POST",
    path: "/tasks",
    body: { project_id: opts.projectId, title },
    idempotencyKey: opts.idempotencyKey ?? crypto.randomUUID(),
    schema: zTaskOut,
  });

  const target = findItem(editor, title, item.paragraphPos);
  if (target && !editor.isDestroyed) {
    const from = target.paragraphPos + 1;
    editor
      .chain()
      .insertContentAt({ from, to: from + target.paragraph.content.size }, [
        {
          type: "text",
          text: title,
          marks: [
            { type: "link", attrs: { href: tumnisHref("task", task.id) } },
          ],
        },
      ])
      .unsetMark("link")
      .run();
  }
  return task.id;
}
