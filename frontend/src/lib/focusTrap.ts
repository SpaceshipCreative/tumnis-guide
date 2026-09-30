// Keys for the aria-modal dialogs (P0-25, NFR-6). Tab and Shift+Tab wrap between the
// dialog's first and last focusable controls, so focus never leaves the modal. Escape
// closes it, except while an IME composition is open: there Escape cancels the
// composition, and the dialog stays.
import type { KeyboardEvent } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  'input:not([disabled]):not([type="hidden"])',
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]",
].join(",");

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.tabIndex >= 0 && !el.closest("[inert]"),
  );
}

export function trapTab(event: KeyboardEvent<HTMLElement>): void {
  if (event.key !== "Tab") return;
  const root = event.currentTarget;
  const items = focusables(root);
  const first = items[0];
  const last = items.at(-1);
  if (!first || !last) {
    event.preventDefault();
    return;
  }
  const active = document.activeElement;
  const inside = active instanceof Node && root.contains(active);
  if (event.shiftKey && (!inside || active === first)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (!inside || active === last)) {
    event.preventDefault();
    first.focus();
  }
}

export function dialogKeyDown(
  event: KeyboardEvent<HTMLElement>,
  onClose: () => void,
): void {
  if (event.key === "Escape") {
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    onClose();
    return;
  }
  trapTab(event);
}
