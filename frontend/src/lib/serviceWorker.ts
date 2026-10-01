// Registers the service worker vite-plugin-pwa builds from src/sw.ts (/sw.js: precaching
// the shell and its assets, nothing under /v1; push, P4-05). An external script, not an
// injected inline one,
// so the strict CSP holds (P0-16). Production builds only: in development a cached
// shell would hide the code being edited.
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      // no service worker: the app still works online
    });
  });
}
