// The dashboard's Just added list (A1.1, coordinator decision 83): today's captures still
// in Backlog, each with its label chip (a visible one-line reason), its first action
// (only the value in the line's element) and its estimate; hidden when there are none.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { makeTask } from "../../test/factories";
import { justAdded } from "../../test/msw/dashboard";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { JustAdded } from "./JustAdded";

test("[JOURNEYS-B][A1.1] T-JOURNEYS-B-01 Just added is hidden when nothing was added today", async () => {
  server.use(justAdded([]));
  const { queryClient } = renderWithProviders(
    <JustAdded onOpen={() => undefined} />,
  );
  await vi.waitFor(() => {
    expect(queryClient.isFetching()).toBe(0);
  });
  expect(
    screen.queryByRole("region", { name: "Just added" }),
  ).not.toBeInTheDocument();
});

test("[JOURNEYS-B][A1.1] T-JOURNEYS-B-02 a Just added row shows the label, first action and estimate", async () => {
  const task = {
    ...makeTask({
      title: "Send Acme the March invoice",
      status: "backlog",
      label: "hybrid",
      estimate_minutes: 20,
    }),
    label_source: "jev",
    label_reason: "Drafting is quick; sending needs you",
    label_suggestion: null,
    first_action: "Open the invoice template",
    first_action_source: "placeholder",
  } as const;
  server.use(justAdded([task]));
  const onOpen = vi.fn();
  const { user } = renderWithProviders(<JustAdded onOpen={onOpen} />);

  const region = await screen.findByRole("region", { name: "Just added" });
  const row = within(region).getByRole("listitem");
  const chip = within(row).getByTestId("label-chip");
  expect(chip).toHaveTextContent("Hybrid");
  const reasonId = chip.getAttribute("aria-describedby") ?? "";
  expect(reasonId).not.toBe("");
  const reason = document.getElementById(reasonId);
  expect(reason).toHaveTextContent("Drafting is quick; sending needs you");
  expect(reason).not.toHaveClass("sr-only");

  const first = within(row).getByTestId("first-action");
  expect(first).toHaveAttribute("data-state", "placeholder");
  expect(first.textContent).toBe("Open the invoice template");
  expect(within(row).getByTestId("estimate-chip")).toHaveTextContent("20 min");

  await user.click(
    within(row).getByRole("button", { name: "Send Acme the March invoice" }),
  );
  expect(onOpen).toHaveBeenCalledWith(task.id);
});

test("[JOURNEYS-B][A1.1] T-JOURNEYS-B-03 an AI task's row shows no estimate", async () => {
  const task = {
    ...makeTask({
      title: "Tag the photos",
      status: "backlog",
      label: "ai",
      estimate_minutes: 15,
    }),
    first_action: null,
  };
  server.use(justAdded([task]));
  renderWithProviders(<JustAdded onOpen={() => undefined} />);

  const row = await screen.findByRole("listitem");
  expect(within(row).queryByTestId("estimate-chip")).not.toBeInTheDocument();
  expect(within(row).getByTestId("first-action")).toHaveAttribute(
    "data-state",
    "pending",
  );
});
