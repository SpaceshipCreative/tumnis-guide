// Schedule reads every page of the project's recurring tasks (P0-24, FR-2.7):
// `GET /v1/recurrence?project_id=` answers a `Page[RecurrenceOut]`, so the rail follows
// `next_cursor` until it runs out; a route that is not there reads as nothing repeating.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProblem, makeProject } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

function rule(projectId: string, title: string) {
  return {
    id: crypto.randomUUID(),
    project_id: projectId,
    title,
    preset: "weekly",
    cron: null,
    weekday: 0,
    month_day: null,
    due_time: "09:00:00",
    latest_task_id: crypto.randomUUID(),
    latest_occurrence_on: "2026-03-09",
    next_due_at: "2026-03-16T13:00:00Z",
  };
}

async function openRail(projectId: string) {
  const { user } = await renderRoute(`/projects/${projectId}?view=tasks`, {
    viewport: "laptop",
  });
  const rail = await screen.findByRole("complementary", { name: "Context" });
  return { user, rail };
}

test("[P0-24][FR-2.7] Schedule lists recurring tasks from every page", async () => {
  const project = makeProject({ name: "Acme site" });
  const fake = new ProjectFake({ project, tasks: [] });
  server.resetHandlers();
  server.use(...fake.handlers);
  const first = rule(project.id, "Weekly review");
  const second = rule(project.id, "Invoice the client");
  const cursors: (string | null)[] = [];
  server.use(
    http.get("/v1/recurrence", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      cursors.push(cursor);
      return cursor === "page-2"
        ? HttpResponse.json({ items: [second], next_cursor: null })
        : HttpResponse.json({ items: [first], next_cursor: "page-2" });
    }),
  );

  const { user, rail } = await openRail(project.id);
  const schedule = within(rail).getByRole("button", { name: /^Schedule/ });
  await waitFor(() => {
    expect(schedule).toHaveTextContent("2 recurring tasks");
  });
  await user.click(schedule);
  expect(
    within(rail).getByRole("button", { name: /Weekly review/ }),
  ).toBeVisible();
  expect(
    within(rail).getByRole("button", { name: /Invoice the client/ }),
  ).toBeVisible();
  expect(cursors).toEqual([null, "page-2"]);
});

test("[P0-24][FR-2.7] Schedule reads a missing recurrence route as none", async () => {
  const project = makeProject({ name: "Acme site" });
  const fake = new ProjectFake({ project, tasks: [] });
  server.resetHandlers();
  server.use(...fake.handlers);
  server.use(
    http.get("/v1/recurrence", () =>
      HttpResponse.json(
        makeProblem({ status: 404, code: "not_found", title: "Not found" }),
        {
          status: 404,
          headers: { "Content-Type": "application/problem+json" },
        },
      ),
    ),
  );

  const { rail } = await openRail(project.id);
  const schedule = within(rail).getByRole("button", { name: /^Schedule/ });
  await waitFor(() => {
    expect(schedule).toHaveTextContent("No recurring tasks");
  });
});
