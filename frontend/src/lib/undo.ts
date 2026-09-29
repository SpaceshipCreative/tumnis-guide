// Session undo (P0-24, UX 9, R-09): the session's list of change ids, in memory only; the
// before-state lives on the server, so undo works for any task write, AI ones included.
import { createStore } from "@xstate/store";

export interface UndoEntry {
  changeId: string;
  label: string;
  taskId: string;
  afterVersion: number;
  at: number;
}

export const UNDO_LIMIT = 50; // (plan default)

export const undoStore = createStore({
  context: { entries: [] as UndoEntry[] },
  on: {
    push: (c, e: { entry: UndoEntry }) => ({
      entries: [...c.entries, e.entry].slice(-UNDO_LIMIT),
    }),
    drop: (c, e: { changeId: string }) => ({
      entries: c.entries.filter((x) => x.changeId !== e.changeId),
    }),
  },
});

/** `POST /v1/tasks/{taskId}/undo {change_id, version: afterVersion}`. */
export function undo(entry: UndoEntry): Promise<void> {
  return Promise.reject(
    new Error(`not implemented: P0-24 (${entry.changeId})`),
  );
}
