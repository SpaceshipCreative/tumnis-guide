// The plain list across projects (P0-24, FR-3.7): due date first, undated last, then
// priority, then title; `/tasks` renders in that order.
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { projectsList } from "../../test/msw/dashboard";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";
import { sortByDue } from "./sorting";

test.fails("[P0-24][FR-3.7] T-P0-24-10 plain list sorts by due date", async () => {
  const acme = makeProject({ name: "Acme site" });
  const course = makeProject({ name: "Authenticity course" });
  const t = (title: string, due_on: string | null, priority = "normal") =>
    makeTask({
      title,
      due_on,
      priority: priority as "normal",
      project_id: title.startsWith("C") ? course.id : acme.id,
      status: "backlog",
    });
  const expected = [
    t("A due Mar 10 urgent", "2026-03-10", "urgent"),
    t("C due Mar 10 high", "2026-03-10", "high"),
    t("B due Mar 10 normal", "2026-03-10"),
    t("D due Mar 10 normal", "2026-03-10"),
    t("C due Mar 12 low", "2026-03-12", "low"),
    t("A undated urgent", null, "urgent"),
    t("B undated low", null, "low"),
  ];

  // The rule itself.
  const shuffled = [
    expected[5],
    expected[3],
    expected[0],
    expected[6],
    expected[2],
    expected[4],
    expected[1],
  ].filter((x) => x !== undefined);
  expect([...shuffled].sort(sortByDue).map((x) => x.title)).toEqual(
    expected.map((x) => x.title),
  );

  // And /tasks shows it, with each task's project.
  server.use(
    projectsList([acme, course]),
    http.get("/v1/tasks", () =>
      HttpResponse.json({ items: shuffled, next_cursor: null }),
    ),
  );
  for (const viewport of ["phone", "laptop"] as const) {
    const { unmount } = await renderRoute("/tasks", { viewport });
    const list = await screen.findByRole("list", { name: "Tasks" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((row) => row.dataset.taskTitle)).toEqual(
      expected.map((x) => x.title),
    );
    const second = rows[1];
    if (!second) throw new Error("two rows");
    expect(within(second).getByText("Authenticity course")).toBeVisible();
    unmount();
  }
});
