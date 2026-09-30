// App-wide shortcuts (P0-25, FR-3.3, FR-3.9): `/` opens quick add and Mod+K (Ctrl or
// Cmd + K) opens search. `/` is left alone in a field that takes typing (input, textarea,
// select, contenteditable), so it types a slash there; neither fires while an IME is
// composing. Mod+K is not a character, so it works from a field too.
import { useEffect } from "react";

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
