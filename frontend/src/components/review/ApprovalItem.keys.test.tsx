// An `approval` card's keys (P2-05, SEC-3): Enter and r on the focused card go to the
// reason field and never decide (a decision without a reason is refused), and the card
// says why the server asked. Every fixture is synthetic.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { renderWithProviders } from "../../test/render";
import { ApprovalItem, type ApprovalDecide } from "./ApprovalItem";

test("[P2-05][SEC-3] Enter and r go to the reason and never decide", async () => {
  const onDecide = vi.fn<ApprovalDecide>();
  const { user } = renderWithProviders(
    <ApprovalItem
      item={makeReviewItem({
        kind: "approval",
        target_title: "Fix footer link",
        target_tainted: true,
        actions: ["approve", "deny", "snooze"],
        primary_action: "approve",
        payload: {
          approval_id: "01950000-0000-7000-8000-000000000951",
          run_id: "01950000-0000-7000-8000-000000000952",
          action_class: "push_feature_branch",
          description: "",
          rule: "tainted_run",
        },
      })}
      onDecide={onDecide}
    />,
  );
  const card = screen.getByRole("article", { name: /Fix footer link/ });
  expect(within(card).getByText("Push feature branch")).toBeInTheDocument();
  expect(within(card).getByText(/read outside content/)).toBeInTheDocument();

  const reason = within(card).getByRole("textbox", { name: "Reason" });
  card.focus();
  await user.keyboard("{Enter}");
  expect(reason).toHaveFocus();
  card.focus();
  await user.keyboard("r");
  expect(reason).toHaveFocus();
  expect(reason).toHaveValue("");
  expect(onDecide).not.toHaveBeenCalled();
});
