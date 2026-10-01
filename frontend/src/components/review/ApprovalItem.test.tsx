// An `approval` review item (P2-05, FR-5.6, SEC-3): a run asks to take an action. Approve
// and deny both need a reason, which goes to the audit log with the decision; a preset
// fills the reason in one tap, and a blank reason sends nothing. Every fixture is
// synthetic.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { renderWithProviders } from "../../test/render";

const RUN_ID = "01950000-0000-7000-8000-000000000931";
const APPROVAL_ID = "01950000-0000-7000-8000-000000000932";

type Decide = (action: string, payload?: Record<string, unknown>) => void;

interface ApprovalItemModule {
  ApprovalItem: (props: {
    item: ReturnType<typeof makeReviewItem>;
    onDecide: Decide;
  }) => React.JSX.Element;
}

// Loaded at run time, so this file compiles before ApprovalItem.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

function approvalItem() {
  return makeReviewItem({
    kind: "approval",
    target_type: "run",
    target_id: RUN_ID,
    target_title: "Fix footer link",
    actions: ["approve", "deny", "snooze"],
    primary_action: "approve",
    payload: {
      approval_id: APPROVAL_ID,
      run_id: RUN_ID,
      action_class: "merge_main",
      description: "Merge the footer fix into main",
      target: "example/site#7",
      rule: "gated_by_policy",
    },
  });
}

test("[P2-05][SEC-3] deny needs a reason", async () => {
  const { ApprovalItem } = await load<ApprovalItemModule>("./ApprovalItem");
  const onDecide = vi.fn<Decide>();
  const { user } = renderWithProviders(
    <ApprovalItem item={approvalItem()} onDecide={onDecide} />,
  );

  const card = screen.getByRole("article", { name: /Fix footer link/ });
  expect(
    within(card).getByText("Merge the footer fix into main"),
  ).toBeInTheDocument();

  // Blank (or only spaces) blocks both decisions.
  const deny = within(card).getByRole("button", { name: "Deny" });
  const approve = within(card).getByRole("button", { name: "Approve" });
  expect(deny).toBeDisabled();
  expect(approve).toBeDisabled();
  const reason = within(card).getByRole("textbox", { name: "Reason" });
  await user.type(reason, "   ");
  expect(deny).toBeDisabled();
  await user.click(deny);
  expect(onDecide).not.toHaveBeenCalled();

  // A preset fills the reason; deny then sends it.
  await user.click(within(card).getByRole("button", { name: "Too risky" }));
  expect(reason).toHaveValue("Too risky");
  expect(deny).toBeEnabled();
  await user.click(deny);
  expect(onDecide).toHaveBeenCalledTimes(1);
  expect(onDecide).toHaveBeenLastCalledWith("deny", { reason: "Too risky" });

  // A typed reason works for approve too.
  await user.clear(reason);
  expect(approve).toBeDisabled();
  await user.type(reason, "Checked the diff");
  await user.click(approve);
  expect(onDecide).toHaveBeenLastCalledWith("approve", {
    reason: "Checked the diff",
  });
});
