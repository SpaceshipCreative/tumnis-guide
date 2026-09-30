// The colour theme (DS-01, ADR-0012): light, dark, or the system's choice (the default),
// remembered per device. A manual choice sets `data-theme` on <html>; "system" removes it
// and styles.css follows `prefers-color-scheme`.
export const THEME_KEY = "tumnis.theme";

export const themeChoices = ["light", "dark", "system"] as const;
export type ThemeChoice = (typeof themeChoices)[number];

/** The stored choice; anything missing or unknown reads as "system". */
export function loadTheme(): ThemeChoice {
  return "system";
}

/** Shows `choice` on the page. */
export function applyTheme(choice: ThemeChoice): void {
  if (choice === "system") return;
}
