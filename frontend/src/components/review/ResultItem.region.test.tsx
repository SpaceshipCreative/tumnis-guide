// A `result` item's files touched (P2-04, FR-5.8) are a region of the card as well as a
// list, as the run view's are: the acceptance journey finds both by "Files touched".
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { renderWithProviders } from "../../test/render";
import { ResultItem } from "./ResultItem";

test.fails(
  "[P2-04][FR-5.8] the files touched are a named region of the result",
  () => {
    const item = makeReviewItem({
      kind: "result",
      target_type: "task",
      target_id: "01950000-0000-7000-8000-000000000d01",
      target_title: "Fix footer link",
      actions: ["accept", "reject", "snooze"],
      primary_action: "accept",
      payload: {
        run_id: "01950000-0000-7000-8000-000000000d02",
        outcome: "done",
        summary: "Fixed the footer link target",
        files_touched: [{ path: "src/footer.tsx", change: "modified" }],
        links: [],
        tests_summary: null,
      },
    });
    renderWithProviders(<ResultItem item={item} onDecide={vi.fn()} />);

    const card = screen.getByRole("article", { name: /Fix footer link/ });
    const region = within(card).getByRole("region", { name: "Files touched" });
    expect(region).toHaveTextContent("src/footer.tsx");
    expect(
      within(region).getByRole("list", { name: "Files touched" }),
    ).toBeVisible();
  },
);
