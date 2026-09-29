// The agent activity feed (P0-23, FR-1.5): collapsed by default so it never crowds the
// screen; empty until agent results arrive (P2-04 fills it).
export function ActivityFeed() {
  return (
    <details className="shrink-0 rounded-lg border border-border bg-surface">
      <summary className="flex min-h-11 cursor-pointer items-center px-3 text-sm font-medium md:min-h-9">
        Agent activity
      </summary>
      <p className="px-3 pb-3 text-sm text-muted">
        Nothing from the agents yet.
      </p>
    </details>
  );
}
