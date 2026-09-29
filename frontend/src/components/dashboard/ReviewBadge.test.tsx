// The review badge (P0-23, FR-1.4): the count of items waiting for a decision (0 until
// P1-13 queues real ones), linking to the review route.
import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { reviewCount } from "../../test/msw/dashboard";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";

test.fails(
  "[P0-23][FR-1.4] T-P0-23-06 badge shows the count and opens the review route",
  async () => {
    const empty = await renderRoute("/");
    expect(
      await screen.findByRole("link", { name: "0 to review" }),
    ).toBeInTheDocument();
    empty.unmount();

    server.use(reviewCount(3));
    const { router, user } = await renderRoute("/");
    await user.click(await screen.findByRole("link", { name: "3 to review" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/review");
    });
  },
);
