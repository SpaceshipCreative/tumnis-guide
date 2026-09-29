// Settings > Dead letters (P0-26, REL-3): deliveries that ran out of attempts, by status.
// Retry enqueues the delivery again; discard drops it for good and asks first. Both send
// the version they read (a stale one is a 409 and the list reloads).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import type { DeadLetterOut } from "../../api/types.gen";
import { ApiError, apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { ConfirmDialog } from "./ConfirmDialog";
import {
  DEAD_LETTER_STATUSES,
  deadLettersQuery,
  type DeadLetterStatus,
} from "./queries";
import {
  CARD,
  DANGER,
  ERROR,
  HEADING,
  HINT,
  INPUT,
  LABEL,
  SECONDARY,
  SECTION,
} from "./styles";

type Action = "retry" | "discard";

function isStatus(value: string): value is DeadLetterStatus {
  return (DEAD_LETTER_STATUSES as readonly string[]).includes(value);
}

export function DeadLettersSection() {
  const queryClient = useQueryClient();
  const statusId = useId();
  const [status, setStatus] = useState<DeadLetterStatus>("open");
  const [discarding, setDiscarding] = useState<DeadLetterOut | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const list = useQuery(deadLettersQuery(status));

  const act = useWrite<
    { action: Action; item: DeadLetterOut; idempotencyKey?: string },
    DeadLetterOut
  >({
    mutationFn: ({ action, item, idempotencyKey }) =>
      apiWrite<DeadLetterOut>({
        kind: "update",
        method: "POST",
        path: `/dead-letters/${encodeURIComponent(item.id)}/${action}`,
        body: {},
        version: item.version,
        idempotencyKey,
      }),
    onSuccess: (_row, { action }) => {
      setMessage(action === "retry" ? "Queued to run again." : "Discarded.");
    },
    onError: (error) => {
      setMessage(
        error instanceof ConflictError
          ? "That dead letter changed elsewhere; the list is up to date now."
          : error instanceof ApiError
            ? (error.problem.detail ?? error.message)
            : "That did not work.",
      );
    },
    onSettled: () => {
      setDiscarding(null);
      void queryClient.invalidateQueries({
        predicate: (query) =>
          (query.queryKey[0] as { _id?: string } | undefined)?._id ===
          "deadLettersGetDeadLetters",
      });
    },
  });

  const items = list.data?.items ?? [];
  return (
    <section aria-labelledby="dead-letters-title" className={SECTION}>
      <h2 id="dead-letters-title" className={HEADING}>
        Dead letters
      </h2>
      <p className={HINT}>
        Events a subscriber could not handle after every attempt. Retry runs the
        delivery again; discard drops it.
      </p>
      <div className="flex max-w-xs flex-col gap-1">
        <label htmlFor={statusId} className={LABEL}>
          Status
        </label>
        <select
          id={statusId}
          className={INPUT}
          value={status}
          onChange={(e) => {
            if (isStatus(e.target.value)) setStatus(e.target.value);
          }}
        >
          {DEAD_LETTER_STATUSES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>
      <p role="status" className={HINT}>
        {message}
      </p>
      {list.isError && (
        <p role="alert" className={ERROR}>
          The dead letters could not be loaded.
        </p>
      )}
      {list.isSuccess && items.length === 0 && (
        <p className={HINT}>No {status} dead letters.</p>
      )}
      <ul className="flex flex-col gap-2">
        {items.map((item) => (
          <li key={item.id} className={CARD}>
            <p className="font-medium [overflow-wrap:anywhere]">
              {item.event_name}
            </p>
            <p className="text-sm [overflow-wrap:anywhere]">
              {item.subscriber}
            </p>
            <p className="text-sm text-danger [overflow-wrap:anywhere]">
              {item.error}
            </p>
            <p className={HINT}>
              {item.attempts} attempts, {item.retries} retries, last{" "}
              {new Date(item.last_at).toLocaleString()}
            </p>
            {item.status === "open" && (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className={SECONDARY}
                  disabled={act.isPending}
                  onClick={() => {
                    setMessage(null);
                    act.mutate({ action: "retry", item });
                  }}
                >
                  Retry
                </button>
                <button
                  type="button"
                  className={DANGER}
                  disabled={act.isPending}
                  onClick={() => {
                    setMessage(null);
                    setDiscarding(item);
                  }}
                >
                  Discard
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {discarding !== null && (
        <ConfirmDialog
          title="Discard this dead letter?"
          confirmLabel="Discard dead letter"
          busy={act.isPending}
          onCancel={() => {
            setDiscarding(null);
          }}
          onConfirm={() => {
            act.mutate({ action: "discard", item: discarding });
          }}
        >
          <p>
            <strong>{discarding.subscriber}</strong> will not get this{" "}
            {discarding.event_name} event. This cannot be undone.
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}
