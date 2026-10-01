// The swap picker (P1-11, J1, NFR-6): a modal dialog. Focus starts inside, Tab stays
// inside, Escape closes it, and focus goes back to what opened it.
import { screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { expect, test, vi } from "vitest";

import { ALTERNATES, planHandlers } from "../../test/msw/planning";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithRouter } from "../../test/render";
import { SwapPicker } from "./SwapPicker";

function Opener({ onPick }: { onPick: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
        }}
      >
        Swap
      </button>
      {open && (
        <SwapPicker
          day="2026-03-09"
          title="Write the proposal"
          onPick={(alternate) => {
            onPick(alternate.id);
            setOpen(false);
          }}
          onClose={() => {
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

test("[P1-11][J1][NFR-6] the swap picker keeps focus inside and gives it back", async () => {
  server.use(...planHandlers(new Recorder()));
  const onPick = vi.fn();
  const { user } = await renderWithRouter(<Opener onPick={onPick} />);
  const opener = screen.getByRole("button", { name: "Swap" });
  await user.click(opener);

  const dialog = screen.getByRole("dialog", { name: "Swap" });
  const options = await within(dialog).findAllByRole("option");
  expect(options).toHaveLength(ALTERNATES.length);
  const cancel = within(dialog).getByRole("button", { name: "Cancel" });
  expect(cancel).toHaveFocus();

  // Tab from the last control wraps to the first: focus never leaves the dialog.
  await user.tab();
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  expect(options[0]).toHaveFocus();
  await user.tab({ shift: true });
  expect(cancel).toHaveFocus();

  await user.keyboard("{Escape}");
  await waitFor(() => {
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  expect(opener).toHaveFocus();
  expect(onPick).not.toHaveBeenCalled();

  // Enter on an option picks it.
  await user.click(opener);
  const first = (
    await within(screen.getByRole("dialog")).findAllByRole("option")
  )[0];
  first?.focus();
  await user.keyboard("{Enter}");
  expect(onPick).toHaveBeenCalledWith(ALTERNATES[0]?.id);
});
