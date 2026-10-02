// Close the day's Queued overnight (P4-04, FR-4.5, J7): the tasks queued to run
// unattended, in queued order; one that will not run says so, with the reason in plain
// words.
import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { QueuedUnattendedOut } from "../../api/types.gen";
import { daySummary, FULL_DAY, taskRef } from "../../test/msw/closeDay";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CloseDayPanel } from "./CloseDayPanel";

function queued(
  n: number,
  title: string,
  overrides: Partial<QueuedUnattendedOut> = {},
): QueuedUnattendedOut {
  return {
    ...taskRef(n, title, "ai"),
    queued_at: `2026-03-09T1${String(n)}:00:00Z`,
    will_run: true,
    refusal: null,
    reason: null,
    ...overrides,
  };
}

test("[P4-04][FR-4.5][J7] Queued overnight lists queued tasks in order with reasons", async () => {
  const tasks = [
    queued(5, "Draft the release notes"),
    queued(6, "Summarise the client email", {
      will_run: false,
      refusal: "tainted",
      reason: "From outside content: needs you",
    }),
    queued(7, "Tidy the changelog"),
  ];
  server.use(
    daySummary({
      ...FULL_DAY,
      queued_overnight: tasks.map(({ task_id, project_id, title, label }) => ({
        task_id,
        project_id,
        title,
        label,
      })),
      queued_unattended: tasks,
    }),
  );
  renderWithProviders(
    <CloseDayPanel day="2026-03-09" onClose={() => undefined} />,
    {
      viewport: "phone",
    },
  );
  const panel = await screen.findByRole("dialog", { name: "Close the day" });
  const part = await within(panel).findByRole("region", {
    name: "Queued overnight",
  });
  const rows = within(part).getAllByRole("listitem");
  expect(rows.map((row) => row.textContent)).toEqual([
    "Draft the release notes",
    "Summarise the client emailWill not run: From outside content: needs you",
    "Tidy the changelog",
  ]);
  expect(part).not.toHaveTextContent("Nothing queued");
});
