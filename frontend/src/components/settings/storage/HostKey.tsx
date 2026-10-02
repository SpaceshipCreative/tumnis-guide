// Settings > Storage > SFTP host key (P3-14, FR-15.7): the fingerprint of the key the
// server showed, and a field for the one the user reads on the server itself. "Trust this
// server" stays disabled until the two match exactly (stray spaces and line breaks from a
// paste are ignored), so a key is pinned only after a real comparison.
import { useId, useState } from "react";

import { BUTTON, CARD, HINT, INPUT, LABEL } from "../styles";

export const FINGERPRINT_HINT =
  "On the server, run: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub";

export function HostKey({
  fingerprint,
  onConfirm,
  busy = false,
}: {
  fingerprint: string;
  onConfirm: (fingerprint: string) => void;
  busy?: boolean;
}) {
  const fieldId = useId();
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === fingerprint;

  return (
    <div className={CARD}>
      <p>The server presented this host key:</p>
      <code className="break-all font-mono text-sm">{fingerprint}</code>
      <p className={HINT}>{FINGERPRINT_HINT}</p>
      <label className={LABEL} htmlFor={fieldId}>
        Fingerprint from the server
      </label>
      <input
        id={fieldId}
        className={INPUT}
        value={typed}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => {
          setTyped(event.target.value);
        }}
      />
      <button
        type="button"
        className={BUTTON}
        disabled={!matches || busy}
        onClick={() => {
          if (matches) onConfirm(fingerprint);
        }}
      >
        Trust this server
      </button>
    </div>
  );
}
