// The plan_issue review item (P1-13 with P1-11, J6). APP-12 (application test): the queue
// showed the item's payload as raw "key: value" text ("day: 2026-10-01; title: ...;
// reason: ...; move to: 2026-10-02; estimate minutes: 90"). It reads as a sentence now:
// why the task does not fit and what each action does. Every fixture is synthetic.
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { ReviewItemCard } from "./ReviewItemCard";
import { slotFor } from "./slots";

const planIssue = (payload: Record<string, unknown>) =>
  makeReviewItem({
    kind: "plan_issue",
    target_title: "Write Acme proposal",
    payload: {
      plan_id: crypto.randomUUID(),
      day: "2026-10-01",
      title: "Write Acme proposal",
      estimate_minutes: 90,
      reason: "No 90-minute gap today",
      split: null,
      move_to: "2026-10-02",
      ...payload,
    },
  });

test("[P1-13][UX 7] APP-12 a plan issue reads as a sentence, not its payload", () => {
  const item = planIssue({ split: [60, 30] });
  render(
    <ReviewItemCard
      item={item}
      current={false}
      mode="idle"
      busy={false}
      articleRef={() => undefined}
      onFocus={() => undefined}
      onMode={() => undefined}
      onDecide={() => undefined}
      onOpen={() => undefined}
    />,
  );
  expect(
    screen.getByText(
      "Doesn't fit Thu 1 Oct (no 90-minute gap today). Accept splits it into 60 + 30 min; Edit moves it to Fri 2 Oct; Reject keeps it off that day.",
    ),
  ).toBeVisible();
  expect(screen.getByText("Plan")).toBeVisible();
  expect(screen.queryByText(/day: 2026-10-01/)).toBeNull();
  expect(screen.queryByText(/estimate minutes:/)).toBeNull();
});

test("[P1-13][UX 7] APP-12 a plan issue names only the offers it has", () => {
  expect(slotFor("plan_issue").summary(planIssue({}))).toBe(
    "Doesn't fit Thu 1 Oct (no 90-minute gap today). Edit moves it to Fri 2 Oct; Reject keeps it off that day.",
  );
  expect(
    slotFor("plan_issue").summary(
      planIssue({ move_to: null, reason: "No estimate yet" }),
    ),
  ).toBe(
    "Doesn't fit Thu 1 Oct (no estimate yet). Reject keeps it off that day.",
  );
});
