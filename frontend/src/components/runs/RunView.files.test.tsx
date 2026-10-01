// The run view's files and clock (P2-04, FR-5.5): the files the agent touched (its
// `file` events) in their own region, each once, and an elapsed time that counts each
// second through a run's first minute (then in five-second steps, T-P2-04-15).
import { act, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";

import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { RunView } from "./RunView";

const RUN_ID = "01950000-0000-7000-8000-000000000c01";
const STARTED = "2026-03-09T12:00:00Z";

function event(seq: number, kind: string, text: string) {
  return {
    seq,
    message_id: `01950000-0000-7000-8000-0000000c10${String(seq).padStart(2, "0")}`,
    kind,
    payload: { seq, kind, text },
    at: STARTED,
  };
}

function useRun() {
  server.use(
    http.get("*/v1/runs/:id", () =>
      HttpResponse.json({
        id: RUN_ID,
        task_id: null,
        project_id: null,
        kind: "task",
        status: "running",
        stop_reason: null,
        rerun_of: null,
        started_at: STARTED,
        finished_at: null,
        created_at: STARTED,
      }),
    ),
    http.get("*/v1/runs/:id/events", ({ request }) => {
      const after = Number(
        new URL(request.url).searchParams.get("after_seq") ?? "0",
      );
      const items = [
        event(1, "log", "Reading the footer component"),
        event(2, "file", "src/footer.tsx"),
        event(3, "tool_call", "git.checkout"),
        event(4, "file", "src/footer.tsx"),
        event(5, "file", "src/header.tsx"),
      ].filter((e) => e.seq > after);
      return HttpResponse.json({
        items,
        next_after_seq: items.at(-1)?.seq ?? after,
      });
    }),
  );
}

afterEach(() => {
  vi.useRealTimers();
});

test.fails(
  "[P2-04][FR-5.5] the run view lists the files touched once each",
  async () => {
    useRun();
    renderWithProviders(<RunView runId={RUN_ID} />);

    const files = await screen.findByRole("region", { name: "Files touched" });
    expect(await within(files).findByText("src/header.tsx")).toBeVisible();
    expect(
      within(files)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["src/footer.tsx", "src/header.tsx"]);
  },
);

test.fails(
  "[P2-04][FR-5.5] elapsed counts each second in a run's first minute",
  async () => {
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["Date", "setInterval", "setTimeout"],
    });
    vi.setSystemTime(new Date("2026-03-09T12:00:12Z"));
    useRun();
    renderWithProviders(<RunView runId={RUN_ID} />);

    const elapsed = await screen.findByTestId("run-elapsed");
    expect(elapsed).toHaveAccessibleName("Elapsed");
    const shown = elapsed.textContent;
    expect(shown).toMatch(/^0:1[2-4]$/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(elapsed.textContent).not.toBe(shown);
    expect(elapsed.textContent).toMatch(/^0:1[4-6]$/);
  },
);
