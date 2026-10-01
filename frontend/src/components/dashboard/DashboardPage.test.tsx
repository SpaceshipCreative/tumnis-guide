// The dashboard route (P0-23, UX 1, FR-1.1, FR-1.2): the loader fills the cache before the
// first render, so the page opens with its data and never shows a spinner.
import { act, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { daySummary, FULL_DAY } from "../../test/msw/closeDay";
import {
  projectsList,
  reviewCount,
  todayTasks,
} from "../../test/msw/dashboard";
import {
  mondayPlan,
  ninetyMinuteIssue,
  planHandlers,
} from "../../test/msw/planning";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";
import { DASHBOARD_READY_MARK } from "./DashboardPage";

test("[P0-23][UX 1][FR-1.1] T-P0-23-08 first render has data, no spinner", async () => {
  const acme = makeProject({ name: "Acme site", health: "blocked" });
  const dogfood = makeProject({ name: "Tumnis dogfood", health: "on_track" });
  const queries: URLSearchParams[] = [];
  server.use(
    projectsList([acme, dogfood]),
    todayTasks(
      [
        makeTask({
          project_id: acme.id,
          title: "Send logo drafts",
          status: "today",
        }),
      ],
      1,
      (query) => queries.push(query),
    ),
    reviewCount(2),
  );

  await renderRoute("/", { viewport: "laptop" });

  // Synchronous queries: the data is there on the first render, not after a fetch.
  expect(screen.getByRole("link", { name: "Acme site" })).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Tumnis dogfood" }),
  ).toBeInTheDocument();
  const today = screen.getByRole("region", { name: "Today" });
  expect(within(today).getByText("Send logo drafts")).toBeInTheDocument();
  expect(within(today).getByText("Acme site")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "2 to review" })).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.queryByText(/loading/i)).toBeNull();

  expect(queries).toHaveLength(1);
  expect(Object.fromEntries(queries[0] ?? [])).toEqual({
    status: "today",
    order: "today",
    limit: "5",
  });
});

test("[P0-23][PERF-2] marks tumnis:dashboard-ready once cards and Today have data", async () => {
  const mark = vi.spyOn(performance, "mark");
  const acme = makeProject({ name: "Acme site" });
  server.use(
    projectsList([acme]),
    todayTasks([makeTask({ project_id: acme.id, status: "today" })]),
  );

  await renderRoute("/");

  await waitFor(() => {
    expect(
      mark.mock.calls.filter(([name]) => name === DASHBOARD_READY_MARK),
    ).toHaveLength(1);
  });
});

test("[P0-23][FR-1.2] a failed Today read says so and the cards still show", async () => {
  const acme = makeProject({ name: "Acme site" });
  server.use(
    projectsList([acme]),
    http.get("/v1/tasks", () =>
      HttpResponse.json(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          code: "not_found",
        },
        { status: 404 },
      ),
    ),
  );

  await renderRoute("/");

  const today = screen.getByRole("region", { name: "Today" });
  expect(
    await within(today).findByText("Today's tasks could not be loaded."),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Acme site" })).toBeInTheDocument();
});

test("[P0-23][FR-1.2] Start sends one status write with the version read", async () => {
  const acme = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: acme.id,
    title: "Send logo drafts",
    status: "today",
    version: 3,
  });
  const writes: { body: unknown; key: string | null }[] = [];
  server.use(
    projectsList([acme]),
    todayTasks([task]),
    http.post(`/v1/tasks/${task.id}/status`, async ({ request }) => {
      writes.push({
        body: await request.json(),
        key: request.headers.get("Idempotency-Key"),
      });
      return HttpResponse.json({ ...task, status: "in_progress", version: 4 });
    }),
  );

  const { user } = await renderRoute("/");
  await user.click(
    screen.getByRole("button", { name: "Start Send logo drafts" }),
  );

  await waitFor(() => {
    expect(writes).toHaveLength(1);
  });
  expect(writes[0]?.body).toEqual({ to: "in_progress", version: 3 });
  expect(writes[0]?.key).toBeTruthy();
});

test("[P0-23][UX 11] the quick-add button opens quick add", async () => {
  const { uiStore } = await import("../../stores/uiStore");
  const { user } = await renderRoute("/", { viewport: "phone" });

  await user.click(screen.getByRole("button", { name: "Quick add" }));

  expect(uiStore.getSnapshot().context.quickAddOpen).toBe(true);
  uiStore.trigger.closeQuickAdd();
});

test("[P1-11][FR-1.2][J6] with a published plan, Today shows its items and the issues their offers", async () => {
  server.use(
    ...planHandlers(
      new Recorder(),
      mondayPlan({ issues: [ninetyMinuteIssue()] }),
    ),
  );
  for (const viewport of ["phone", "laptop"] as const) {
    const { unmount } = await renderRoute("/", { viewport });
    const today = screen.getByRole("region", { name: "Today" });
    expect(within(today).getAllByRole("listitem")).toHaveLength(4);
    expect(within(today).getByText("Send logo drafts")).toBeInTheDocument();
    const offers = screen.getByRole("region", { name: "Doesn't fit today" });
    expect(offers).toHaveTextContent("Write Acme proposal");
    expect(offers).toHaveTextContent("No 90-minute gap today");
    unmount();
  }
});

// Close the day (P1-18, J7): a dashboard button from 16:00 local (the workspace's zone,
// America/New_York by default) and the `?panel=close` search param.
test("[P1-18][J7] Close the day shows from 16:00 local and opens the panel", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  try {
    server.use(daySummary(FULL_DAY));
    // 15:59 in New York: not yet.
    vi.setSystemTime(new Date("2026-03-09T19:59:00Z"));
    const early = await renderRoute("/", { viewport: "laptop" });
    expect(screen.queryByRole("button", { name: "Close the day" })).toBeNull();
    early.unmount();

    // 17:40: the button opens the panel and sets the search param.
    vi.setSystemTime(new Date("2026-03-09T21:40:00Z"));
    for (const viewport of ["phone", "laptop"] as const) {
      const { user, router, unmount } = await renderRoute("/", { viewport });
      await user.click(screen.getByRole("button", { name: "Close the day" }));
      const panel = await screen.findByRole("dialog", {
        name: "Close the day",
      });
      expect(router.state.location.search).toMatchObject({ panel: "close" });
      await within(panel).findAllByRole("region");

      // Done drops the param and the panel.
      await user.click(within(panel).getByRole("button", { name: "Done" }));
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).toBeNull();
      });
      expect(router.state.location.search).not.toHaveProperty("panel");
      // Focus goes back to the button that opened the panel.
      expect(
        screen.getByRole("button", { name: "Close the day" }),
      ).toHaveFocus();
      unmount();
    }
  } finally {
    vi.useRealTimers();
  }
});

test("[P1-18][J7] Close the day appears at 16:00 on an idle dashboard", async () => {
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
  try {
    vi.setSystemTime(new Date("2026-03-09T19:59:30Z")); // 15:59:30 in New York (EDT)
    await renderRoute("/", { viewport: "laptop" });
    expect(screen.queryByRole("button", { name: "Close the day" })).toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(
      screen.getByRole("button", { name: "Close the day" }),
    ).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

test("[P1-18][J7] ?panel=close opens the panel at any hour", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  try {
    vi.setSystemTime(new Date("2026-03-09T14:00:00Z"));
    server.use(daySummary(FULL_DAY));
    await renderRoute("/?panel=close");
    const panel = await screen.findByRole("dialog", { name: "Close the day" });
    expect(
      await within(panel).findByRole("region", { name: "Shipped" }),
    ).toHaveTextContent("Write Acme proposal");
  } finally {
    vi.useRealTimers();
  }
});
