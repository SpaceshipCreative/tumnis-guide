// The `unattended_refused` review item (P4-04, FR-4.5, SAF-1): a queued task the window
// would not run says why in plain words, is marked Overnight, and offers "Take off the
// queue" (accept) and Snooze like any other card.
import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { renderWithProviders } from "../../test/render";
import { ReviewItemCard } from "./ReviewItemCard";

test("[P4-04][FR-4.5] a refused overnight task shows its reason and its actions", async () => {
  const item = makeReviewItem({
    kind: "unattended_refused",
    target_title: "Summarise the client email",
    target_tainted: true,
    actions: ["accept", "snooze"],
    payload: {
      refusal: "tainted",
      reason: "From outside content: needs you",
      window_start: "2026-03-10T03:00:00Z",
      batch: "overnight",
      release_at: "2026-03-10T13:45:00Z",
    },
  });
  const onDecide = vi.fn();
  const onMode = vi.fn();
  const { user } = renderWithProviders(
    <ReviewItemCard
      item={item}
      current={false}
      mode="idle"
      busy={false}
      articleRef={() => undefined}
      onFocus={() => undefined}
      onMode={onMode}
      onDecide={onDecide}
      onOpen={() => undefined}
    />,
    { viewport: "phone" },
  );

  expect(screen.getByText("Overnight")).toBeInTheDocument();
  expect(screen.getByText("Did not run overnight")).toBeInTheDocument();
  expect(
    screen.getByText(/^From outside content: needs you\./),
  ).toBeInTheDocument();
  // No jargon from the payload (refusal codes, window times).
  expect(screen.queryByText(/window start|tainted/)).toBeNull();

  await user.click(screen.getByRole("button", { name: "Take off the queue" }));
  expect(onDecide).toHaveBeenCalledWith({ action: "accept" });
  await user.click(screen.getByRole("button", { name: "Snooze" }));
  expect(onMode).toHaveBeenCalledWith("snooze");
});
