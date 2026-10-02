// Settings > Retention (P3-09, SAAS-2): how long ingested email, chat and notes stay.
// The default keeps everything until its project is purged; the opt-in days mode lets the
// hourly retention purge remove content older than that many days (7 at least), except
// what an open task links or an archived project holds. Saved with one
// PUT /v1/settings/integrations.retention.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import type { SettingSectionOut } from "../../../api/types.gen";
import {
  ApiError,
  apiWrite,
  ConflictError,
  useWrite,
} from "../../../lib/fetch";
import { retentionQuery } from "../queries";
import { BUTTON, ERROR, HEADING, HINT, INPUT, LABEL, SECTION } from "../styles";

const RETENTION_SECTION = "integrations.retention"; // queries.retentionQuery's section
const MIN_DAYS = 7; // rules.RETENTION_MIN_DAYS
const MAX_DAYS = 36_500; // rules.RETENTION_MAX_DAYS
const CHOICE = "flex min-h-11 items-center gap-2 [overflow-wrap:anywhere]";

type Mode = "keep_until_project_purged" | "days";

function readMode(section: SettingSectionOut): { mode: Mode; days: string } {
  const values = section.values as { mode?: unknown; days?: unknown };
  const mode: Mode =
    values.mode === "days" ? "days" : "keep_until_project_purged";
  const days = typeof values.days === "number" ? String(values.days) : "";
  return { mode, days };
}

export function RetentionSection() {
  const loaded = useQuery(retentionQuery());
  return (
    <section aria-labelledby="retention-title" className={SECTION}>
      <h2 id="retention-title" className={HEADING}>
        Retention
      </h2>
      {loaded.data ? (
        <RetentionForm loaded={loaded.data} />
      ) : loaded.isError ? (
        <p role="alert" className={ERROR}>
          The retention setting could not be loaded.
        </p>
      ) : (
        <p className={HINT}>Loading…</p>
      )}
    </section>
  );
}

function RetentionForm({ loaded }: { loaded: SettingSectionOut }) {
  const queryClient = useQueryClient();
  const name = useId();
  const daysId = useId();
  const [section, setSection] = useState(loaded);
  const [draft, setDraft] = useState(() => readMode(loaded));
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const show = (current: SettingSectionOut) => {
    queryClient.setQueryData(retentionQuery().queryKey, current);
    setSection(current);
    setDraft(readMode(current));
  };

  const save = useWrite<
    { values: Record<string, unknown>; idempotencyKey?: string },
    SettingSectionOut
  >({
    mutationFn: ({ values, idempotencyKey }) =>
      section.version == null
        ? apiWrite<SettingSectionOut>({
            kind: "create",
            method: "PUT",
            path: `/settings/${RETENTION_SECTION}`,
            body: { values, version: null },
            idempotencyKey,
          })
        : apiWrite<SettingSectionOut>({
            kind: "update",
            method: "PUT",
            path: `/settings/${RETENTION_SECTION}`,
            body: { values },
            version: section.version,
            idempotencyKey,
          }),
    onSuccess: (saved) => {
      show(saved);
      setFailed(false);
      setMessage("Saved.");
    },
    onError: async (error) => {
      setFailed(true);
      if (error instanceof ConflictError) {
        try {
          const current = await queryClient.query({
            ...retentionQuery(),
            staleTime: 0,
          });
          show(current);
          setMessage(
            "Retention changed elsewhere; you are now seeing the current setting.",
          );
        } catch {
          setMessage("The retention setting could not be saved.");
        }
        return;
      }
      setMessage(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The retention setting could not be saved.",
      );
    },
  });

  const days = Number(draft.days);
  const daysValid =
    Number.isInteger(days) && days >= MIN_DAYS && days <= MAX_DAYS;

  return (
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        setMessage(null);
        if (draft.mode === "days" && !daysValid) {
          setFailed(true);
          setMessage(`Keep content at least ${String(MIN_DAYS)} days.`);
          return;
        }
        save.mutate({
          values:
            draft.mode === "days"
              ? { mode: "days", days }
              : { mode: "keep_until_project_purged", days: null },
        });
      }}
    >
      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium">
          Ingested email, chat and notes
        </legend>
        <label className={CHOICE}>
          <input
            type="radio"
            className="size-5"
            name={`${name}-mode`}
            checked={draft.mode === "keep_until_project_purged"}
            onChange={() => {
              setDraft((d) => ({ ...d, mode: "keep_until_project_purged" }));
            }}
          />
          Keep until the project is purged
        </label>
        <label className={CHOICE}>
          <input
            type="radio"
            className="size-5"
            name={`${name}-mode`}
            checked={draft.mode === "days"}
            onChange={() => {
              setDraft((d) => ({ ...d, mode: "days" }));
            }}
          />
          Delete after a number of days
        </label>
      </fieldset>
      {draft.mode === "days" && (
        <div className="flex flex-col gap-1">
          <label htmlFor={daysId} className={LABEL}>
            Days to keep
          </label>
          <input
            id={daysId}
            type="number"
            inputMode="numeric"
            min={MIN_DAYS}
            max={MAX_DAYS}
            className={`${INPUT} max-w-40`}
            value={draft.days}
            onChange={(e) => {
              setDraft((d) => ({ ...d, days: e.target.value }));
            }}
          />
        </div>
      )}
      <p className={HINT}>
        Content an open task links is kept until the task is done, and an
        archived project&apos;s content is compressed, never deleted by
        retention. Deleting removes it from Tumnis only, never from the
        provider; backups keep it until they expire (35 days).
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className={BUTTON} disabled={save.isPending}>
          Save
        </button>
        {message && (
          <p
            role={failed ? "alert" : "status"}
            className={failed ? ERROR : HINT}
          >
            {message}
          </p>
        )}
      </div>
    </form>
  );
}
