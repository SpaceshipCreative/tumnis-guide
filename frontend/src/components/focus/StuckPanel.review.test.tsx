// The stuck panel after the agent's report is reviewed (Scott decision 73, FR-10.5):
// accepting it marks the step done; rejecting it hands the step back to the person, who
// sees the task's first action again.
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import type { NextStepOut } from "../../api/types.gen";
import { StuckPanel } from "./StuckPanel";

const REPORT = "Drafted the March invoice from February's";

function step(state: string): NextStepOut {
  return {
    focus_event_id: crypto.randomUUID(),
    task_id: crypto.randomUUID(),
    run_id: crypto.randomUUID(),
    state,
    requested_at: new Date().toISOString(),
    first_action: "Open the invoice template",
    timer_minutes: 10,
    fallback_at: null,
    step: null,
    summary: REPORT,
  } as unknown as NextStepOut;
}

test.fails("[P4-02][FR-10.5] an accepted report shows the step done", () => {
  render(<StuckPanel step={step("done")} />);
  const panel = screen.getByRole("status", { name: "Next step" });
  expect(panel).toHaveTextContent("Step done");
  expect(panel).toHaveTextContent(REPORT);
});

test.fails("[P4-02][FR-10.5] a rejected report hands the step back", () => {
  render(<StuckPanel step={step("reopened")} />);
  const panel = screen.getByRole("status", { name: "Next step" });
  expect(panel).toHaveTextContent("Back to you");
  expect(panel).toHaveTextContent("Open the invoice template");
  expect(panel).not.toHaveTextContent(REPORT);
});
