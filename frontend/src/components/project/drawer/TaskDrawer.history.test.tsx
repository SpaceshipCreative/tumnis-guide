// The task drawer's label chip and History (A1.1, coordinator decision 83): the drawer
// shows the task's label, and a History list, newest first, says which fields each write
// changed and who made it ("you" for the person's own).
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

test("[JOURNEYS-B][A1.1] T-JOURNEYS-B-04 the drawer shows the label and the task's history", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send Acme the March invoice",
    status: "backlog",
    label: "human",
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  const change = (fields: string[], by_you: boolean, actor: string) => ({
    change_id: crypto.randomUUID(),
    at: "2026-03-09T14:00:00Z",
    actor,
    by_you,
    fields,
    undone: false,
  });
  fake.history.set(task.id, [
    change(["label"], true, "user:me"),
    change(["acceptance_criteria", "estimate_minutes"], false, "task_token:x"),
    change(["label"], false, "system"),
    change(["created"], true, "user:me"),
  ]);
  server.use(...fake.handlers);

  await renderRoute(`/projects/${project.id}?task=${task.id}`, {
    viewport: "laptop",
  });
  const drawer = await screen.findByRole("dialog", {
    name: "Send Acme the March invoice",
  });
  expect(within(drawer).getByTestId("label-chip")).toHaveTextContent("Human");

  const history = within(drawer).getByRole("region", { name: "History" });
  const entries = await within(history).findAllByRole("listitem");
  expect(entries).toHaveLength(4);
  expect(entries[0]).toHaveTextContent(/Label/);
  expect(entries[0]).toHaveTextContent(/\byou\b/);
  expect(entries[1]).toHaveTextContent(/Acceptance criteria, Estimate/);
  expect(entries[1]).toHaveTextContent(/the agent/);
  expect(entries[2]).toHaveTextContent(/Label/);
  expect(entries[2]).not.toHaveTextContent(/\byou\b/);
  expect(entries[3]).toHaveTextContent(/Created/);
});

test("[JOURNEYS-B][A1.1] T-JOURNEYS-B-05 older history loads a page at a time", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send Acme the March invoice",
    status: "backlog",
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  const entry = (fields: string[]) => ({
    change_id: crypto.randomUUID(),
    at: "2026-03-09T14:00:00Z",
    actor: "user:me",
    by_you: true,
    fields,
    undone: false,
  });
  const cursors: (string | null)[] = [];
  server.use(...fake.handlers);
  server.use(
    http.get("/v1/tasks/:id/history", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      cursors.push(cursor);
      return HttpResponse.json(
        cursor === null
          ? { items: [entry(["title"])], next_cursor: "page-2" }
          : { items: [entry(["created"])], next_cursor: null },
      );
    }),
  );

  const { user } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "laptop" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Send Acme the March invoice",
  });
  const history = within(drawer).getByRole("region", { name: "History" });
  expect(await within(history).findAllByRole("listitem")).toHaveLength(1);

  await user.click(within(history).getByRole("button", { name: "Load more" }));
  const entries = await within(history).findAllByRole("listitem");
  expect(entries).toHaveLength(2);
  expect(entries[1]).toHaveTextContent(/Created/);
  expect(cursors).toEqual([null, "page-2"]);
  expect(
    within(history).queryByRole("button", { name: "Load more" }),
  ).not.toBeInTheDocument();
});
