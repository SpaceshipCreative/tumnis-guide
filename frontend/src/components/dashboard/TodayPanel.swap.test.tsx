// The swap picker waits for the server (P1-11, J1 step 4): picking an alternative keeps
// the dialog open, its options disabled, until the swap is saved, so the dialog closing
// means the plan already holds the swapped-in task.
import { screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { ALTERNATES, planHandlers } from "../../test/msw/planning";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithRouter } from "../../test/render";
import { TodayPanel } from "./TodayPanel";

const MONDAY = "2026-03-09";

function planRows(): HTMLElement[] {
  return within(screen.getByRole("region", { name: "Today" })).getAllByRole(
    "listitem",
  );
}

function nth(list: HTMLElement[], index: number): HTMLElement {
  const element = list[index];
  if (element === undefined) throw new Error(`no element at ${String(index)}`);
  return element;
}

async function renderGated(fail?: string) {
  const recorder = new Recorder();
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  server.use(
    ...planHandlers(recorder, undefined, {
      gate,
      ...(fail === undefined ? {} : { fail }),
    }),
  );
  const result = await renderWithRouter(
    <TodayPanel items={[]} total={0} projectNames={{}} planDay={MONDAY} />,
  );
  await screen.findByText("Send logo drafts");
  return { ...result, recorder, release };
}

async function pickFirstAlternate(
  user: Awaited<ReturnType<typeof renderGated>>["user"],
) {
  await user.click(
    within(nth(planRows(), 2)).getByRole("button", { name: "Swap" }),
  );
  const picker = await screen.findByRole("dialog", { name: "Swap" });
  const options = await within(picker).findAllByRole("option");
  await user.click(nth(options, 0));
  return picker;
}

test("[P1-11][J1] the swap picker stays open until the swap is saved", async () => {
  const { user, recorder, release } = await renderGated();
  const picker = await pickFirstAlternate(user);

  // The server has not answered: the dialog is still open and takes no second pick.
  await waitFor(() => {
    expect(recorder.writes()).toHaveLength(1);
  });
  expect(screen.getByRole("dialog", { name: "Swap" })).toBe(picker);
  const options = within(picker).getAllByRole("option");
  for (const option of options) {
    expect(option).toHaveAttribute("aria-disabled", "true");
  }
  await user.click(nth(options, 1));
  expect(recorder.writes()).toHaveLength(1);

  // Once it answers, the dialog closes and the row already shows the new task.
  release();
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "Swap" })).toBeNull();
  });
  expect(planRows()[2]).toHaveTextContent(ALTERNATES[0]?.title ?? "");
});

test("[P1-11][J1] Escape and Cancel wait for a pending swap, so its answer closes only its own picker", async () => {
  const { user, recorder, release } = await renderGated();
  const picker = await pickFirstAlternate(user);
  await waitFor(() => {
    expect(recorder.writes()).toHaveLength(1);
  });

  // While the swap is in flight the picker cannot be dismissed, so no other
  // picker can open and be closed by this swap's answer.
  await user.keyboard("{Escape}");
  await user.click(within(picker).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("dialog", { name: "Swap" })).toBe(picker);

  release();
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "Swap" })).toBeNull();
  });
  expect(planRows()[2]).toHaveTextContent(ALTERNATES[0]?.title ?? "");
});

test("[P1-11][J1] a failed swap closes the picker with the plan unchanged", async () => {
  const { user, release } = await renderGated("swap");
  const before = planRows()[2]?.textContent;
  await pickFirstAlternate(user);
  release();
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "Swap" })).toBeNull();
  });
  expect(planRows()[2]?.textContent).toBe(before);
});
