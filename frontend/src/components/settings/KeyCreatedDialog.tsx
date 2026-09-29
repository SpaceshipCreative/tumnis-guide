// A new or rotated API key, shown once (P0-14, P0-26, FR-9.3): the secret lives only in
// the caller's state while this dialog is open, never in the query cache.
import { useId, useState } from "react";

import { BUTTON, SECONDARY } from "./styles";

export function KeyCreatedDialog({
  secret,
  onClose,
}: {
  secret: string;
  onClose: () => void;
}) {
  const titleId = useId();
  const [copied, setCopied] = useState(false);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex w-full max-w-md flex-col gap-3 rounded-lg bg-surface p-4 shadow-lg"
      >
        <h3 id={titleId} className="text-lg font-semibold">
          Copy your new key
        </h3>
        <p>It is shown only now. Store it somewhere safe before closing.</p>
        <code className="rounded bg-surface-muted p-2 text-sm [overflow-wrap:anywhere]">
          {secret}
        </code>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {copied && <span role="status">Copied</span>}
          <button
            type="button"
            className={SECONDARY}
            onClick={() => {
              void navigator.clipboard.writeText(secret).then(() => {
                setCopied(true);
              });
            }}
          >
            Copy
          </button>
          <button type="button" className={BUTTON} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
