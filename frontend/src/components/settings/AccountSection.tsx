// Settings > Account (P0-26, SEC-1): who is signed in and their second factor, and moving
// it to a new authenticator app: the current password, then a code from the new app. The
// new secret lives only in this component's state while the dialog is open (the
// mutations keep nothing and are reset on close). A lost phone is `tumnis admin
// reset-totp` on the server.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";

import type { TotpEnrolOut } from "../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import { accountQuery } from "./queries";
import {
  BUTTON,
  ERROR,
  HEADING,
  HINT,
  INPUT,
  LABEL,
  SECONDARY,
  SECTION,
} from "./styles";

const TITLE = "Set up a new authenticator";

function failureOf(error: Error): string {
  return error instanceof ApiError
    ? (error.problem.detail ?? error.message)
    : "That did not work.";
}

function secretOf(uri: string): string {
  try {
    return new URL(uri).searchParams.get("secret") ?? "";
  } catch {
    return "";
  }
}

export function AccountSection() {
  const account = useQuery(accountQuery());
  const [enrolling, setEnrolling] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const data = account.data;

  return (
    <section aria-labelledby="account-title" className={SECTION}>
      <h2 id="account-title" className={HEADING}>
        Account
      </h2>
      {account.isError && (
        <p role="alert" className={ERROR}>
          The account could not be loaded.
        </p>
      )}
      {data && (
        <dl className="flex flex-col gap-2">
          <div>
            <dt className={HINT}>Signed in as</dt>
            <dd className="font-medium [overflow-wrap:anywhere]">
              {data.email}
            </dd>
          </div>
          <div>
            <dt className={HINT}>Second factor</dt>
            <dd>
              {data.totp_confirmed_at
                ? `Authenticator app (TOTP): on since ${new Date(data.totp_confirmed_at).toLocaleDateString()}`
                : "Authenticator app (TOTP): not set up"}
            </dd>
          </div>
        </dl>
      )}
      <p role="status" className={HINT}>
        {done}
      </p>
      <div>
        <button
          type="button"
          className={BUTTON}
          onClick={() => {
            setDone(null);
            setEnrolling(true);
          }}
        >
          {TITLE}
        </button>
      </div>
      <p className={HINT}>
        Lost the phone with your authenticator? The server owner can reset it
        with <code>tumnis admin reset-totp</code>.
      </p>
      {enrolling && (
        <EnrolDialog
          onClose={() => {
            setEnrolling(false);
          }}
          onDone={() => {
            setEnrolling(false);
            setDone(
              "Your new authenticator is set up; the old one no longer works.",
            );
          }}
        />
      )}
    </section>
  );
}

function EnrolDialog({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const ids = { title: useId(), password: useId(), code: useId() };
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState<TotpEnrolOut | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const start = useWrite<
    { password: string; idempotencyKey?: string },
    TotpEnrolOut
  >({
    mutationFn: ({ idempotencyKey, ...body }) =>
      apiWrite<TotpEnrolOut>({
        kind: "create",
        method: "POST",
        path: "/auth/totp/enrol",
        body,
        idempotencyKey,
      }),
    gcTime: 0,
    onSuccess: (enrolment) => {
      setPassword("");
      setPending(enrolment);
    },
    onError: (error) => {
      setFailure(failureOf(error));
    },
  });
  const confirm = useWrite<
    { enrol_token: string; code: string; idempotencyKey?: string },
    undefined
  >({
    mutationFn: ({ idempotencyKey, ...body }) =>
      apiWrite<undefined>({
        kind: "create",
        method: "POST",
        path: "/auth/totp/enrol/confirm",
        body,
        idempotencyKey,
      }),
    gcTime: 0,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountQuery().queryKey });
      close(onDone);
    },
    onError: (error) => {
      setFailure(failureOf(error));
    },
  });

  function close(then: () => void) {
    setPending(null);
    start.reset();
    confirm.reset();
    then();
  }

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    setFailure(null);
    if (pending === null) start.mutate({ password });
    else
      confirm.mutate({ enrol_token: pending.enrol_token, code: code.trim() });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={ids.title}
        className="flex max-h-full w-full max-w-md flex-col gap-3 overflow-y-auto rounded-lg bg-surface p-4 shadow-lg"
        onKeyDown={(event) => {
          if (event.key === "Escape") close(onClose);
        }}
      >
        <h3 id={ids.title} className="text-lg font-semibold">
          {TITLE}
        </h3>
        <form onSubmit={submit} className="flex flex-col gap-3">
          {pending === null ? (
            <div className="flex flex-col gap-1">
              <label htmlFor={ids.password} className={LABEL}>
                Current password
              </label>
              <input
                id={ids.password}
                type="password"
                autoComplete="current-password"
                required
                className={INPUT}
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                }}
              />
            </div>
          ) : (
            <>
              <p>
                Add this key to your new authenticator app, then type the code
                it shows. Your current app keeps working until you confirm.
              </p>
              <code className="rounded bg-surface-muted p-2 text-sm [overflow-wrap:anywhere]">
                {secretOf(pending.otpauth_uri)}
              </code>
              <a href={pending.otpauth_uri} className="text-accent underline">
                Open in authenticator app
              </a>
              <div className="flex flex-col gap-1">
                <label htmlFor={ids.code} className={LABEL}>
                  Code from the new app
                </label>
                <input
                  id={ids.code}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={8}
                  required
                  className={INPUT}
                  value={code}
                  onChange={(e) => {
                    setCode(e.target.value);
                  }}
                />
              </div>
            </>
          )}
          {failure !== null && (
            <p role="alert" className={ERROR}>
              {failure}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <button
              type="button"
              className={SECONDARY}
              onClick={() => {
                close(onClose);
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              className={BUTTON}
              disabled={start.isPending || confirm.isPending}
            >
              {pending === null ? "Continue" : "Confirm"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
