// The composer (P0-24, FR-2.5): one input; Enter creates a task in this project (label
// pending, Backlog), shown at once under Up next and kept once the server answers.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { ProjectFake } from "../../test/msw/project";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test.fails(
  "[P0-24][FR-2.5] T-P0-24-14 Enter creates a task in this project",
  async () => {
    for (const viewport of ["phone", "laptop"] as const) {
      const project = makeProject({ name: "Acme site" });
      const fake = new ProjectFake({ project });
      let release: () => void = () => undefined;
      fake.createGate = new Promise((resolve) => {
        release = resolve;
      });
      server.resetHandlers();
      server.use(...fake.handlers);

      const { user, unmount } = await renderRoute(
        `/projects/${project.id}?view=tasks`,
        { viewport },
      );
      const input = await screen.findByRole("textbox", { name: "New task" });
      await user.type(input, "Send logo drafts{Enter}");

      // Optimistic: the row shows before the server answers, and the input is cleared.
      const upNext = screen.getByRole("region", { name: "Up next" });
      expect(await within(upNext).findByText("Send logo drafts")).toBeVisible();
      expect(input).toHaveValue("");
      expect(fake.sent("POST", "/v1/tasks").map((s) => s.body)).toEqual([
        { project_id: project.id, title: "Send logo drafts" },
      ]);
      expect(fake.sent("POST", "/v1/tasks")[0]?.idempotencyKey).toBeTruthy();

      release();
      await waitFor(() => {
        expect(fake.tasks.size).toBe(1);
      });
      await waitFor(() => {
        expect(within(upNext).getAllByText("Send logo drafts")).toHaveLength(1);
      });
      unmount();
    }
  },
);
