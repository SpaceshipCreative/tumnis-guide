// Purge a connection's or an archived project's ingested content (P3-09, FR-5.10, SEC-3):
// POST /v1/purges {scope, id, reason}. The reason is required (it goes to the audit log),
// so Purge stays disabled until one is typed. The dialog says what goes, that nothing is
// deleted at the provider, and that backups keep the content until they expire.
import { useId, useState } from "react";

import type { PurgeOut } from "../../../api/types.gen";
import { apiWrite, useWrite } from "../../../lib/fetch";
import { ConfirmDialog } from "../ConfirmDialog";
import { ERROR, HINT, INPUT, LABEL } from "../styles";
import { problemText } from "./api";

const BACKUP_DAYS = 35; // the B2 lifecycle (architecture)

export interface PurgeDialogProps {
  scope: "connection" | "project";
  targetId: string;
  name: string;
  onDone: (accepted: PurgeOut) => void;
  onCancel: () => void;
}

export function PurgeDialog({
  scope,
  targetId,
  name,
  onDone,
  onCancel,
}: PurgeDialogProps) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const purge = useWrite<{ reason: string; idempotencyKey?: string }, PurgeOut>(
    {
      mutationFn: ({ reason: why, idempotencyKey }) =>
        apiWrite<PurgeOut>({
          kind: "create",
          method: "POST",
          path: "/purges",
          body: { scope, id: targetId, reason: why },
          idempotencyKey,
        }),
      onSuccess: (accepted) => {
        onDone(accepted);
      },
      onError: (failure) => {
        setError(problemText(failure, "The purge could not be started."));
      },
    },
  );

  return (
    <ConfirmDialog
      title={`Purge ${name}`}
      confirmLabel="Purge"
      busy={purge.isPending || reason.trim() === ""}
      onCancel={onCancel}
      onConfirm={() => {
        setError(null);
        purge.mutate({ reason: reason.trim() });
      }}
    >
      <p className={HINT}>
        {scope === "connection"
          ? "Deletes the email, chat and notes this account synced, with their raw copies. The account stays connected, so a later sync can bring them back."
          : "Deletes the project for good, with the email, chat and notes only it holds and their raw copies."}{" "}
        Tasks keep their links, shown as removed. Nothing is deleted at the
        provider, and backups keep the content until they expire ({BACKUP_DAYS}{" "}
        days).
      </p>
      <label htmlFor={reasonId} className={LABEL}>
        Reason
      </label>
      <textarea
        id={reasonId}
        className={INPUT}
        rows={2}
        maxLength={500}
        value={reason}
        onChange={(e) => {
          setReason(e.target.value);
        }}
      />
      {error && (
        <p role="alert" className={ERROR}>
          {error}
        </p>
      )}
    </ConfirmDialog>
  );
}
