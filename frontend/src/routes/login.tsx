// Sign in (P0-13, SEC-1): the password step, then the authentication code. A minimal
// form; P0-22 restyles it.
import { useState, type SyntheticEvent } from "react";

import { apiFetch, problemDetail } from "../lib/fetch";

type Step = { kind: "password" } | { kind: "code"; preauth: string };

export function LoginPage({ onSignedIn }: { onSignedIn: () => void }) {
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submitPassword(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await apiFetch("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setBusy(false);
    if (!response.ok) {
      setError(await problemDetail(response));
      return;
    }
    const body = (await response.json()) as { preauth: string };
    setPassword("");
    setStep({ kind: "code", preauth: body.preauth });
  }

  async function submitCode(event: SyntheticEvent) {
    event.preventDefault();
    if (step.kind !== "code") return;
    setBusy(true);
    setError(null);
    const response = await apiFetch("/v1/auth/totp", {
      method: "POST",
      body: JSON.stringify({ preauth: step.preauth, code: code.trim() }),
    });
    setBusy(false);
    if (!response.ok) {
      setError(await problemDetail(response));
      return;
    }
    onSignedIn();
  }

  return (
    <main>
      <h1>Sign in</h1>
      {step.kind === "password" ? (
        <form onSubmit={(e) => void submitPassword(e)}>
          <label>
            Email
            <input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
              }}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
              }}
            />
          </label>
          <button type="submit" disabled={busy}>
            Sign in
          </button>
        </form>
      ) : (
        <form onSubmit={(e) => void submitCode(e)}>
          <label>
            Authentication code
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              required
              value={code}
              onChange={(e) => {
                setCode(e.target.value);
              }}
            />
          </label>
          <button type="submit" disabled={busy}>
            Verify
          </button>
        </form>
      )}
      {error !== null && <p role="alert">{error}</p>}
    </main>
  );
}
