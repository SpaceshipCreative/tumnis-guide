// The service worker (P4-05): `push` and `notificationclick` handlers.

/** The minimal parts of the Clients API the click handler uses. */
export interface SwClients {
  matchAll(options: {
    type: "window";
    includeUncontrolled: boolean;
  }): Promise<readonly unknown[]>;
  openWindow(url: string): Promise<unknown>;
}

export function safeReviewUrl(raw: unknown, origin: string): string {
  throw new Error(`not built yet: ${typeof raw} ${origin}`);
}

export function openOrFocus(
  url: string,
  clients: SwClients,
  origin: string,
): Promise<void> {
  return Promise.reject(
    new Error(`not built yet: ${url} ${origin} ${typeof clients}`),
  );
}
