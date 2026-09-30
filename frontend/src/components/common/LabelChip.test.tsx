// The label chip (P1-07, FR-3.3, FR-4.2, UX 2, UX 9): pending while Jev decides, then
// confirmed with a one-line reason when the `/ws` message refetches the task; a
// low-confidence label shows as a suggestion; one click and one pick override it with
// one PATCH; an AI label can be undone for the session.
import { act, screen, waitFor, within } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { connectLive } from "../../lib/ws";
import { FakeSocket } from "../../test/fakeSocket";
import { makeTask } from "../../test/factories";
import { server } from "../../test/msw/server";
import { createTestQueryClient, renderWithProviders } from "../../test/render";
import { undoStore } from "../../lib/undo";
import { TaskLabelChip } from "./LabelChip";

const REASON = "Part automatable, part needs you: needs your decision";

type Json = Record<string, unknown>;

/** A `TaskOut` as the API answers it, with these fields. */
function taskJson(overrides: Json): Json {
  return { ...makeTask(), ...overrides };
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.reset();
  for (const entry of undoStore.getSnapshot().context.entries) {
    undoStore.trigger.drop({ changeId: entry.changeId });
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("[P1-07][FR-3.3] T-P1-07-10 pending then confirmed via ws", async () => {
  const pending = taskJson({
    title: "Send Acme the March invoice",
    label: null,
    label_source: null,
    label_reason: null,
    label_suggestion: null,
    version: 1,
  });
  let current = pending;
  server.use(http.get("/v1/tasks/:id", () => HttpResponse.json(current)));
  const queryClient = createTestQueryClient();
  const stop = connectLive(queryClient, "ws://localhost/ws");
  FakeSocket.latest().open();
  renderWithProviders(<TaskLabelChip taskId={String(pending.id)} />, {
    queryClient,
  });

  const chip = await screen.findByTestId("label-chip");
  await waitFor(() => {
    expect(chip).toHaveTextContent("Labeling…");
  });
  expect(chip).toHaveAttribute("aria-busy", "true");
  expect(chip).toHaveAttribute("data-state", "pending");

  current = {
    ...pending,
    label: "hybrid",
    label_source: "jev",
    label_reason: REASON,
    version: 2,
  };
  act(() => {
    FakeSocket.latest().receive({ entity: "task", id: String(pending.id) });
  });

  await waitFor(() => {
    expect(screen.getByTestId("label-chip")).toHaveTextContent("Hybrid");
  });
  const confirmed = screen.getByTestId("label-chip");
  expect(confirmed).toHaveAttribute("data-state", "confirmed");
  expect(confirmed).not.toHaveAttribute("aria-busy", "true");
  const describedBy = confirmed.getAttribute("aria-describedby") ?? "";
  expect(describedBy).not.toBe("");
  const reason = document.getElementById(describedBy);
  expect(reason).toHaveTextContent(REASON);
  expect(reason?.textContent).not.toContain("\n");
  stop();
});

test("[P1-07][UX 2] T-P1-07-11 suggested label shows as suggestion", async () => {
  const task = taskJson({
    label: null,
    label_source: null,
    label_reason: "Part automatable, part needs you: needs your judgment",
    label_suggestion: "hybrid",
  });
  server.use(http.get("/v1/tasks/:id", () => HttpResponse.json(task)));
  renderWithProviders(<TaskLabelChip taskId={String(task.id)} />);

  const chip = await screen.findByTestId("label-chip");
  await waitFor(() => {
    expect(chip).toHaveTextContent("Suggested: Hybrid");
  });
  expect(chip).toHaveAttribute("data-state", "suggested");
  expect(chip.className).toMatch(/border-dashed/);
  expect(chip).toHaveAccessibleName(/^Suggested/);
  expect(chip).not.toHaveAttribute("aria-busy", "true");
});

test("[P1-07][FR-4.2] T-P1-07-12 one click override sends one PATCH", async () => {
  const task = taskJson({
    label: "hybrid",
    label_source: "jev",
    label_reason: REASON,
    label_suggestion: null,
    version: 2,
  });
  const patches: { body: unknown; key: string | null }[] = [];
  let answer: "ok" | "conflict" = "ok";
  server.use(
    http.get("/v1/tasks/:id", () => HttpResponse.json(task)),
    http.patch("/v1/tasks/:id", async ({ request }) => {
      patches.push({
        body: await request.json(),
        key: request.headers.get("Idempotency-Key"),
      });
      await delay(50);
      if (answer === "conflict") {
        return HttpResponse.json(
          {
            type: "about:blank",
            title: "Stale version",
            status: 409,
            code: "stale_version",
            current: task,
          },
          {
            status: 409,
            headers: { "Content-Type": "application/problem+json" },
          },
        );
      }
      return HttpResponse.json({
        ...task,
        label: "human",
        label_source: "user",
        version: 3,
      });
    }),
  );
  const { user } = renderWithProviders(
    <TaskLabelChip taskId={String(task.id)} />,
  );
  const chip = await screen.findByTestId("label-chip");
  await waitFor(() => {
    expect(chip).toHaveTextContent("Hybrid");
  });

  await user.click(chip);
  const menu = screen.getByRole("listbox", { name: "Label" });
  expect(within(menu).getAllByRole("option")).toHaveLength(3);
  await user.click(within(menu).getByRole("option", { name: "Human" }));

  expect(screen.getByTestId("label-chip")).toHaveTextContent("Human");
  await waitFor(() => {
    expect(patches).toHaveLength(1);
  });
  expect(patches[0]?.body).toEqual({ label: "human", version: 2 });
  expect(patches[0]?.key).toBeTruthy();
  await waitFor(() => {
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  // A 409 puts the server's record back.
  answer = "conflict";
  await user.click(screen.getByTestId("label-chip"));
  await user.keyboard("2");
  expect(screen.getByTestId("label-chip")).toHaveTextContent("AI");
  await waitFor(() => {
    expect(screen.getByTestId("label-chip")).toHaveTextContent("Hybrid");
  });
  expect(patches).toHaveLength(2);
});

test("[P1-07][UX 9] T-P1-07-13 AI label can be undone for the session", async () => {
  const changeId = crypto.randomUUID();
  const labelled = taskJson({
    label: "hybrid",
    label_source: "jev",
    label_reason: REASON,
    label_suggestion: null,
    version: 2,
    change_id: changeId,
  });
  let current = labelled;
  const undos: unknown[] = [];
  server.use(
    http.get("/v1/tasks/:id", () => HttpResponse.json(current)),
    http.post("/v1/tasks/:id/undo", async ({ request }) => {
      undos.push(await request.json());
      current = {
        ...labelled,
        label: null,
        label_source: null,
        version: 3,
        change_id: crypto.randomUUID(),
      };
      return HttpResponse.json(current);
    }),
  );
  const { user } = renderWithProviders(
    <TaskLabelChip taskId={String(labelled.id)} />,
  );
  await waitFor(() => {
    expect(screen.getByTestId("label-chip")).toHaveTextContent("Hybrid");
  });

  await user.click(screen.getByRole("button", { name: "Undo AI label" }));

  await waitFor(() => {
    expect(screen.getByTestId("label-chip")).toHaveTextContent("Labeling…");
  });
  expect(undos).toEqual([{ change_id: changeId, version: 2 }]);
  expect(screen.queryByRole("button", { name: "Undo AI label" })).toBeNull();
});
