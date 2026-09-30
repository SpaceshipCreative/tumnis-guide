// Modal dialog keys (P0-25, NFR-6): Tab and Shift+Tab stay inside an aria-modal dialog,
// and Escape closes it, except while an IME composition is open (Escape cancels that).
import { fireEvent, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { dialogKeyDown } from "./focusTrap";

function Dialog({ onClose }: { onClose: () => void }) {
  return (
    <>
      <button type="button">Outside</button>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Test"
        onKeyDown={(event) => {
          dialogKeyDown(event, onClose);
        }}
      >
        <input aria-label="First" />
        <button type="button" disabled>
          Disabled
        </button>
        <a href="/somewhere">Middle</a>
        <button type="button" tabIndex={-1}>
          Skipped
        </button>
        <button type="button">Last</button>
      </div>
    </>
  );
}

test("Tab from the last control wraps to the first, Shift+Tab back again", async () => {
  const user = userEvent.setup();
  render(<Dialog onClose={() => undefined} />);
  const first = screen.getByRole("textbox", { name: "First" });
  const last = screen.getByRole("button", { name: "Last" });

  first.focus();
  await user.tab();
  expect(screen.getByRole("link", { name: "Middle" })).toHaveFocus();
  await user.tab();
  expect(last).toHaveFocus();
  await user.tab();
  expect(first).toHaveFocus();
  await user.tab({ shift: true });
  expect(last).toHaveFocus();
});

test("Escape closes the dialog, but not while an IME composition is open", () => {
  const onClose = vi.fn();
  render(<Dialog onClose={onClose} />);
  const first = screen.getByRole("textbox", { name: "First" });

  const composing = fireEvent.keyDown(first, {
    key: "Escape",
    isComposing: true,
  });
  expect(onClose).not.toHaveBeenCalled();
  expect(composing).toBe(true); // not default-prevented: the IME gets it

  // Safari: the key that ends a composition has keyCode 229, not isComposing.
  fireEvent.keyDown(first, { key: "Escape", keyCode: 229 });
  expect(onClose).not.toHaveBeenCalled();

  const plain = fireEvent.keyDown(first, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(plain).toBe(false);
});
