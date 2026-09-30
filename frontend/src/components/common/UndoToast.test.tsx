// The Undo toast's refusals and repeats (P0-24, UX 9): an undo of a task that is gone says
// so (the entry is dropped, so "try again" would be wrong), and a second Mod+Z while an
// undo is still on its way undoes the next entry instead of re-sending the same change.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, test } from "vitest";

import { makeProblem, makeTask } from "../../test/factories";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { undoStore, type UndoEntry } from "../../lib/undo";
import { uiStore } from "../../stores/uiStore";
import { UndoToast } from "./UndoToast";

function entry(label: string): UndoEntry {
  return {
    changeId: crypto.randomUUID(),
    label,
    taskId: crypto.randomUUID(),
    afterVersion: 3,
    at: 0,
  };
}

beforeEach(() => {
  for (const e of undoStore.getSnapshot().context.entries) {
    undoStore.trigger.drop({ changeId: e.changeId });
  }
  uiStore.trigger.clearNotice();
});

test("[P0-24][UX 9] an undo of a task that is gone says it cannot be undone", async () => {
  const gone = entry("Marked done");
  undoStore.trigger.push({ entry: gone });
  server.use(
    http.post(`/v1/tasks/${gone.taskId}/undo`, () =>
      HttpResponse.json(
        makeProblem({ status: 404, code: "not_found", title: "Not found" }),
        {
          status: 404,
          headers: { "Content-Type": "application/problem+json" },
        },
      ),
    ),
  );
  const { user } = renderWithProviders(<UndoToast />);

  const toast = await screen.findByRole("status", { name: "Undo" });
  await user.click(within(toast).getByRole("button", { name: "Undo" }));

  await waitFor(() => {
    expect(uiStore.getSnapshot().context.notice).not.toBeNull();
  });
  const notice = uiStore.getSnapshot().context.notice;
  expect(notice).toMatch(/no longer exists/);
  expect(notice).toMatch(/can't be undone/);
});

test("[P0-24][UX 9] Mod+Z while an undo is on its way undoes the next entry", async () => {
  const older = entry("Title changed");
  const latest = entry("Marked done");
  undoStore.trigger.push({ entry: older });
  undoStore.trigger.push({ entry: latest });
  const sent: unknown[] = [];
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    http.post("/v1/tasks/:id/undo", async ({ params, request }) => {
      sent.push(((await request.json()) as { change_id: string }).change_id);
      await gate;
      return HttpResponse.json(
        makeTask({ id: String(params.id), title: "Restored" }),
      );
    }),
  );
  const { user } = renderWithProviders(<UndoToast />);
  await screen.findByRole("status", { name: "Undo" });

  await user.keyboard("{Control>}z{/Control}");
  await waitFor(() => {
    expect(sent).toHaveLength(1);
  });
  await user.keyboard("{Control>}z{/Control}");
  await waitFor(() => {
    expect(sent).toHaveLength(2);
  });
  release();

  expect(sent).toEqual([latest.changeId, older.changeId]);
  await waitFor(() => {
    expect(undoStore.getSnapshot().context.entries).toEqual([]);
  });
});
