// Delete an outside file at its source (P3-14, FR-15.12, SEC-3). Tumnis never deletes a
// file it did not create unless the user confirms it here: they type the file's name and
// a reason, and only then does the dialog ask the server for a one-time confirmation
// token and send the delete with it. The file goes at the next folder sync; the audit
// log keeps the reason. Agents have no such path (the server refuses them).
import { useId, useState } from "react";

import type { DeleteConfirmationOut, DeleteOut } from "../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import { ConfirmDialog } from "../settings/ConfirmDialog";
import { fieldClass } from "./rail/RailSection";

export function DeleteAtSourceDialog({
  documentId,
  fileName,
  onDone,
  onCancel,
}: {
  documentId: string;
  fileName: string;
  onDone: (outcome: string) => void;
  onCancel: () => void;
}) {
  const ids = { name: useId(), reason: useId() };
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const docId = encodeURIComponent(documentId);
  const ready = typed.trim() === fileName && reason.trim() !== "";

  const remove = useWrite<
    { reason: string; idempotencyKey?: string },
    DeleteOut
  >({
    mutationFn: async ({ reason: why, idempotencyKey }) => {
      const confirmation = await apiWrite<DeleteConfirmationOut>({
        kind: "create",
        method: "POST",
        path: `/knowledge/documents/${docId}/delete-confirmation`,
        idempotencyKey: crypto.randomUUID(),
      });
      return apiWrite<DeleteOut>({
        kind: "create",
        method: "POST",
        path: `/knowledge/documents/${docId}/delete-at-source`,
        body: { confirm_token: confirmation.confirm_token, reason: why },
        idempotencyKey,
      });
    },
    onSuccess: (result) => {
      onDone(result.outcome);
    },
    onError: (failure) => {
      setError(
        failure instanceof ApiError
          ? (failure.problem.detail ?? failure.message)
          : "The file could not be deleted.",
      );
    },
  });

  return (
    <ConfirmDialog
      title="Delete this file at its source?"
      confirmLabel="Delete at source"
      busy={!ready || remove.isPending}
      onCancel={() => {
        // Once the delete is on its way it cannot be called back: Cancel and Escape
        // wait for it rather than claim to stop it.
        if (!remove.isPending) onCancel();
      }}
      onConfirm={() => {
        if (!ready) return;
        setError(null);
        remove.mutate({ reason: reason.trim() });
      }}
    >
      <p className="text-sm">
        Tumnis did not create {fileName}. Deleting it here deletes it in your
        folder too, at the next sync. This cannot be undone from Tumnis.
      </p>
      <label htmlFor={ids.name} className="text-sm">
        Type the file name to confirm
      </label>
      <input
        id={ids.name}
        className={fieldClass}
        autoComplete="off"
        spellCheck={false}
        value={typed}
        onChange={(event) => {
          setTyped(event.target.value);
        }}
      />
      <label htmlFor={ids.reason} className="text-sm">
        Why delete it?
      </label>
      <input
        id={ids.reason}
        className={fieldClass}
        maxLength={500}
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
        }}
      />
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </ConfirmDialog>
  );
}
