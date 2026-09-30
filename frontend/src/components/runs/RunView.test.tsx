// The run view (P2-04, FR-5.5, UX 10): a run's log as it streams, read by cursor pages of
// `GET /v1/runs/{id}/events?after_seq=&limit=`, the elapsed time ticking, and a Stop
// button that asks the server to cancel the run (`POST /v1/runs/{id}/cancel`, 202).
import { act, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";

import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";

const RUN_ID = "01950000-0000-7000-8000-000000000901";
const TASK_ID = "01950000-0000-7000-8000-000000000902";
const PROJECT_ID = "01950000-0000-7000-8000-000000000903";
const STARTED = "2026-03-09T12:00:00Z";

interface RunViewModule {
  RunView: (props: { runId: string }) => React.JSX.Element;
}

// Loaded at run time, so this file compiles before RunView.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

interface EventFixture {
  seq: number;
  message_id: string;
  kind: string;
  payload: Record<string, unknown>;
  at: string;
}

function line(seq: number, text: string, kind = "log"): EventFixture {
  return {
    seq,
    message_id: `01950000-0000-7000-8000-0000000010${String(seq).padStart(2, "0")}`,
    kind,
    payload: { seq, kind, text },
    at: STARTED,
  };
}

function run(status = "running") {
  return {
    id: RUN_ID,
    task_id: TASK_ID,
    project_id: PROJECT_ID,
    kind: "task",
    status,
    started_at: STARTED,
    finished_at: null,
    stop_reason: null,
    rerun_of: null,
    created_at: STARTED,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

test.fails(
  "[P2-04][FR-5.5] streams lines in order and Stop cancels",
  async () => {
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["Date", "setInterval", "setTimeout"],
    });
    vi.setSystemTime(new Date("2026-03-09T12:01:05Z"));
    const { RunView } = await load<RunViewModule>("./RunView");

    const stored = [
      line(1, "Checking out fix-footer"),
      line(2, "git.checkout fix-footer", "tool_call"),
      line(3, "Editing src/footer.tsx"),
    ];
    let status = "running";
    const recorder = new Recorder();
    server.use(
      http.get("*/v1/runs/:id", () => HttpResponse.json(run(status))),
      http.get("*/v1/runs/:id/events", async ({ request }) => {
        await recorder.record(request);
        const url = new URL(request.url);
        const after = Number(url.searchParams.get("after_seq") ?? "0");
        const limit = Number(url.searchParams.get("limit") ?? "100");
        const items = stored.filter((e) => e.seq > after).slice(0, limit);
        return HttpResponse.json({
          items,
          next_after_seq: items.at(-1)?.seq ?? after,
        });
      }),
      http.post("*/v1/runs/:id/cancel", async ({ request }) => {
        await recorder.record(request);
        status = "cancelled";
        return HttpResponse.json(
          { run_id: RUN_ID, status: "running" },
          { status: 202 },
        );
      }),
    );
    const { user } = renderWithProviders(<RunView runId={RUN_ID} />);

    // The log, in `seq` order, from the pages it fetched.
    const log = await screen.findByRole("log", { name: "Run log" });
    await waitFor(() => {
      expect(within(log).getAllByRole("listitem")).toHaveLength(3);
    });
    expect(
      within(log)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual([
      expect.stringContaining("Checking out fix-footer"),
      expect.stringContaining("git.checkout fix-footer"),
      expect.stringContaining("Editing src/footer.tsx"),
    ]);
    // Every page after the first asks from the last seq it holds (cursor paging).
    const afters = recorder.sent
      .filter((s) => s.method === "GET")
      .map((s) => new URLSearchParams(s.search).get("after_seq"));
    expect(afters.filter((a) => a !== null).map(Number)).toEqual(
      [...afters.filter((a) => a !== null).map(Number)].sort((a, b) => a - b),
    );

    // A new line arrives: appended after the others, never re-fetched from the start.
    stored.push(line(4, "Running the tests"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await waitFor(() => {
      expect(within(log).getAllByRole("listitem")).toHaveLength(4);
    });
    expect(within(log).getAllByRole("listitem").at(-1)).toHaveTextContent(
      "Running the tests",
    );

    // Elapsed time ticks on the clock: 1:10 after the first five seconds, then 1:15.
    const elapsed = screen.getByLabelText("Elapsed");
    expect(elapsed).toHaveTextContent("1:10");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(elapsed).toHaveTextContent("1:15");

    // Stop: one cancel request for the run, with an Idempotency-Key.
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => {
      expect(recorder.writes()).toEqual([`POST /v1/runs/${RUN_ID}/cancel`]);
    });
    const cancel = recorder.sent.find((s) => s.method === "POST");
    expect(cancel?.idempotencyKey).toBeTruthy();
  },
);
