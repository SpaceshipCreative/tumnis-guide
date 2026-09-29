// The one fetch wrapper (R-18): every write carries the session's CSRF token, read
// from the `__Host-tumnis_csrf` cookie, as `X-CSRF-Token`, and an `Idempotency-Key`
// (REL-2). P0-22 builds the generated client on top of it.
export const CSRF_COOKIE = "__Host-tumnis_csrf";
const WRITES = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** A cookie's value from `document.cookie`, or undefined. */
export function readCookie(
  name: string,
  cookies: string = document.cookie,
): string | undefined {
  for (const part of cookies.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

/** `fetch` with the write headers added; the session rides on its own cookie. */
export async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (WRITES.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf !== undefined && !headers.has("X-CSRF-Token")) {
      headers.set("X-CSRF-Token", csrf);
    }
    if (!headers.has("Idempotency-Key")) {
      headers.set("Idempotency-Key", crypto.randomUUID());
    }
  }
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(path, { ...init, method, headers, credentials: "same-origin" });
}

/** The problem+json `detail` (or a fallback) of a failed response. */
export async function problemDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // not JSON
  }
  return `Request failed (${String(response.status)})`;
}
