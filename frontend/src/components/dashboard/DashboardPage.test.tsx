// The dashboard route (P0-23, UX 1, FR-1.1, FR-1.2): the loader fills the cache before the
// first render, so the page opens with its data and never shows a spinner.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import {
  projectsList,
  reviewCount,
  todayTasks,
} from "../../test/msw/dashboard";
import { server } from "../../test/msw/server";
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
