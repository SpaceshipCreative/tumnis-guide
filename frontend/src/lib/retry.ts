// The reads' retry policy for a 429 (P0-10's rate limit, SEC-5). A 4xx problem does not
// change on a retry, except 429: the per-principal bucket refills within a second, so a
// read refused for going too fast is asked again after the server's Retry-After (capped),
// at most RATE_LIMIT_RETRIES times. Without this, a dashboard reload that ran into the
// limit kept the 429 for the day's plan and showed the Today tasks instead (J1 A1.2).
//
// The generated client throws the problem body, which has no headers, so its error
// interceptor (lib/client.ts) adds the Retry-After to the error with `withRetryAfter`.

/** How many more times a read refused with 429 is asked. */
export const RATE_LIMIT_RETRIES = 2;
/** The longest a 429 waits before it is asked again, whatever Retry-After says. */
export const RETRY_AFTER_CAP_MS = 5_000;
/** The wait when a 429 brings no usable Retry-After (the server's floor is 1 s). */
const RETRY_AFTER_DEFAULT_MS = 1_000;
/** Query's own backoff for other failures: doubling from 1 s, at most 30 s. */
const BACKOFF_BASE_MS = 1_000;
const BACKOFF_CAP_MS = 30_000;

const RETRY_AFTER_KEY = "retryAfterMs";

function field(error: unknown, key: string): unknown {
  return typeof error === "object" && error !== null && key in error
    ? (error as Record<string, unknown>)[key]
    : undefined;
}

/** Whether `error` is a 429 (a problem body or an `ApiError`, both carry `status`). */
export function isRateLimited(error: unknown): boolean {
  return field(error, "status") === 429;
}

/** A `Retry-After` header in milliseconds (delta-seconds or an HTTP date), if usable. */
export function readRetryAfter(
  header: string | null,
  now: number = Date.now(),
): number | undefined {
  if (header === null) return undefined;
  const value = header.trim();
  if (/^\d+$/.test(value)) return Number(value) * 1_000;
  // An HTTP date starts with its day name (RFC 9110 IMF-fixdate, e.g. "Mon, 09 Mar ...").
  const at = /^[A-Za-z]/.test(value) ? Date.parse(value) : Number.NaN;
  return Number.isNaN(at) ? undefined : Math.max(0, at - now);
}

/** The error with its `Retry-After` (ms) added when it is a 429 that has one. */
export function withRetryAfter(error: unknown, header: string | null): unknown {
  if (!isRateLimited(error) || typeof error !== "object" || error === null) {
    return error;
  }
  const wait = readRetryAfter(header);
  return wait === undefined ? error : { ...error, [RETRY_AFTER_KEY]: wait };
}

/** Query `retry`: a 429 is asked again, at most RATE_LIMIT_RETRIES times. */
export function retryRateLimited(
  failureCount: number,
  error: unknown,
): boolean {
  return isRateLimited(error) && failureCount < RATE_LIMIT_RETRIES;
}

/** Query `retryDelay`: a 429 waits its Retry-After (capped, 1 s without one); other
 * failures keep Query's own doubling backoff. */
export function retryDelay(attempt: number, error: unknown): number {
  if (isRateLimited(error)) {
    const wait = field(error, RETRY_AFTER_KEY);
    return Math.min(
      typeof wait === "number" ? wait : RETRY_AFTER_DEFAULT_MS,
      RETRY_AFTER_CAP_MS,
    );
  }
  return Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_CAP_MS);
}
