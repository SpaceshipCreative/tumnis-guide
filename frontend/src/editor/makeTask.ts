// Make task from a checklist item (P1-17, FR-15.1). Spec stub: implemented next.
import type { Editor } from "@tiptap/core";

export function makeTask(
  editor: Editor,
  opts: { projectId: string },
): Promise<string | null> {
  return Promise.reject(
    new Error(
      `not implemented: ${String(editor.isEditable)} ${opts.projectId}`,
    ),
  );
}
