// The project header (P0-24, FR-2.4): name, goal, health, the next milestone, today's
// tasks with their estimates and the sum, and the agent's status, at both widths.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject, makeTask } from "../../test/factories";
import { renderWithProviders } from "../../test/render";
import { ProjectHeader } from "./ProjectHeader";

test.fails(
  "[P0-24][FR-2.4] T-P0-24-13 header shows name, goal, health, milestone, today's tasks and agent status",
  () => {
    const project = makeProject({
      name: "Acme site",
      goal: "Ship the new logo",
      health: "at_risk",
      next_milestone: "2026-03-20",
    });
    const today = [
      makeTask({
        project_id: project.id,
        title: "Send logo drafts",
        status: "today",
        estimate_minutes: 30,
      }),
      makeTask({
        project_id: project.id,
        title: "Pick colours",
        status: "in_progress",
        estimate_minutes: 45,
      }),
      makeTask({
        project_id: project.id,
        title: "Ask Jo about fonts",
        status: "today",
        estimate_minutes: null,
      }),
    ];
    for (const viewport of ["phone", "laptop"] as const) {
      const { unmount } = renderWithProviders(
        <ProjectHeader project={project} today={today} />,
        { viewport },
      );
      expect(
        screen.getByRole("heading", { level: 1, name: "Acme site" }),
      ).toBeVisible();
      expect(screen.getByText("Ship the new logo")).toBeVisible();
      expect(
        screen.getByRole("img", { name: "Health: At risk" }),
      ).toBeVisible();
      expect(screen.getByText("Next milestone: Mar 20")).toBeVisible();
      const list = screen.getByRole("list", { name: "Today's tasks" });
      const items = within(list).getAllByRole("listitem");
      expect(items.map((item) => item.textContent)).toEqual([
        expect.stringContaining("Send logo drafts"),
        expect.stringContaining("Pick colours"),
        expect.stringContaining("Ask Jo about fonts"),
      ]);
      expect(items[0]).toHaveTextContent("30 min");
      expect(items[1]).toHaveTextContent("45 min");
      expect(items[2]).toHaveTextContent("No estimate");
      expect(screen.getByText("Today: 1 h 15 min")).toBeVisible();
      expect(screen.getByText("No agent yet")).toBeVisible();
      unmount();
    }

    renderWithProviders(
      <ProjectHeader
        project={{ ...project, next_milestone: null, goal: null }}
        today={[]}
      />,
    );
    expect(screen.getByText("No milestone set")).toBeVisible();
    expect(screen.getByText("Nothing planned for today")).toBeVisible();
  },
);
