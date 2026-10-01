// The Tasks view's row shows the task's status in words (Scott decision 67, A2.2): a
// DS-01 badge on the row, so a person sees "In progress" (and every other status)
// without opening the task, and never by colour alone.
import { cleanup, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { makeTask } from "../../test/factories";
import { renderWithProviders } from "../../test/render";
import { TaskRow } from "./TaskRow";

const EXPECTED = [
  ["backlog", "Backlog"],
  ["today", "Today"],
  ["in_progress", "In progress"],
  ["waiting_on_human", "Waiting on you"],
  ["in_review", "In review"],
  ["done", "Done"],
] as const;

test("[FIX-taskrow-status][FR-2.6] T-FIX-taskrow-status-01 the task row shows its status in words", () => {
  for (const [status, word] of EXPECTED) {
    const task = makeTask({
      parent_id: null,
      title: "Fix the footer",
      status,
      label: "human",
      due_on: null,
      estimate_minutes: null,
    });
    renderWithProviders(
      <ul>
        <TaskRow task={task} subtasksOf={() => []} onOpen={() => undefined} />
      </ul>,
    );
    const row = screen.getByRole("listitem");
    const chip = within(row).getByText(word, {
      selector: "[data-task-status]",
    });
    expect(chip).toBeVisible();
    expect(chip).toHaveAttribute("data-task-status", status);
    expect(row).toHaveTextContent(word);
    cleanup();
  }
});
