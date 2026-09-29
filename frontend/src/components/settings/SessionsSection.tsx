// Settings > Sessions (P0-26, SEC-1, R-21): this user's signed-in devices, this one
// marked. "Sign out other devices" (DELETE /v1/auth/sessions) keeps this one; a single
// device can be signed out too. Both ask first.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { SessionOut } from "../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import { ConfirmDialog } from "./ConfirmDialog";
import { sessionsQuery } from "./queries";
import {
  CARD,
  DANGER,
  ERROR,
  HEADING,
  HINT,
  SECONDARY,
  SECTION,
} from "./styles";

type Target = { kind: "others" } | { kind: "one"; session: SessionOut };

function device(session: SessionOut): string {
  return session.device_label ?? session.user_agent ?? "Unknown device";
}

export function SessionsSection() {
  const queryClient = useQueryClient();
  const sessions = useQuery(sessionsQuery());
  const [target, setTarget] = useState<Target | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const signOut = useWrite<
    { target: Target; idempotencyKey?: string },
    undefined
  >({
    mutationFn: ({ target: chosen, idempotencyKey }) =>
      apiWrite<undefined>({
        kind: "create",
        method: "DELETE",
        path:
          chosen.kind === "others"
            ? "/auth/sessions"
            : `/auth/sessions/${encodeURIComponent(chosen.session.id)}`,
        idempotencyKey,
      }),
    onError: (error) => {
      setFailure(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "That did not work.",
      );
    },
    onSettled: () => {
      setTarget(null);
      void queryClient.invalidateQueries({
        queryKey: sessionsQuery().queryKey,
      });
    },
  });

  const items = sessions.data?.items ?? [];
  const others = items.filter((session) => !session.current).length;
  return (
    <section aria-labelledby="sessions-title" className={SECTION}>
      <h2 id="sessions-title" className={HEADING}>
        Sessions
      </h2>
      <p className={HINT}>
        Devices signed in to this account. A session ends after 30 days without
        use.
      </p>
      <div>
        <button
          type="button"
          className={DANGER}
          disabled={signOut.isPending}
          onClick={() => {
            setFailure(null);
            setTarget({ kind: "others" });
          }}
        >
          Sign out other devices
        </button>
      </div>
      {failure !== null && (
        <p role="alert" className={ERROR}>
          {failure}
        </p>
      )}
      {sessions.isError && (
        <p role="alert" className={ERROR}>
          The sessions could not be loaded.
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {items.map((session) => (
          <li key={session.id} className={CARD}>
            <p className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium [overflow-wrap:anywhere]">
                {device(session)}
              </span>
              {session.current && (
                <span className="rounded bg-surface-muted px-2 text-sm">
                  This device
                </span>
              )}
            </p>
            <p className={HINT}>
              {session.source_ip ? `${session.source_ip}, ` : ""}last seen{" "}
              {new Date(session.last_seen_at).toLocaleString()}
            </p>
            {!session.current && (
              <div>
                <button
                  type="button"
                  className={SECONDARY}
                  disabled={signOut.isPending}
                  onClick={() => {
                    setFailure(null);
                    setTarget({ kind: "one", session });
                  }}
                >
                  Sign out this device
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {target !== null && (
        <ConfirmDialog
          title={
            target.kind === "others"
              ? "Sign out every other device?"
              : "Sign out this device?"
          }
          confirmLabel="Sign out"
          busy={signOut.isPending}
          onCancel={() => {
            setTarget(null);
          }}
          onConfirm={() => {
            signOut.mutate({ target });
          }}
        >
          <p>
            {target.kind === "others"
              ? `${String(others)} other ${others === 1 ? "device" : "devices"} will need to sign in again. This one stays signed in.`
              : `${device(target.session)} will need to sign in again.`}
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}
