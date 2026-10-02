// The task drawer's repeat against the project's recurring tasks (P0-24, FR-3.5; CodeRabbit
// on #158). The drawer reads a task's rule only while the project's list names the task
// (APP-09), so a rule it once read stayed in the cache, and on screen, after another
// client stopped the repeat and a refreshed list dropped the task. A list newer than the
// cached rule now has the last word; a rule this drawer saved still shows until the list
// catches up. Every fixture is synthetic.
import { screen, waitFor, within } from "@testing-library/react";
import { delay, http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake, type RecurrenceStub } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

function weekly(taskId: string, title: string): RecurrenceStub {
  return {
    id: crypto.randomUUID(),
    task_id: taskId,
    title,
    preset: "weekly",
    cron: null,
    weekday: 0,
    month_day: null,
    due_time: "09:00:00",
    next_due_at: null,
    version: 3,
  };
}

test("[P0-24][FR-3.5] a repeat stopped elsewhere leaves the drawer once the list drops the task", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send the weekly report",
    status: "backlog",
  });
  const fake = new ProjectFake({
    project,
    tasks: [task],
    recurrences: [weekly(task.id, task.title)],
  });
  server.use(...fake.handlers);

  const { queryClient, unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "laptop" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Send the weekly report",
  });
  expect(
    await within(drawer).findByText("Repeats weekly on Monday at 09:00"),
  ).toBeVisible();

  fake.recurrences = []; // another client stopped the repeat
  await queryClient.refetchQueries({
    predicate: (query) =>
      JSON.stringify(query.queryKey).includes("tasksListRecurrence"),
  });

  await waitFor(() => {
    expect(within(drawer).getByText("Does not repeat")).toBeVisible();
  });
  expect(within(drawer).getByRole("combobox", { name: "Repeats" })).toHaveValue(
    "never",
  );
  unmount();
});

test("[P0-24][FR-3.5] a repeat saved here shows before the list catches up", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send the weekly report",
    status: "backlog",
    version: 7,
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  server.use(...fake.handlers);

  const { user, unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "phone" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Send the weekly report",
  });
  await within(drawer).findByText("Does not repeat");
  // From now on the list answers late (then as the server holds it), so the save lands
  // before it.
  let listAnswered = 0;
  server.use(
    http.get("/v1/recurrence", async () => {
      await delay(400);
      listAnswered += 1;
    }),
  );

  await user.selectOptions(
    within(drawer).getByRole("combobox", { name: "Repeats" }),
    "daily",
  );
  await user.click(within(drawer).getByRole("button", { name: "Save repeat" }));

  expect(
    await within(drawer).findByText("Repeats daily at 09:00"),
  ).toBeVisible();
  expect(listAnswered).toBe(0);
  await waitFor(() => {
    expect(listAnswered).toBeGreaterThan(0);
  });
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(within(drawer).getByText("Repeats daily at 09:00")).toBeVisible();
  unmount();
});

test("[P0-24][FR-3.5] a list read started before a save does not hide the saved repeat", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send the weekly report",
    status: "backlog",
    version: 7,
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  server.use(...fake.handlers);

  const { queryClient, user, unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "laptop" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Send the weekly report",
  });
  await within(drawer).findByText("Does not repeat");
  // The next list read is an old snapshot (no repeat yet) that answers after the save;
  // later reads answer as the server holds it.
  let stale = true;
  let answeredAfterSave: boolean | undefined;
  server.use(
    http.get("/v1/recurrence", async () => {
      if (!stale) return;
      stale = false;
      await delay(400);
      answeredAfterSave =
        fake.sent("PUT", `/v1/tasks/${task.id}/recurrence`).length > 0;
      return HttpResponse.json({ items: [], next_cursor: null });
    }),
  );
  void queryClient.refetchQueries({
    predicate: (query) =>
      JSON.stringify(query.queryKey).includes("tasksListRecurrence"),
  });

  await user.selectOptions(
    within(drawer).getByRole("combobox", { name: "Repeats" }),
    "daily",
  );
  await user.click(within(drawer).getByRole("button", { name: "Save repeat" }));
  expect(
    await within(drawer).findByText("Repeats daily at 09:00"),
  ).toBeVisible();

  // Past the old snapshot's answer, which came after the save: the save's refresh of the
  // task views replaced that read (TanStack Query's cancelRefetch), so the saved repeat
  // still shows.
  await new Promise((resolve) => setTimeout(resolve, 600));
  expect(answeredAfterSave).toBe(true);
  expect(within(drawer).getByText("Repeats daily at 09:00")).toBeVisible();
  expect(within(drawer).getByRole("combobox", { name: "Repeats" })).toHaveValue(
    "daily",
  );
  unmount();
});
