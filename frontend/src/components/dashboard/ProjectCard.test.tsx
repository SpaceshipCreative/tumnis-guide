// Project cards (P0-23, FR-1.1, UX 10): name, rule-based health in plain words, next
// milestone, open count and last agent activity, at phone and laptop widths.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import {
  renderWithProviders,
  renderWithRouter,
  type Viewport,
} from "../../test/render";
import { HealthBadge, healthCopy } from "./HealthBadge";
import { ProjectCard } from "./ProjectCard";

const TZ = "America/New_York";
const VIEWPORTS: readonly Viewport[] = ["phone", "laptop"];

test.fails(
  "[P0-23][FR-1.1] T-P0-23-01 card shows name, health, next milestone, open count, last agent activity",
  async () => {
    for (const viewport of VIEWPORTS) {
      const project = makeProject({
        name: "Acme site",
        health: "at_risk",
        next_milestone: "2026-03-20",
        open_count: 7,
        last_agent_activity_at: null,
      });
      const { unmount } = await renderWithRouter(
        <ProjectCard project={project} timeZone={TZ} />,
        { viewport },
      );

      const card = screen.getByRole("article", { name: "Acme site" });
      expect(card).toHaveAttribute("data-project-id", project.id);
      expect(
        within(card).getByRole("link", { name: "Acme site" }),
      ).toHaveAttribute("href", `/projects/${project.id}`);
      expect(within(card).getByLabelText("Health: At risk")).toHaveTextContent(
        "At risk",
      );
      // A milestone is a calendar day: the same day in every timezone.
      expect(card).toHaveTextContent("Next milestone Mar 20");
      expect(within(card).getByTestId("open-count")).toHaveTextContent(/^7$/);
      expect(card).toHaveTextContent("7 open");
      expect(card).toHaveTextContent("No agent activity yet");
      unmount();
    }

    // Agent activity is an instant, shown in the workspace timezone (EDT, UTC-4).
    const active = makeProject({
      name: "Tumnis dogfood",
      next_milestone: null,
      last_agent_activity_at: "2026-03-09T14:30:00Z",
    });
    await renderWithRouter(<ProjectCard project={active} timeZone={TZ} />, {
      viewport: "laptop",
    });
    const card = screen.getByRole("article", { name: "Tumnis dogfood" });
    expect(card).toHaveTextContent("No milestone");
    expect(card).toHaveTextContent("Agent active Mar 9, 10:30 AM");
  },
);

test.fails(
  "[P0-23][FR-1.1][UX 10] T-P0-23-02 health uses plain words for all three states",
  () => {
    expect(healthCopy).toEqual({
      on_track: "On track",
      at_risk: "At risk",
      blocked: "Blocked: waiting on you",
    });
    for (const [health, words] of Object.entries(healthCopy)) {
      const { unmount } = renderWithProviders(
        <HealthBadge health={health as keyof typeof healthCopy} />,
      );
      const badge = screen.getByLabelText(`Health: ${words}`);
      expect(badge).toHaveTextContent(words);
      expect(badge).not.toHaveTextContent(/_/);
      unmount();
    }
  },
);
