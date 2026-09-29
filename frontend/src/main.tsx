// Placeholder shell (P0-04), now behind sign-in (P0-13): `/login` and `/setup` are the
// minimal forms; every other path checks the session and sends a signed-out visitor to
// `/login`. P0-22 replaces this with the real shell and router.
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { apiFetch } from "./lib/fetch";
import { LoginPage } from "./routes/login";
import { SetupPage } from "./routes/setup";

function goHome() {
  window.location.assign("/");
}

function Shell() {
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    void apiFetch("/v1/auth/sessions?limit=1").then((response) => {
      if (response.status === 401) {
        window.location.replace("/login");
      } else {
        setSignedIn(true);
      }
    });
  }, []);

  async function signOut() {
    await apiFetch("/v1/auth/logout", { method: "POST" });
    window.location.replace("/login");
  }

  return (
    <main>
      <h1>Tumnis Guide</h1>
      {signedIn && (
        <button type="button" onClick={() => void signOut()}>
          Sign out
        </button>
      )}
    </main>
  );
}

function App() {
  switch (window.location.pathname) {
    case "/login":
      return <LoginPage onSignedIn={goHome} />;
    case "/setup":
      return <SetupPage onDone={goHome} />;
    default:
      return <Shell />;
  }
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
