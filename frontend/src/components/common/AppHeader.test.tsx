// The header's review badge as the acceptance journeys read it (SEED, A2.2): the count
// inside the Review link carries `data-testid="review-count"` (e2e/phase2.ts
// `reviewBadgeCount`), and shows nothing while the queue is empty.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { reviewCount } from "../../test/msw/dashboard";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test.fails(
  "[SEED][A2.2] T-SEED-28 the header's review badge carries the count the journeys read",
  async () => {
    const empty = await renderRoute("/", { viewport: "laptop" });
    const none = await screen.findByRole("link", { name: "Review: 0 waiting" });
    expect(within(none).queryByTestId("review-count")).toBeNull();
    empty.unmount();

    server.use(reviewCount(3));
    await renderRoute("/", { viewport: "laptop" });
    const review = await screen.findByRole("link", {
      name: "Review: 3 waiting",
    });
    expect(within(review).getByTestId("review-count")).toHaveTextContent("3");
  },
);
