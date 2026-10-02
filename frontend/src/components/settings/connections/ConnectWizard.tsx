// Connect an account (P3-02, FR-14.4): pick what to connect, read and accept its consent
// notice when it has one, name the account and how far back to read, then create the
// connection and, for a provider behind a sign-in, start it and send the browser to the
// provider's page once the server has it ready.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";

import type { ConnectionOut, ProviderOut } from "../../../api/types.gen";
import { useWrite } from "../../../lib/fetch";
import { DIALOG_BACKDROP, DIALOG_PANEL, DIALOG_TITLE } from "../../common/ui";
import { BUTTON, ERROR, HINT, INPUT, LABEL, SECONDARY } from "../styles";
import {
  createConnection,
  problemText,
  providersQuery,
  signInUrl,
  storeConnection,
} from "./api";

const DEFAULT_BACKFILL_DAYS = 30;
const MAX_BACKFILL_DAYS = 3650;

export function ConnectWizard({
  openUrl,
  pollMs,
  onClose,
}: {
  openUrl: (url: string) => void;
  pollMs?: number | undefined;
  /** Closes the wizard; `done` is what to tell the user when something was made. */
  onClose: (done?: string) => void;
}) {
  const queryClient = useQueryClient();
  const providers = useQuery(providersQuery());
  const titleId = useId();
  const ids = {
    provider: useId(),
    consent: useId(),
    label: useId(),
    backfill: useId(),
  };
  const first = useRef<HTMLSelectElement>(null);
  const [providerName, setProviderName] = useState("");
  const [consent, setConsent] = useState(false);
  const [label, setLabel] = useState("");
  const [backfill, setBackfill] = useState(String(DEFAULT_BACKFILL_DAYS));
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<"form" | "signing-in">("form");

  const list = providers.data ?? [];
  const provider: ProviderOut | undefined =
    list.find((p) => p.provider === providerName) ?? list[0];
  const cap = Math.min(
    provider?.backfill_cap_days ?? MAX_BACKFILL_DAYS,
    MAX_BACKFILL_DAYS,
  );

  useEffect(() => {
    first.current?.focus();
  }, [providers.isSuccess]);

  const create = useWrite<
    { provider: ProviderOut; idempotencyKey?: string },
    ConnectionOut
  >({
    mutationFn: ({ provider: chosen, idempotencyKey }) =>
      createConnection(
        {
          provider: chosen.provider,
          account_label: label.trim(),
          consent_acknowledged: chosen.consent_notice != null && consent,
          settings: { backfill_days: Number(backfill) },
        },
        idempotencyKey,
      ),
    onSuccess: async (created, { provider: chosen }) => {
      storeConnection(queryClient, created);
      if (chosen.auth === "none") {
        onClose(`Connected ${created.account_label}.`);
        return;
      }
      setStep("signing-in");
      try {
        openUrl(await signInUrl(created.id, { pollMs }));
      } catch (failure) {
        onClose(
          `${created.account_label} is waiting for you to sign in. ${problemText(
            failure,
            "The sign-in could not start.",
          )}`,
        );
      }
    },
    onError: (failure) => {
      setError(problemText(failure, "The account could not be connected."));
    },
  });

  const days = Number(backfill);
  const ready =
    provider !== undefined &&
    (provider.consent_notice == null || consent) &&
    label.trim() !== "" &&
    Number.isInteger(days) &&
    days >= 1 &&
    days <= cap;

  return (
    <div className={DIALOG_BACKDROP}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={DIALOG_PANEL}
        onKeyDown={(event) => {
          if (event.key === "Escape" && step === "form") onClose();
        }}
      >
        <h3 id={titleId} className={DIALOG_TITLE}>
          Connect an account
        </h3>
        {providers.isPending && <p className={HINT}>Loading…</p>}
        {providers.isError && (
          <p role="alert" className={ERROR}>
            What can be connected could not be loaded.
          </p>
        )}
        {step === "signing-in" ? (
          <p role="status" className={HINT}>
            Opening {provider?.label ?? "the provider"} to sign in…
          </p>
        ) : (
          provider && (
            <form
              className="flex min-w-0 flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!ready) return;
                setError(null);
                create.mutate({ provider });
              }}
            >
              <label htmlFor={ids.provider} className={LABEL}>
                Service
              </label>
              <select
                id={ids.provider}
                ref={first}
                className={INPUT}
                value={provider.provider}
                onChange={(e) => {
                  setProviderName(e.target.value);
                  setConsent(false);
                }}
              >
                {list.map((p) => (
                  <option key={p.provider} value={p.provider}>
                    {p.label}
                  </option>
                ))}
              </select>
              {provider.consent_notice && (
                <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface-muted p-3">
                  <p className="text-sm [overflow-wrap:anywhere]">
                    {provider.consent_notice}
                  </p>
                  <label
                    htmlFor={ids.consent}
                    className="flex min-h-11 items-center gap-2 text-sm"
                  >
                    <input
                      id={ids.consent}
                      type="checkbox"
                      className="size-5"
                      checked={consent}
                      onChange={(e) => {
                        setConsent(e.target.checked);
                      }}
                    />
                    I understand and agree
                  </label>
                </div>
              )}
              <label htmlFor={ids.label} className={LABEL}>
                Account name
              </label>
              <input
                id={ids.label}
                className={INPUT}
                maxLength={120}
                placeholder="Work mail"
                value={label}
                onChange={(e) => {
                  setLabel(e.target.value);
                }}
              />
              <label htmlFor={ids.backfill} className={LABEL}>
                Days to look back
              </label>
              <input
                id={ids.backfill}
                className={INPUT}
                type="number"
                inputMode="numeric"
                min={1}
                max={cap}
                value={backfill}
                onChange={(e) => {
                  setBackfill(e.target.value);
                }}
              />
              <p className={HINT}>Up to {cap} days.</p>
              {error && (
                <p role="alert" className={ERROR}>
                  {error}
                </p>
              )}
              <div className="flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  className={SECONDARY}
                  onClick={() => {
                    onClose();
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className={BUTTON}
                  disabled={!ready || create.isPending}
                >
                  Connect
                </button>
              </div>
            </form>
          )
        )}
      </div>
    </div>
  );
}
