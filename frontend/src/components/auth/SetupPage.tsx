// First-run setup (P0-13): the owner's email and password, then the authenticator
// secret shown once and its first code. Restyled and routed at /setup by P0-22.
import { useState, type SyntheticEvent } from "react";

import { apiFetch, problemDetail } from "../../lib/fetch";
import { BUTTON, ERROR, FORM, INPUT, LABEL, PAGE, TITLE } from "./styles";

interface Started {
  otpauth_uri: string;
  setup_token: string;
}

function secretOf(uri: string): string {
  return new URL(uri).searchParams.get("secret") ?? "";
}

export function SetupPage({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [started, setStarted] = useState<Started | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start(event: SyntheticEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const response = await apiFetch("/v1/setup", {
      method: "POST",
      body: JSON.stringify({ email, password, timezone }),
    });
    setBusy(false);
    if (!response.ok) {
      setError(await problemDetail(response));
      return;
    }
    setPassword("");
    setStarted((await response.json()) as Started);
  }

  async function confirm(event: SyntheticEvent) {
    event.preventDefault();
    if (started === null) return;
    setBusy(true);
    setError(null);
    const response = await apiFetch("/v1/setup/totp", {
      method: "POST",
      body: JSON.stringify({
        setup_token: started.setup_token,
        code: code.trim(),
      }),
    });
    setBusy(false);
    if (!response.ok) {
      setError(await problemDetail(response));
      return;
    }
    onDone();
  }

  return (
    <div className={PAGE}>
      <h1 className={TITLE}>Set up Tumnis</h1>
      {started === null ? (
        <form
          aria-label="Set up"
          className={FORM}
          onSubmit={(e) => void start(e)}
        >
          <label className={LABEL}>
            Email
            <input
              className={INPUT}
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
              }}
            />
          </label>
          <label className={LABEL}>
            Password
            <input
              className={INPUT}
              type="password"
              autoComplete="new-password"
              minLength={12}
              required
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
              }}
            />
          </label>
          <button type="submit" className={BUTTON} disabled={busy}>
            Continue
          </button>
        </form>
      ) : (
        <form
          aria-label="Two-step verification"
          className={FORM}
          onSubmit={(e) => void confirm(e)}
        >
          <p>
            Add this account to your authenticator app. It is shown only once.
          </p>
          <p>
            <a href={started.otpauth_uri}>Open in authenticator</a>
          </p>
          <p>
            Secret: <code>{secretOf(started.otpauth_uri)}</code>
          </p>
          <label className={LABEL}>
            Authentication code
            <input
              className={INPUT}
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
          <button type="submit" className={BUTTON} disabled={busy}>
            Verify
          </button>
        </form>
      )}
      {error !== null && (
        <p role="alert" className={ERROR}>
          {error}
        </p>
      )}
    </div>
  );
}
