// How a connection's status reads on its card (P3-02, FR-14.4): a short chip, coloured
// by how much it needs the user, and when it last synced in words.
import type { ConnectionStatus } from "../../../api/types.gen";
import type { BadgeTone } from "../../common/ui";

export const STATUS_CHIP: Record<
  ConnectionStatus,
  { label: string; tone: BadgeTone }
> = {
  ok: { label: "Connected", tone: "success" },
  syncing: { label: "Connected", tone: "success" },
  degraded: { label: "Retrying", tone: "warning" },
  auth_required: { label: "Sign in needed", tone: "danger" },
  pending_auth: { label: "Waiting for sign-in", tone: "info" },
  disabled: { label: "Disconnected", tone: "neutral" },
};

const UNITS: readonly [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** "5 minutes ago", "yesterday", "just now": `then` against `now`. */
export function relativeTime(then: Date, now: Date = new Date()): string {
  const seconds = Math.round((then.getTime() - now.getTime()) / 1000);
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) {
      return format.format(Math.round(seconds / size), unit);
    }
  }
  return "just now";
}

export function lastSyncedText(
  lastSuccessAt: string | null,
  now: Date = new Date(),
): string {
  return lastSuccessAt
    ? `Last synced ${relativeTime(new Date(lastSuccessAt), now)}`
    : "Never synced";
}
