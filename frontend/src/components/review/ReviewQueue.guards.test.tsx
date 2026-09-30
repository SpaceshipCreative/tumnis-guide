// Review queue guards (P1-13 review follow-up): a number edit sends only a positive whole
// number, and a key press while a decision is on its way does not send a second one.
// Every fixture is synthetic.
import { screen, waitFor } from "@testing-library/react";
import { http } from "msw";
import { expect, test } from "vitest";

import { reviewKinds } from "../../test/msw/handlers";
import { workingHoursHandlers } from "../../test/msw/planning";
import { makeReviewItem, reviewQueue } from "../../test/msw/review";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderRoute } from "../../test/render";

const KINDS = ["low_confidence_label", "estimate_outlier"];

function card(title: string): HTMLElement {
  return screen.getByRole("article", { name: new RegExp(title) });
}

test("[P1-13][UX 7] a number edit sends only a positive whole number", async () => {
  const outlier = makeReviewItem({
    kind: "estimate_outlier",
    target_title: "Task A",
    payload: { estimate_minutes: 600, flag: "too_high", history_median: 60 },
  });
  const queue = reviewQueue([outlier]);
  server.use(
    reviewKinds(KINDS),
    ...queue.handlers,
    ...workingHoursHandlers(new Recorder()),
  );
  const { user } = await renderRoute("/review");
  await waitFor(() => {
    expect(card("Task A")).toHaveFocus();
  });

  await user.keyboard("e");
  const input = await screen.findByLabelText("Estimate in minutes");
  for (const bad of ["0", "-5", "1.5", "1e999"]) {
    await user.clear(input);
    await user.type(input, `${bad}{Enter}`);
  }
  expect(queue.recorder.sent.filter((s) => s.method === "POST")).toEqual([]);

  await user.clear(input);
  await user.type(input, "45{Enter}");
  await waitFor(() => {
    expect(queue.recorder.sent.filter((s) => s.method === "POST")).toHaveLength(
      1,
    );
  });
  expect(queue.recorder.sent[0]?.body).toEqual({
    action: "edit",
    payload: { estimate_minutes: 45 },
    version: 1,
  });
});

test("[P1-13][UX 7] a key press during a pending decision sends nothing more", async () => {
  const queue = reviewQueue([
    makeReviewItem({ target_title: "Task A" }),
    makeReviewItem({ target_title: "Task B" }),
  ]);
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    reviewKinds(KINDS),
    ...queue.handlers,
    ...workingHoursHandlers(new Recorder()),
  );
  // Prepended: holds every decide until released, then falls through to the queue's.
  server.use(
    http.post("*/v1/review/:id/decide", async () => {
      await gate;
    }),
  );
  const { user } = await renderRoute("/review");
  await waitFor(() => {
    expect(card("Task A")).toHaveFocus();
  });

  await user.keyboard("{Enter}");
  await user.keyboard("{Enter}");
  await user.keyboard("r");
  release();
  await waitFor(() => {
    expect(card("Task B")).toHaveFocus();
  });
  expect(queue.recorder.sent.filter((s) => s.method === "POST")).toHaveLength(
    1,
  );
});
