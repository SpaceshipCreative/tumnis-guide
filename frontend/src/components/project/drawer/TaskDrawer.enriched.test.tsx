// The drawer's enrichment lines (P1-08, review of PR #109): the acceptance criteria keep
// their own order, and `Enriched by agent · Undo` never offers a later write's change.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

function enrichedTask(projectId: string, criteria: string) {
  return {
    ...makeTask({
      project_id: projectId,
      title: "Draft the Acme proposal",
      label: "hybrid",
      status: "backlog",
    }),
    first_action: "Open the proposal template",
    first_action_source: "agent",
    enrichment_status: "done",
    acceptance_criteria: criteria,
    estimate_minutes: 30,
    change_id: crypto.randomUUID(),
    version: 4,
  };
}

test("[P1-08][UX 9] criteria keep their order, a heading before its own list", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = enrichedTask(
    project.id,
    "AI part:\n- Draft the outline\nYour part:\n- Approve the draft",
  );
  const fake = new ProjectFake({ project, tasks: [task] });
  server.resetHandlers();
  server.use(...fake.handlers);
  server.use(http.get(`/v1/tasks/${task.id}`, () => HttpResponse.json(task)));

  const { unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "laptop" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Draft the Acme proposal",
  });
  const ours = await within(drawer).findByText("AI part:");
  const lists = within(drawer).getAllByRole("list", {
    name: "Acceptance criteria",
  });
  expect(lists.map((list) => list.textContent)).toEqual([
    "Draft the outline",
    "Approve the draft",
  ]);
  const yours = within(drawer).getByText("Your part:");
  const [first, second] = lists as [HTMLElement, HTMLElement];
  const follows = (a: Node, b: Node) =>
    Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  expect(follows(ours, first)).toBe(true);
  expect(follows(first, yours)).toBe(true);
  expect(follows(yours, second)).toBe(true);
  unmount();
});

test("[P1-08][UX 9] a title edit's answer does not become the enrichment's Undo", async () => {
  const project = makeProject({ name: "Acme site" });
  const task = enrichedTask(project.id, "- The proposal is sent");
  const titled = {
    ...task,
    title: "Draft the Acme proposal v2",
    change_id: crypto.randomUUID(),
    version: 5,
  };
  // The read keeps answering the edit's own change: the worst case of the moment
  // between the edit's answer and the read that follows it.
  let current: Record<string, unknown> = task;
  const fake = new ProjectFake({ project, tasks: [task] });
  server.resetHandlers();
  server.use(...fake.handlers);
  server.use(
    http.get(`/v1/tasks/${task.id}`, () => HttpResponse.json(current)),
    http.patch(`/v1/tasks/${task.id}`, () => {
      current = titled;
      return HttpResponse.json(titled);
    }),
  );

  const { user, unmount } = await renderRoute(
    `/projects/${project.id}?task=${task.id}`,
    { viewport: "laptop" },
  );
  const drawer = await screen.findByRole("dialog", {
    name: "Draft the Acme proposal",
  });
  expect(await within(drawer).findByText("Enriched by agent")).toBeVisible();

  const title = within(drawer).getByRole("textbox", { name: "Title" });
  await user.clear(title);
  await user.type(title, "Draft the Acme proposal v2");
  await user.click(within(drawer).getByRole("button", { name: "Save" }));

  await screen.findByRole("dialog", { name: "Draft the Acme proposal v2" });
  await waitFor(() => {
    expect(within(drawer).queryByText("Enriched by agent")).toBeNull();
  });
  unmount();
});
