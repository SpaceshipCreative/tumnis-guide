// The agent activity feed (P0-23, FR-1.5): collapsed by default so it never crowds the
// screen; empty until agent results arrive (P2-04 fills it).
export function ActivityFeed() {
  return (
    <details className="shrink-0 rounded-xl border border-border bg-surface shadow-card">
      <summary className="flex min-h-11 cursor-pointer items-center px-4 text-sm font-semibold md:min-h-9">
        Agent activity
      </summary>
      <p className="border-t border-border px-4 py-3 text-sm text-muted">
        Nothing from the agents yet.
      </p>
    </details>
  );
}
