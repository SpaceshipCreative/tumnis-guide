// The agent activity feed (P0-23, FR-1.5): collapsed by default, expandable, and an empty
// state until agent results exist (P2-04).
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { renderWithProviders } from "../../test/render";
import { ActivityFeed } from "./ActivityFeed";

test.fails(
  "[P0-23][FR-1.5] T-P0-23-07 feed is collapsed by default",
  async () => {
    const { user } = renderWithProviders(<ActivityFeed />);

    const summary = screen.getByText("Agent activity");
    const details = summary.closest("details");
    expect(details).not.toBeNull();
    expect(details?.open).toBe(false);
    expect(screen.getByText("Nothing from the agents yet.")).not.toBeVisible();

    await user.click(summary);
    expect(details?.open).toBe(true);
    expect(screen.getByText("Nothing from the agents yet.")).toBeVisible();
  },
);
