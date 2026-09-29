// Placeholder shell (P0-04): the `main` landmark, the product name and a sign-in link,
// enough for the @smoke spec and the PR preview. P0-22 replaces it with the real shell.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

function Shell() {
  return (
    <main>
      <h1>Tumnis Guide</h1>
      <a href="/sign-in">Sign in</a>
    </main>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Shell />
    </StrictMode>,
  );
}
