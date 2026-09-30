// App-wide shortcuts (P0-25, FR-3.3, FR-3.9): `/` opens quick add and Mod+K (Ctrl or
// Cmd + K) opens search. `/` is left alone in a field that takes typing (input, textarea,
// select, contenteditable), so it types a slash there; neither fires while an IME is
// composing. Mod+K is not a character, so it works from a field too.
import { useEffect } from "react";

import { uiStore } from "../stores/uiStore";

/** A field that takes typing, where `/` belongs to the field. */
export function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.closest("input, textarea, select, [contenteditable]") !== null
  );
}

export interface Hotkeys {
  onQuickAdd: () => void;
  onSearch: () => void;
}

/** The keydown handler behind `useHotkeys` (exported for tests). */
export function hotkeyHandler({ onQuickAdd, onSearch }: Hotkeys) {
  return (event: KeyboardEvent): void => {
    if (event.isComposing || event.defaultPrevented) return;
    const mod = event.ctrlKey || event.metaKey;
    if (mod && !event.altKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      onSearch();
      return;
    }
    if (
      event.key === "/" &&
      !mod &&
      !event.altKey &&
      !isEditable(event.target)
    ) {
      // Otherwise the slash lands in the title field the dialog focuses.
      event.preventDefault();
      onQuickAdd();
    }
  };
}

/** Listens on the window while `enabled`. */
export function useHotkeys(hotkeys: Hotkeys, enabled = true): void {
  const { onQuickAdd, onSearch } = hotkeys;
  useEffect(() => {
    if (!enabled) return;
    const handler = hotkeyHandler({ onQuickAdd, onSearch });
    window.addEventListener("keydown", handler);
    return () => {
      window.removeEventListener("keydown", handler);
    };
  }, [enabled, onQuickAdd, onSearch]);
}

/** What the shortcuts do: quick add and the search palette replace each other. */
export const appHotkeys: Hotkeys = {
  onQuickAdd: () => {
    uiStore.trigger.toggleSearch({ open: false });
    uiStore.trigger.openQuickAdd();
  },
  onSearch: () => {
    uiStore.trigger.closeQuickAdd();
    uiStore.trigger.toggleSearch({});
  },
};

/**
 * Listens from the first script on, before the app has mounted: a key pressed while the
 * shell is still loading opens its dialog as soon as the shell shows it, instead of being
 * lost. A press the app's own `useHotkeys` sees is already handled (`defaultPrevented`).
 */
export function installHotkeys(hotkeys: Hotkeys, enabled: () => boolean): void {
  const handler = hotkeyHandler(hotkeys);
  window.addEventListener("keydown", (event) => {
    if (enabled()) handler(event);
  });
}
