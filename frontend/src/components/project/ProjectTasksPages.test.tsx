// The project page reads every page of its tasks (P0-24, FR-2.2): `GET /v1/tasks` answers
// at most 200 per page, so the Tasks view follows `next_cursor` until it runs out.
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test("[P0-24][FR-2.2] the Tasks view shows tasks from every page", async () => {
  const project = makeProject({ name: "Acme site" });
  const first = makeTask({
    project_id: project.id,
    parent_id: null,
    title: "Send logo drafts",
    status: "backlog",
  });
  const second = makeTask({
    project_id: project.id,
    parent_id: null,
    title: "Book the photo shoot",
    status: "backlog",
  });
  const fake = new ProjectFake({ project, tasks: [first, second] });
  server.resetHandlers();
  server.use(...fake.handlers);
  const cursors: (string | null)[] = [];
  server.use(
    http.get("/v1/tasks", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      cursors.push(cursor);
      return cursor === "page-2"
        ? HttpResponse.json({ items: [second], next_cursor: null, total: 2 })
        : HttpResponse.json({
            items: [first],
            next_cursor: "page-2",
            total: 2,
          });
    }),
  );

  await renderRoute(`/projects/${project.id}?view=tasks`, {
    viewport: "laptop",
  });
  expect(
    await screen.findByRole("button", { name: "Book the photo shoot" }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Send logo drafts" }),
  ).toBeVisible();
  expect(cursors).toContain("page-2");
});
