// ResultItem in the review queue (`confirmReject`): closing the feedback field with
// Escape or Cancel puts focus back on the card, so the queue's keys keep working.
import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { renderWithProviders } from "../../test/render";
import { ResultItem } from "./ResultItem";

function resultItem() {
  return makeReviewItem({
    kind: "result",
    target_type: "task",
    target_id: "01950000-0000-7000-8000-000000000922",
    target_title: "Fix footer link",
    actions: ["accept", "reject", "snooze"],
    primary_action: "accept",
    payload: {
      run_id: "01950000-0000-7000-8000-000000000921",
      outcome: "done",
      summary: "Fixed the footer link",
      files_touched: [],
      links: [],
      tests_summary: null,
    },
  });
}

test.each(["Escape", "Cancel"])(
  "[P2-04] closing the feedback field with %s focuses the card",
  async (how) => {
    const { user } = renderWithProviders(
      <ResultItem item={resultItem()} onDecide={vi.fn()} confirmReject />,
    );
    const card = screen.getByRole("article", { name: /Fix footer link/ });

    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.getByRole("textbox", { name: "Feedback" })).toHaveFocus();
    if (how === "Escape") {
      await user.keyboard("{Escape}");
    } else {
      await user.click(screen.getByRole("button", { name: "Cancel" }));
    }

    expect(
      screen.queryByRole("textbox", { name: "Feedback" }),
    ).not.toBeInTheDocument();
    expect(card).toHaveFocus();
  },
);
