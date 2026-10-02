// Settings > Connections (P3-02, FR-14.4): the accounts Tumnis syncs from. Each card shows
// a status chip (with what went wrong when it is not healthy), when it last synced, and
// one action: Reconnect when the sign-in expired, Sign in when it never finished, Sync now
// otherwise. Details opens the connection's settings and Disconnect. Connect account
// opens the wizard. The provider sends the browser back here after its sign-in, with
// `?connection=<id>` (and `&connect_error=1` when it was declined), which opens that card.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { ConnectionOut } from "../../../api/types.gen";
import { useWrite } from "../../../lib/fetch";
import { badge } from "../../common/ui";
import {
  BUTTON,
  CARD,
  ERROR,
  HEADING,
  HINT,
  SECONDARY,
  SECTION,
} from "../styles";
import {
  connectionsQuery,
  problemText,
  signInUrl,
  storeConnection,
  syncConnection,
} from "./api";
import { ConnectionDetail } from "./ConnectionDetail";
import { ConnectWizard } from "./ConnectWizard";
import { lastSyncedText, STATUS_CHIP } from "./status";

const REFRESH_MS = 60_000;

export interface ConnectionsProps {
  /** Sends the browser to the provider's sign-in page (the whole window by default). */
  openUrl?: (url: string) => void;
  /** How long to wait between reads of the sign-in page's URL. */
  pollMs?: number;
}

/** What the provider's redirect back here says (read once, on open). */
function returnedFrom(): { id: string | null; declined: boolean } {
  const search = new URLSearchParams(window.location.search);
  return {
    id: search.get("connection"),
    declined: search.get("connect_error") === "1",
  };
}

export function Connections({
  openUrl = (url) => {
    window.location.assign(url);
  },
  pollMs,
}: ConnectionsProps) {
  // No live message carries a connection, so the list refreshes while it is open: the
  // background syncs move status and last sync on their own.
  const connections = useQuery({
    ...connectionsQuery(),
    refetchInterval: REFRESH_MS,
  });
  const [returned] = useState(returnedFrom);
  const [openId, setOpenId] = useState<string | null>(returned.id);
  const [message, setMessage] = useState<string | null>(
    returned.declined
      ? "The sign-in did not finish, so nothing was connected. Try again."
      : null,
  );
  const [wizard, setWizard] = useState(false);
  const [signingIn, setSigningIn] = useState<string | null>(null);

  async function signIn(id: string) {
    setMessage(null);
    setSigningIn(id);
    try {
      openUrl(await signInUrl(id, { pollMs }));
    } catch (error) {
      setMessage(problemText(error, "The sign-in could not start."));
    } finally {
      setSigningIn(null);
    }
  }

  const items = connections.data ?? [];
  return (
    <section aria-labelledby="connections-title" className={SECTION}>
      <h2 id="connections-title" className={HEADING}>
        Connections
      </h2>
      <p className={HINT}>
        The accounts Tumnis reads mail, notes and messages from. It keeps
        syncing in the background and tells you here when an account needs you.
      </p>
      <p role="status" className={HINT}>
        {message}
      </p>
      {connections.isError && (
        <p role="alert" className={ERROR}>
          The connections could not be loaded.
        </p>
      )}
      {connections.isSuccess && items.length === 0 && (
        <p className={HINT}>No account is connected yet.</p>
      )}
      <ul className="flex flex-col gap-2">
        {items.map((connection) => (
          <li key={connection.id}>
            <ConnectionCard
              connection={connection}
              open={openId === connection.id}
              signingIn={signingIn === connection.id}
              onToggle={() => {
                setOpenId(openId === connection.id ? null : connection.id);
              }}
              onSignIn={() => void signIn(connection.id)}
              onMessage={setMessage}
            />
          </li>
        ))}
      </ul>
      <div>
        <button
          type="button"
          className={BUTTON}
          onClick={() => {
            setMessage(null);
            setWizard(true);
          }}
        >
          Connect account
        </button>
      </div>
      {wizard && (
        <ConnectWizard
          openUrl={openUrl}
          pollMs={pollMs}
          onClose={(done) => {
            setWizard(false);
            if (done) setMessage(done);
          }}
        />
      )}
    </section>
  );
}

function ConnectionCard({
  connection,
  open,
  signingIn,
  onToggle,
  onSignIn,
  onMessage,
}: {
  connection: ConnectionOut;
  open: boolean;
  signingIn: boolean;
  onToggle: () => void;
  onSignIn: () => void;
  onMessage: (message: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const chip = STATUS_CHIP[connection.status];
  const detailId = `connection-${connection.id}-detail`;

  const syncNow = useWrite<{ idempotencyKey?: string }, ConnectionOut>({
    mutationFn: ({ idempotencyKey }) =>
      syncConnection(connection.id, idempotencyKey),
    onSuccess: (next) => {
      storeConnection(queryClient, next);
      onMessage(`Syncing ${connection.account_label}.`);
    },
    onError: (error) => {
      onMessage(problemText(error, "The sync could not start."));
    },
  });

  let action;
  if (connection.status === "auth_required") {
    action = (
      <button
        type="button"
        className={BUTTON}
        disabled={signingIn}
        onClick={onSignIn}
      >
        Reconnect
      </button>
    );
  } else if (connection.status === "pending_auth") {
    action = (
      <button
        type="button"
        className={BUTTON}
        disabled={signingIn}
        onClick={onSignIn}
      >
        Sign in
      </button>
    );
  } else if (connection.status !== "disabled") {
    action = (
      <button
        type="button"
        className={SECONDARY}
        disabled={syncNow.isPending}
        onClick={() => {
          onMessage(null);
          syncNow.mutate({});
        }}
      >
        Sync now
      </button>
    );
  }

  return (
    <div role="group" aria-label={connection.account_label} className={CARD}>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 font-medium [overflow-wrap:anywhere]">
          {connection.account_label}
        </p>
        <span className={badge(chip.tone)}>{chip.label}</span>
      </div>
      {connection.status_detail && (
        <p
          className={`text-sm [overflow-wrap:anywhere] ${
            chip.tone === "danger" ? "text-danger" : "text-muted"
          }`}
        >
          {connection.status_detail}
        </p>
      )}
      <p className={HINT}>{lastSyncedText(connection.last_success_at)}</p>
      <div className="flex flex-wrap gap-2">
        {action}
        <button
          type="button"
          className={SECONDARY}
          aria-expanded={open}
          aria-controls={open ? detailId : undefined}
          onClick={onToggle}
        >
          {open ? "Hide details" : "Details"}
        </button>
      </div>
      {open && (
        <div id={detailId}>
          <ConnectionDetail connection={connection} onMessage={onMessage} />
        </div>
      )}
    </div>
  );
}
