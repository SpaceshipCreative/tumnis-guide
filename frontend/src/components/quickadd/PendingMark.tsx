// The pending mark (P0-25, FR-3.10): a capture still waiting to reach the server. After
// 23 hours (plan default) it also warns: the server keeps an idempotency key for 24, so
// a replay after that could add the task twice if the first attempt did land.
import { usePendingTasks } from "./queue";

export const STALE_AFTER_MS = 23 * 60 * 60 * 1000; // (plan default)

export function PendingMark({
  idempotencyKey,
  now = Date.now(),
}: {
  idempotencyKey: string;
  now?: number;
}) {
  const item = usePendingTasks().find(
    (i) => i.idempotencyKey === idempotencyKey,
  );
  const stale = item !== undefined && now - item.createdAt > STALE_AFTER_MS;
  return (
    <span
      data-testid="pending-mark"
      className="inline-flex items-center gap-1 rounded-full bg-surface-muted px-2 text-xs font-medium text-muted"
    >
      <span aria-hidden="true">⟳</span>
      Waiting to sync
      {stale && (
        <span className="text-danger">
          {" "}
          for over a day; check it was not added twice
        </span>
      )}
    </span>
  );
}
