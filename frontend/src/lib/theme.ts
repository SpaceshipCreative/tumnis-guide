// The colour theme (DS-01, ADR-0012): light, dark, or the system's choice (the default),
// remembered per device. A manual choice sets `data-theme` on <html>; "system" removes it
// and styles.css follows `prefers-color-scheme`. public/theme-init.js applies the stored
// choice before the first paint with the same key and values.
import { safeGetItem, safeSetItem } from "./storage";

export const THEME_KEY = "tumnis.theme";

export const themeChoices = ["light", "dark", "system"] as const;
export type ThemeChoice = (typeof themeChoices)[number];

function isThemeChoice(value: unknown): value is ThemeChoice {
  return themeChoices.some((choice) => choice === value);
}

/** The stored choice; anything missing or unknown reads as "system". */
export function loadTheme(): ThemeChoice {
  const stored = safeGetItem(THEME_KEY);
  return isThemeChoice(stored) ? stored : "system";
}

/** Remembers `choice` on this device. */
export function saveTheme(choice: ThemeChoice): void {
  safeSetItem(THEME_KEY, choice);
}

/** Shows `choice` on the page. */
export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.dataset.theme = choice;
}
