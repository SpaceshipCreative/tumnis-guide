// The drawer reads every page of a task's comments (P0-24, FR-3.5): `GET
// /v1/tasks/{id}/comments` answers at most 50 per page, so the list follows
// `next_cursor` until it runs out (CodeRabbit on #57).
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { CommentOut } from "../../../api/types.gen";
import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

test("[P0-24][FR-3.5] the drawer shows comments from every page", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = makeTask({
    project_id: project.id,
    title: "Send the weekly report",
    status: "backlog",
  });
  const comment = (body_md: string): CommentOut => ({
    id: crypto.randomUUID(),
    task_id: task.id,
    body_md,
    created_at: "2026-09-01T09:00:00Z",
    created_by: "user:scott",
    version: 1,
  });
  const fake = new ProjectFake({ project, tasks: [task] });
  server.resetHandlers();
  server.use(...fake.handlers);
  const cursors: (string | null)[] = [];
  server.use(
    http.get("/v1/tasks/:id/comments", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      cursors.push(cursor);
      return cursor === "page-2"
        ? HttpResponse.json({
            items: [comment("Sent to the client")],
            next_cursor: null,
          })
        : HttpResponse.json({
            items: [comment("Drafted the summary")],
            next_cursor: "page-2",
          });
    }),
  );

  await renderRoute(`/projects/${project.id}?task=${task.id}`, {
    viewport: "laptop",
  });
  const drawer = await screen.findByRole(
    "dialog",
    { name: "Send the weekly report" },
    { timeout: 10_000 },
  );
  expect(
    await within(drawer).findByText(
      "Drafted the summary",
      {},
      { timeout: 10_000 },
    ),
  ).toBeVisible();
  expect(within(drawer).getByText("Sent to the client")).toBeVisible();
  expect(cursors).toContain("page-2");
});
