// The review badge (P0-23, FR-1.4): how many decisions wait for the human, linking to the
// review queue (P1-13). A live `review_item` message refreshes the count (LIVE_MAP).
import { Link } from "@tanstack/react-router";

export function ReviewBadge({ count }: { count: number }) {
  return (
    <Link
      to="/review"
      className="inline-flex min-h-11 items-center gap-2 rounded-full border border-border bg-surface px-3 text-sm font-medium hover:bg-surface-muted md:min-h-9"
    >
      <span className="rounded-full bg-accent px-2 text-accent-contrast tabular-nums">
        {count}
      </span>{" "}
      to review
    </Link>
  );
}
