// The context rail (P0-24, FR-2.7): each section is a one-line summary that opens in
// place; the Brief saves, Schedule lists the recurring tasks and opens one, and the
// subtask threshold saves. The rail on a laptop, the Context sheet on the phone.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../../test/factories";
import { ProjectFake } from "../../../test/msw/project";
import { server } from "../../../test/msw/server";
import { renderRoute } from "../../../test/render";

test.fails(
  "[P0-24][FR-2.7] T-P0-24-15 sections summarise and open in place",
  async () => {
    for (const viewport of ["phone", "laptop"] as const) {
      const project = makeProject({
        name: "Acme site",
        subtask_threshold_min: null,
        version: 2,
        links: [
          { kind: "person", value: "jo@example.com" },
          { kind: "domain", value: "acme.example" },
        ],
      });
      const review = makeTask({
        project_id: project.id,
        title: "Weekly review",
        status: "backlog",
      });
      const fake = new ProjectFake({
        project,
        tasks: [review],
        brief: "Logo refresh for Acme\n\nKeep the wordmark.",
        recurrences: [
          {
            id: crypto.randomUUID(),
            task_id: review.id,
            title: "Weekly review",
            preset: "weekly",
            cron: null,
            weekday: 0,
            month_day: null,
            due_time: "09:00",
            next_due_at: "2026-03-16T13:00:00Z",
            version: 1,
          },
        ],
      });
      server.resetHandlers();
      server.use(...fake.handlers);

      const { user, router, unmount } = await renderRoute(
        `/projects/${project.id}?view=tasks`,
        { viewport },
      );
      let rail: HTMLElement;
      if (viewport === "phone") {
        await user.click(
          await screen.findByRole("button", { name: "Context" }),
        );
        rail = screen.getByRole("dialog", { name: "Context" });
      } else {
        rail = await screen.findByRole("complementary", { name: "Context" });
      }

      // One-line summaries.
      const brief = await within(rail).findByRole("button", { name: /^Brief/ });
      await waitFor(() => {
        expect(brief).toHaveTextContent("Logo refresh for Acme");
      });
      expect(
        within(rail).getByRole("button", { name: /^Connections/ }),
      ).toHaveTextContent("1 person · 1 domain");
      const schedule = within(rail).getByRole("button", { name: /^Schedule/ });
      await waitFor(() => {
        expect(schedule).toHaveTextContent("1 recurring task");
      });
      expect(
        within(rail).getByRole("button", { name: /^Settings/ }),
      ).toHaveTextContent("Subtask threshold: workspace default");

      // Brief opens in place and saves with the version read.
      await user.click(brief);
      expect(brief).toHaveAttribute("aria-expanded", "true");
      const text = within(rail).getByRole("textbox", { name: "Brief" });
      expect(text).toHaveValue("Logo refresh for Acme\n\nKeep the wordmark.");
      await user.clear(text);
      await user.type(text, "Logo and site refresh");
      await user.click(
        within(rail).getByRole("button", { name: "Save brief" }),
      );
      await waitFor(() => {
        expect(
          fake
            .sent("PATCH", `/v1/knowledge/documents/${fake.brief.id}`)
            .map((s) => s.body),
        ).toEqual([{ body_md: "Logo and site refresh", version: 1 }]);
      });

      // Settings: the threshold, empty meaning the workspace default (30).
      await user.click(within(rail).getByRole("button", { name: /^Settings/ }));
      const threshold = within(rail).getByRole("spinbutton", {
        name: "Subtask threshold (minutes)",
      });
      expect(threshold).toHaveValue(null);
      expect(threshold).toHaveAttribute(
        "placeholder",
        "30 (workspace default)",
      );
      await user.type(threshold, "45");
      await user.click(
        within(rail).getByRole("button", { name: "Save settings" }),
      );
      await waitFor(() => {
        expect(
          fake.sent("PATCH", `/v1/projects/${project.id}`).map((s) => s.body),
        ).toEqual([{ subtask_threshold_min: 45, version: 2 }]);
      });

      // Schedule lists the recurring task and opens it in the drawer.
      await user.click(schedule);
      const item = within(rail).getByRole("button", { name: /Weekly review/ });
      expect(item).toHaveTextContent("Weekly");
      await user.click(item);
      await waitFor(() => {
        expect(router.state.location.search).toMatchObject({ task: review.id });
      });
      expect(
        await screen.findByRole("dialog", { name: "Weekly review" }),
      ).toBeVisible();
      unmount();
    }
  },
);
