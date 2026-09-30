// The colour theme and the sidebar width are device state (DS-01, ADR-0004, ADR-0012):
// "system" is the default, a manual choice shows at once and survives a reload, and the
// collapsed sidebar is remembered the same way.
import { beforeEach, expect, test } from "vitest";

import { createUiStore, SIDEBAR_KEY } from "../stores/uiStore";
import { loadTheme, THEME_KEY } from "./theme";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

test("[DS-01][UX 11] T-DS-01-01 theme defaults to system and remembers a manual choice", () => {
  const store = createUiStore();
  const root = document.documentElement;
  expect(store.getSnapshot().context.theme).toBe("system");
  expect(root.hasAttribute("data-theme")).toBe(false);

  store.trigger.setTheme({ theme: "dark" });
  expect(root.dataset.theme).toBe("dark");
  expect(localStorage.getItem(THEME_KEY)).toBe("dark");
  expect(createUiStore().getSnapshot().context.theme).toBe("dark");

  store.trigger.setTheme({ theme: "light" });
  expect(root.dataset.theme).toBe("light");
  expect(createUiStore().getSnapshot().context.theme).toBe("light");

  // Back to the system's choice: no attribute, and a reload starts from "system".
  store.trigger.setTheme({ theme: "system" });
  expect(root.hasAttribute("data-theme")).toBe(false);
  expect(createUiStore().getSnapshot().context.theme).toBe("system");

  // Junk in storage reads as the default.
  localStorage.setItem(THEME_KEY, "neon");
  expect(loadTheme()).toBe("system");
});

test("[DS-01][UX 11] T-DS-01-02 the collapsed sidebar is remembered per device", () => {
  const store = createUiStore();
  expect(store.getSnapshot().context.sidebarCollapsed).toBe(false);

  store.trigger.setSidebarCollapsed({ collapsed: true });
  expect(store.getSnapshot().context.sidebarCollapsed).toBe(true);
  expect(localStorage.getItem(SIDEBAR_KEY)).toBe("true");
  expect(createUiStore().getSnapshot().context.sidebarCollapsed).toBe(true);

  store.trigger.setSidebarCollapsed({ collapsed: false });
  expect(createUiStore().getSnapshot().context.sidebarCollapsed).toBe(false);

  // The drawer is never remembered: a reload starts with it closed.
  store.trigger.setNavOpen({ open: true });
  expect(store.getSnapshot().context.navOpen).toBe(true);
  expect(createUiStore().getSnapshot().context.navOpen).toBe(false);
});
