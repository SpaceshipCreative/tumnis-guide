// A refused board move (P0-24, FR-3.2): the card goes back where it was and the toast
// says, in plain words, where a task in that status can go. The card's Move menu takes the
// same path as a drag (planMove, one move request, optimistic reorder).
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test("[P0-24][FR-3.2] T-P0-24-06 a refused move rolls back with plain words", async () => {
  for (const viewport of ["phone", "laptop"] as const) {
    const project = makeProject({ name: "Acme site" });
    const task = makeTask({
      project_id: project.id,
      parent_id: null,
      title: "Review logo drafts",
      status: "in_review",
      label: "ai",
    });
    const fake = new ProjectFake({ project, tasks: [task] });
    fake.refuseMoves = true;
    server.resetHandlers();
    server.use(...fake.handlers);

    const { user, unmount } = await renderRoute(
      `/projects/${project.id}?view=board`,
      { viewport },
    );
    const inReview = await screen.findByRole("region", { name: "In review" });
    const card = await within(inReview).findByRole("listitem", {
      name: /Review logo drafts/,
    });
    await user.click(within(card).getByRole("button", { name: "Move" }));
    await user.click(screen.getByRole("menuitem", { name: "Today" }));

    expect(
      await screen.findByText(
        "In review tasks can go back to In progress or to Done",
      ),
    ).toBeVisible();
    await waitFor(() => {
      expect(
        within(screen.getByRole("region", { name: "In review" })).getByText(
          "Review logo drafts",
        ),
      ).toBeVisible();
    });
    expect(
      within(screen.getByRole("region", { name: "Today" })).queryByText(
        "Review logo drafts",
      ),
    ).toBeNull();
    const moves = fake.sent("POST", `/v1/tasks/${task.id}/move`);
    expect(moves).toHaveLength(1);
    expect(moves[0]?.body).toEqual({
      column_id: fake.column("today").id,
      board_rank: expect.any(String) as string,
      version: task.version,
    });
    unmount();
  }
});
