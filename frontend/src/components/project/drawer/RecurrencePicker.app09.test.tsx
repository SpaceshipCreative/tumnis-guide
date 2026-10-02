// The task drawer's repeat (P0-24, FR-3.5). APP-09 (application test): every drawer open
// asked `GET /v1/tasks/{id}/recurrence`, which answers 404 for a task that does not repeat
// (P0-19, locked), so each open logged a failed request. The drawer now reads the project's
// recurring tasks (the Schedule rail's list) and asks for a task's own rule only when the
// task is one. Every fixture is synthetic.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake, type RecurrenceStub } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

afterEach(() => {
  server.events.removeAllListeners("request:start");
  server.events.removeAllListeners("response:mocked");
});

/** The task-rule reads as they start, and the project list reads once answered. */
function recurrenceReads(): { taskRule: string[]; list: number } {
  const seen = { taskRule: [] as string[], list: 0 };
  server.events.on("request:start", ({ request }) => {
    const path = new URL(request.url).pathname;
    if (request.method !== "GET") return;
    if (/^\/v1\/tasks\/[^/]+\/recurrence$/.test(path)) seen.taskRule.push(path);
  });
  server.events.on("response:mocked", ({ request }) => {
    const path = new URL(request.url).pathname;
    if (request.method === "GET" && path === "/v1/recurrence") seen.list += 1;
  });
  return seen;
}

test("[P0-24][FR-3.5] APP-09 a task that does not repeat opens without asking for its rule", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send the proposal",
    status: "backlog",
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  server.use(...fake.handlers);
  const reads = recurrenceReads();

  const { unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "laptop" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Send the proposal",
  });
  await waitFor(() => {
    expect(reads.list).toBeGreaterThan(0);
  });
  // Give the answered list a moment to reach the drawer, so a rule read it set off
  // would be counted below.
  await new Promise((resolve) => setTimeout(resolve, 100));
  expect(within(drawer).getByText("Does not repeat")).toBeVisible();
  expect(within(drawer).getByRole("combobox", { name: "Repeats" })).toHaveValue(
    "never",
  );
  expect(reads.taskRule).toEqual([]);
  unmount();
});

test("[P0-24][FR-3.5] APP-09 a repeating task still shows its rule", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send the weekly report",
    status: "backlog",
  });
  const rule: RecurrenceStub = {
    id: crypto.randomUUID(),
    task_id: task.id,
    title: task.title,
    preset: "weekly",
    cron: null,
    weekday: 0,
    month_day: null,
    due_time: "09:00:00",
    next_due_at: null,
    version: 3,
  };
  const fake = new ProjectFake({ project, tasks: [task], recurrences: [rule] });
  server.use(...fake.handlers);
  const reads = recurrenceReads();

  const { unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "phone" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Send the weekly report",
  });
  expect(
    await within(drawer).findByText("Repeats weekly on Monday at 09:00"),
  ).toBeVisible();
  expect(reads.taskRule).toEqual([`/v1/tasks/${task.id}/recurrence`]);
  unmount();
});

test("[P0-24][FR-3.5] APP-09 when the project's list fails, the drawer asks for the task's rule", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send the weekly report",
    status: "backlog",
  });
  const rule: RecurrenceStub = {
    id: crypto.randomUUID(),
    task_id: task.id,
    title: task.title,
    preset: "weekly",
    cron: null,
    weekday: 0,
    month_day: null,
    due_time: "09:00:00",
    next_due_at: null,
    version: 3,
  };
  const fake = new ProjectFake({ project, tasks: [task], recurrences: [rule] });
  server.use(...fake.handlers);
  server.use(
    http.get("/v1/recurrence", () =>
      HttpResponse.json({ title: "Unavailable" }, { status: 503 }),
    ),
  );

  const { unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "laptop" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Send the weekly report",
  });
  expect(
    await within(drawer).findByText(
      "Repeats weekly on Monday at 09:00",
      {},
      {
        timeout: 5000,
      },
    ),
  ).toBeVisible();
  unmount();
});

test("[P0-24][FR-3.5] APP-09 a failed refresh of a loaded list still answers from that list", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send the proposal",
    status: "backlog",
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  server.use(...fake.handlers);
  const reads = recurrenceReads();

  const { queryClient, unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "laptop" },
  );
  await screen.findByRole("dialog", { name: "Send the proposal" });
  await waitFor(() => {
    expect(reads.list).toBeGreaterThan(0);
  });

  server.use(
    http.get("/v1/recurrence", () =>
      HttpResponse.json({ title: "Unavailable" }, { status: 503 }),
    ),
  );
  await queryClient.refetchQueries({
    predicate: (query) =>
      JSON.stringify(query.queryKey).includes("tasksListRecurrence"),
  });
  // Give a rule read the failed refresh set off time to start.
  await new Promise((resolve) => setTimeout(resolve, 100));
  expect(reads.taskRule).toEqual([]);
  unmount();
});
