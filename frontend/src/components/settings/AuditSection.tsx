// Settings > Audit log (P0-26, SEC-3): the workspace's audit log, newest first, a page at
// a time by cursor, filtered by day; the CSV export link carries the same filters and
// downloads (GET /v1/audit.csv; the export is itself audited).
import { useInfiniteQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { auditListAuditInfiniteOptions } from "../../api/@tanstack/react-query.gen";
import type { AuditEntry } from "../../api/types.gen";
import { apiUrl } from "../../lib/fetch";
import {
  CARD,
  ERROR,
  HEADING,
  HINT,
  INPUT,
  LABEL,
  SECONDARY,
  SECTION,
} from "./styles";

const PAGE_SIZE = 50;

/** The UTC midnight starting `day` (YYYY-MM-DD), `plusDays` later; "" when unset. */
function midnight(day: string, plusDays = 0): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return "";
  const at = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(at.getTime())) return "";
  at.setUTCDate(at.getUTCDate() + plusDays);
  return `${at.toISOString().slice(0, 10)}T00:00:00Z`;
}

function summary(details: AuditEntry["details"]): string {
  const text = JSON.stringify(details);
  return text === "{}" ? "" : text;
}

export function AuditSection() {
  const fromId = useId();
  const toId = useId();
  const [fromDay, setFromDay] = useState("");
  const [toDay, setToDay] = useState("");
  // `from` is inclusive and `to` exclusive on the server: "to" a day includes all of it.
  const filters = {
    ...(midnight(fromDay) ? { from: midnight(fromDay) } : {}),
    ...(midnight(toDay, 1) ? { to: midnight(toDay, 1) } : {}),
  };
  const audit = useInfiniteQuery({
    ...auditListAuditInfiniteOptions({
      query: { ...filters, limit: PAGE_SIZE },
    }),
    initialPageParam: {},
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const entries = audit.data?.pages.flatMap((page) => page.items) ?? [];
  const exportHref = `${apiUrl("/audit.csv")}${
    Object.keys(filters).length
      ? `?${new URLSearchParams(filters).toString()}`
      : ""
  }`;

  return (
    <section aria-labelledby="audit-title" className={SECTION}>
      <h2 id="audit-title" className={HEADING}>
        Audit log
      </h2>
      <div className="grid grid-cols-2 gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={fromId} className={LABEL}>
            From
          </label>
          <input
            id={fromId}
            type="date"
            className={INPUT}
            value={fromDay}
            onChange={(e) => {
              setFromDay(e.target.value);
            }}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={toId} className={LABEL}>
            To
          </label>
          <input
            id={toId}
            type="date"
            className={INPUT}
            value={toDay}
            onChange={(e) => {
              setToDay(e.target.value);
            }}
          />
        </div>
      </div>
      <div>
        <a href={exportHref} download="audit.csv" className={SECONDARY}>
          Export CSV
        </a>
      </div>
      {audit.isError && (
        <p role="alert" className={ERROR}>
          The audit log could not be loaded.
        </p>
      )}
      {audit.isSuccess && entries.length === 0 && (
        <p className={HINT}>Nothing recorded for these days.</p>
      )}
      <ol className="flex flex-col gap-2">
        {entries.map((entry) => (
          <li key={entry.id} className={CARD}>
            <p className="flex flex-wrap items-baseline justify-between gap-2">
              <code className="font-medium">{entry.action}</code>
              <time dateTime={entry.occurred_at} className={HINT}>
                {new Date(entry.occurred_at).toLocaleString()}
              </time>
            </p>
            <p className={HINT}>
              {entry.actor_type}
              {entry.source_ip ? ` from ${entry.source_ip}` : ""}
            </p>
            {summary(entry.details) && (
              <p className="text-sm [overflow-wrap:anywhere]">
                {summary(entry.details)}
              </p>
            )}
          </li>
        ))}
      </ol>
      {audit.hasNextPage && (
        <div>
          <button
            type="button"
            className={SECONDARY}
            disabled={audit.isFetchingNextPage}
            onClick={() => void audit.fetchNextPage()}
          >
            Load more
          </button>
        </div>
      )}
    </section>
  );
}
