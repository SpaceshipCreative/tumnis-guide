// A `result` review item (P2-04, FR-5.8): what the agent did (summary, files touched,
// links), Accept in one keystroke, and Reject only with feedback, which goes back to the
// agent as a comment.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { renderWithProviders } from "../../test/render";

const TASK_ID = "01950000-0000-7000-8000-000000000912";
const RUN_ID = "01950000-0000-7000-8000-000000000911";

type Decide = (action: string, payload?: Record<string, unknown>) => void;

interface ResultItemModule {
  ResultItem: (props: {
    item: ReturnType<typeof makeReviewItem>;
    onDecide: Decide;
  }) => React.JSX.Element;
}

// Loaded at run time, so this file compiles before ResultItem.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

function resultItem() {
  return makeReviewItem({
    kind: "result",
    target_type: "task",
    target_id: TASK_ID,
    target_title: "Fix footer link",
    actions: ["accept", "reject", "snooze"],
    primary_action: "accept",
    payload: {
      run_id: RUN_ID,
      outcome: "done",
      summary: "Fixed the footer link",
      files_touched: [{ path: "src/footer.tsx", change: "modified" }],
      links: [
        {
          kind: "pull_request",
          url: "https://github.com/acme/site/pull/7",
          label: "PR",
        },
      ],
      tests_summary: null,
    },
  });
}

test("[P2-04][FR-5.8] reject requires feedback", async () => {
  const { ResultItem } = await load<ResultItemModule>("./ResultItem");
  const onDecide = vi.fn<Decide>();
  const { user } = renderWithProviders(
    <ResultItem item={resultItem()} onDecide={onDecide} />,
  );

  const card = screen.getByRole("article", { name: /Fix footer link/ });
  expect(within(card).getByText("Fixed the footer link")).toBeInTheDocument();
  const files = within(card).getByRole("list", { name: "Files touched" });
  expect(within(files).getByText("src/footer.tsx")).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "PR" })).toHaveAttribute(
    "href",
    "https://github.com/acme/site/pull/7",
  );

  // Reject is disabled until feedback is typed.
  const reject = within(card).getByRole("button", { name: "Reject" });
  expect(reject).toBeDisabled();
  const feedback = within(card).getByRole("textbox", { name: "Feedback" });
  await user.type(feedback, "   ");
  expect(reject).toBeDisabled();
  await user.clear(feedback);
  await user.type(feedback, "Link should open in a new tab");
  expect(reject).toBeEnabled();
  await user.click(reject);
  expect(onDecide).toHaveBeenLastCalledWith("reject", {
    feedback: "Link should open in a new tab",
  });

  // Accept is one keystroke on the focused card.
  onDecide.mockClear();
  card.focus();
  await user.keyboard("{Enter}");
  expect(onDecide).toHaveBeenCalledTimes(1);
  expect(onDecide).toHaveBeenCalledWith("accept");
});
