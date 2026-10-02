// A connection's details (P3-02, FR-14.4): its next sync, its name and sync settings
// (PATCH at the version read, the other settings kept as they are), and Disconnect, which
// asks for a reason (audited) and says that what it synced stays until purged.
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import type { ConnectionOut } from "../../../api/types.gen";
import { ConflictError, useWrite } from "../../../lib/fetch";
import { ConfirmDialog } from "../ConfirmDialog";
import { DANGER, ERROR, HINT, INPUT, LABEL, SECONDARY } from "../styles";
import {
  connectionsQueryKey,
  disconnectConnection,
  dropConnection,
  problemText,
  storeConnection,
  updateConnection,
} from "./api";
import { relativeTime } from "./status";

const MAX_BACKFILL_DAYS = 3650;
const MAX_EVERY_MIN = 1440;

function nextSyncText(connection: ConnectionOut): string {
  if (
    connection.status === "auth_required" ||
    connection.status === "pending_auth"
  )
    return "Syncing waits until you sign in.";
  if (!connection.next_sync_at) return "Next sync: as soon as possible.";
  return `Next sync ${relativeTime(new Date(connection.next_sync_at))}.`;
}

/** A 409's `current` when it is this connection as the server shows it. */
function isConnection(value: unknown, id: string): value is ConnectionOut {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Partial<ConnectionOut>;
  return (
    row.id === id &&
    typeof row.version === "number" &&
    typeof row.settings === "object"
  );
}

export function ConnectionDetail({
  connection,
  onMessage,
}: {
  connection: ConnectionOut;
  onMessage: (message: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const ids = {
    label: useId(),
    backfill: useId(),
    every: useId(),
    reason: useId(),
  };
  // The connection as this form loaded it. A save sends its version (and keeps its other
  // settings), never the version of a later read, so an edit made elsewhere since the
  // form loaded answers 409 instead of being overwritten by the fields shown here.
  const [loaded, setLoaded] = useState(connection);
  const [label, setLabel] = useState(connection.account_label);
  const [backfill, setBackfill] = useState(
    String(connection.settings.backfill_days ?? 30),
  );
  const [every, setEvery] = useState(
    connection.settings.sync_every_min == null
      ? ""
      : String(connection.settings.sync_every_min),
  );
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");

  const save = useWrite<
    { body: Parameters<typeof updateConnection>[1]; idempotencyKey?: string },
    ConnectionOut
  >({
    mutationFn: ({ body, idempotencyKey }) =>
      updateConnection(loaded, body, idempotencyKey),
    onSuccess: (next) => {
      setLoaded(next);
      storeConnection(queryClient, next);
      onMessage(`Saved ${next.account_label}.`);
    },
    onError: (failure) => {
      if (failure instanceof ConflictError) {
        // The 409 carries the connection as it is now: the form shows it, to edit again.
        if (isConnection(failure.current, loaded.id)) {
          reload(failure.current);
          storeConnection(queryClient, failure.current);
        }
        void queryClient.invalidateQueries({
          queryKey: connectionsQueryKey(),
        });
      }
      setError(problemText(failure, "The settings could not be saved."));
    },
  });

  const disconnect = useWrite<
    { reason: string; idempotencyKey?: string },
    undefined
  >({
    mutationFn: ({ reason: why, idempotencyKey }) =>
      disconnectConnection(connection.id, why, idempotencyKey),
    onSuccess: () => {
      setConfirming(false);
      dropConnection(queryClient, connection.id);
      onMessage(
        `Disconnected ${connection.account_label}. What it synced stays until you purge it.`,
      );
    },
    onError: (failure) => {
      setConfirming(false);
      setError(problemText(failure, "The account could not be disconnected."));
    },
  });

  function reload(next: ConnectionOut) {
    setLoaded(next);
    setLabel(next.account_label);
    setBackfill(String(next.settings.backfill_days ?? 30));
    setEvery(
      next.settings.sync_every_min == null
        ? ""
        : String(next.settings.sync_every_min),
    );
  }

  const backfillDays = Number(backfill);
  const everyMin = every.trim() === "" ? null : Number(every);
  const valid =
    label.trim() !== "" &&
    Number.isInteger(backfillDays) &&
    backfillDays >= 1 &&
    backfillDays <= MAX_BACKFILL_DAYS &&
    (everyMin === null ||
      (Number.isInteger(everyMin) &&
        everyMin >= 1 &&
        everyMin <= MAX_EVERY_MIN));

  return (
    <div className="flex min-w-0 flex-col gap-3 border-t border-border pt-3">
      <p className={HINT}>{nextSyncText(connection)}</p>
      <form
        className="flex min-w-0 flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid) {
            setError("Check the values: whole numbers within the limits.");
            return;
          }
          setError(null);
          save.mutate({
            body: {
              account_label: label.trim(),
              settings: {
                ...loaded.settings,
                backfill_days: backfillDays,
                sync_every_min: everyMin,
              },
            },
          });
        }}
      >
        <label htmlFor={ids.label} className={LABEL}>
          Name
        </label>
        <input
          id={ids.label}
          className={INPUT}
          maxLength={120}
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
          max={MAX_BACKFILL_DAYS}
          value={backfill}
          onChange={(e) => {
            setBackfill(e.target.value);
          }}
        />
        <label htmlFor={ids.every} className={LABEL}>
          Sync every (minutes)
        </label>
        <input
          id={ids.every}
          className={INPUT}
          type="number"
          inputMode="numeric"
          min={1}
          max={MAX_EVERY_MIN}
          placeholder="Provider default"
          value={every}
          onChange={(e) => {
            setEvery(e.target.value);
          }}
        />
        {error && (
          <p role="alert" className={ERROR}>
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={SECONDARY} disabled={save.isPending}>
            Save settings
          </button>
          <button
            type="button"
            className={DANGER}
            onClick={() => {
              setError(null);
              setReason("");
              setConfirming(true);
            }}
          >
            Disconnect
          </button>
        </div>
      </form>
      {confirming && (
        <ConfirmDialog
          title={`Disconnect ${connection.account_label}?`}
          confirmLabel="Disconnect"
          busy={disconnect.isPending || reason.trim() === ""}
          onCancel={() => {
            setConfirming(false);
          }}
          onConfirm={() => {
            disconnect.mutate({ reason: reason.trim() });
          }}
        >
          <p className={HINT}>
            Tumnis stops syncing and forgets the sign-in. What it already synced
            stays until you purge it.
          </p>
          <label htmlFor={ids.reason} className={LABEL}>
            Reason
          </label>
          <textarea
            id={ids.reason}
            className={INPUT}
            rows={2}
            maxLength={500}
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
            }}
          />
        </ConfirmDialog>
      )}
    </div>
  );
}
