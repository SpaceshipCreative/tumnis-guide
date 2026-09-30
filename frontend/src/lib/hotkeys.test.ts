// The shortcuts listen from the first script on (P0-25, FR-3.3): a `/` pressed while the
// shell is still loading opens quick add once the shell shows it.
import { afterEach, beforeAll, beforeEach, expect, test } from "vitest";

import { uiStore } from "../stores/uiStore";
import { appHotkeys, installHotkeys } from "./hotkeys";

function press(
  key: string,
  init: KeyboardEventInit = {},
  target: HTMLElement = document.body,
): KeyboardEvent {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  target.dispatchEvent(event);
  return event;
}

let enabled = true;

function reset() {
  uiStore.trigger.closeQuickAdd();
  uiStore.trigger.toggleSearch({ open: false });
}

beforeAll(() => {
  installHotkeys(appHotkeys, () => enabled);
});

beforeEach(() => {
  enabled = true;
  reset();
});

afterEach(reset);

test("/ before the app has mounted opens quick add", () => {
  const event = press("/");
  expect(uiStore.getSnapshot().context.quickAddOpen).toBe(true);
  expect(event.defaultPrevented).toBe(true);
});

test("Mod+K opens the palette and closes quick add", () => {
  uiStore.trigger.openQuickAdd();
  press("k", { ctrlKey: true });
  expect(uiStore.getSnapshot().context.searchOpen).toBe(true);
  expect(uiStore.getSnapshot().context.quickAddOpen).toBe(false);
});

test("a slash typed in a field stays in the field", () => {
  const input = document.createElement("input");
  document.body.append(input);
  press("/", {}, input);
  input.remove();
  expect(uiStore.getSnapshot().context.quickAddOpen).toBe(false);
});

test("nothing happens where the shortcuts are off (the sign-in page)", () => {
  enabled = false;
  press("/");
  expect(uiStore.getSnapshot().context.quickAddOpen).toBe(false);
});
