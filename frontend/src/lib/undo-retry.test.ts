// Session undo keeps an entry it may still undo (P0-24, UX 9): a refusal (409) or a task
// that is gone (404) drops the entry, a success drops it, and any other failure (a 5xx, a
// lost connection) keeps it for the Undo button or Mod+Z to try again.
import { http, HttpResponse } from "msw";
import { beforeEach, expect, test } from "vitest";

import { makeProblem } from "../test/factories";
import { server } from "../test/msw/server";
import { undo, undoStore, type UndoEntry } from "./undo";

const ENTRY: UndoEntry = {
  changeId: "01900000-0000-7000-8000-00000000c0de",
  label: "Marked done",
  taskId: "01900000-0000-7000-8000-00000000cafe",
  afterVersion: 3,
  at: 0,
};

function answer(status: number, code: string) {
  server.use(
    http.post(`/v1/tasks/${ENTRY.taskId}/undo`, () =>
      HttpResponse.json(makeProblem({ status, code, title: code }), {
        status,
        headers: { "Content-Type": "application/problem+json" },
      }),
    ),
  );
}

function kept(): boolean {
  return undoStore
    .getSnapshot()
    .context.entries.some((e) => e.changeId === ENTRY.changeId);
}

beforeEach(() => {
  for (const entry of undoStore.getSnapshot().context.entries) {
    undoStore.trigger.drop({ changeId: entry.changeId });
  }
  undoStore.trigger.push({ entry: ENTRY });
});

test("[P0-24][UX 9] a failed undo keeps its entry for a retry", async () => {
  answer(503, "unavailable");
  await expect(undo(ENTRY)).rejects.toThrow();
  expect(kept()).toBe(true);
});

test("[P0-24][UX 9] a refused undo drops its entry", async () => {
  answer(409, "already_undone");
  await expect(undo(ENTRY)).rejects.toThrow();
  expect(kept()).toBe(false);
});

test("[P0-24][UX 9] an undo of a task that is gone drops its entry", async () => {
  answer(404, "not_found");
  await expect(undo(ENTRY)).rejects.toThrow();
  expect(kept()).toBe(false);
});
