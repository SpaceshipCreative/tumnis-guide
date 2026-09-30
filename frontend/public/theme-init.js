/* global document, localStorage */
// Shows the stored colour theme before the first paint (DS-01, ADR-0012), so a manual
// choice never flashes the system's colours. src/lib/theme.ts owns the key and the values
// and applies later changes; this is a file, not an inline script, because the CSP allows
// only same-origin scripts (P0-16).
try {
  const theme = localStorage.getItem("tumnis.theme");
  if (theme === "light" || theme === "dark") {
    document.documentElement.dataset.theme = theme;
  }
} catch {
  // Storage unavailable: the system's theme.
}
