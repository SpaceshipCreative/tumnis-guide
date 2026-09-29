// Session undo (P0-24, UX 9, R-09): the session's list of change ids, in memory only; the
// before-state lives on the server, so undo works for any task write, AI ones included.
// Every task write answers its `change_id`; the mutation hooks `remember` it with the
// version after the write, and `undo` sends both back.
import { createStore } from "@xstate/store";
import type * as z from "zod";

import { zTaskOut } from "../api/zod.gen";
import { apiWrite } from "./fetch";

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

/** Keeps a write's change for undo (a response without `change_id` has nothing to undo). */
export function remember(
  task: { id: string; version: number; change_id?: string | null },
  label: string,
): void {
  if (!task.change_id) return;
  undoStore.trigger.push({
    entry: {
      changeId: task.change_id,
      label,
      taskId: task.id,
      afterVersion: task.version,
      at: Date.now(),
    },
  });
}

/**
 * `POST /v1/tasks/{taskId}/undo {change_id, version: afterVersion}`; answers the restored
 * task. The entry is dropped either way: a refused undo (409 `already_undone`, or
 * `stale_version` when the task changed since) cannot succeed later.
 */
export async function undo(
  entry: UndoEntry,
): Promise<z.output<typeof zTaskOut>> {
  try {
    return await apiWrite({
      kind: "update",
      method: "POST",
      path: `/tasks/${entry.taskId}/undo`,
      body: { change_id: entry.changeId },
      version: entry.afterVersion,
      idempotencyKey: crypto.randomUUID(),
      schema: zTaskOut,
    });
  } finally {
    undoStore.trigger.drop({ changeId: entry.changeId });
  }
}
