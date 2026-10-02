// Run unattended (P4-04, FR-4.5, SAF-1): an AI task's drawer offers a "Run unattended"
// switch that queues it for tonight's window. A tainted task never runs unattended, so
// instead of the switch it shows the taint mark "Needs you: from outside content"; a
// Human, Hybrid or unlabelled task shows neither.
import { cleanup, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeTask } from "../../test/factories";
import { renderWithProviders } from "../../test/render";
import { RunUnattendedToggle } from "./RunUnattendedToggle";

const NEEDS_YOU = "Needs you: from outside content";

test.fails(
  "[P4-04][FR-4.5] T-P4-04-12 toggle hidden for tainted and non-AI",
  () => {
    renderWithProviders(
      <RunUnattendedToggle
        task={makeTask({ label: "ai", tainted: false, status: "today" })}
      />,
    );
    expect(
      screen.getByRole("switch", { name: "Run unattended" }),
    ).toBeVisible();
    expect(screen.queryByText(NEEDS_YOU)).toBeNull();
    cleanup();

    renderWithProviders(
      <RunUnattendedToggle
        task={makeTask({ label: "ai", tainted: true, status: "today" })}
      />,
    );
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByText(NEEDS_YOU)).toBeVisible();
    cleanup();

    for (const label of ["human", "hybrid", null] as const) {
      const { container } = renderWithProviders(
        <RunUnattendedToggle
          task={makeTask({ label, tainted: false, status: "today" })}
        />,
      );
      expect(screen.queryByRole("switch")).toBeNull();
      expect(screen.queryByText(NEEDS_YOU)).toBeNull();
      expect(container).toBeEmptyDOMElement();
      cleanup();
    }
  },
);
