// The service worker (P0-22 shell, P4-05 push), built by vite-plugin-pwa's
// `injectManifest` strategy into /sw.js:
// - precaches the shell and its assets (the manifest Workbox injects at build time) and
//   answers navigations with /index.html, except the api, the live socket, MCP and the
//   probes; a new build takes over on its next open;
// - `push`: shows the push's title and body (Data flow rule 6: the server sends only a
//   kind and a short label), tagged with the item so a newer push replaces it;
// - `notificationclick`: opens the push's link only as a same-origin /review path whose
//   params pass the review route's own schema, or the dashboard (`/`); anything else
//   lands on /review (P0-22: an invalid deep link lands on a valid default). An open
//   window is focused and navigated rather than a new one opened.
// Docs: vite-plugin-pwa "injectManifest", MDN Push API, ServiceWorkerGlobalScope push and
// notificationclick events, Clients.matchAll / openWindow, WindowClient.navigate / focus.
import "./lib/zodConfig"; // before any schema (strict CSP, P0-16)

import { clientsClaim } from "workbox-core";
import {
  cleanupOutdatedCaches,
  createHandlerBoundToURL,
  precacheAndRoute,
} from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import * as z from "zod";

import { reviewSearch } from "./lib/reviewSearch";

const REVIEW = "/review";
const HOME = "/";
const ICON = "/icons/icon-192.png";

// Paths the service worker never answers with the shell: the api, the live socket, the
// MCP endpoint and the probes (the backend's SPA fallback excludes the same prefixes).
const NOT_THE_SHELL = [/^\/v1\//, /^\/ws/, /^\/mcp/, /^\/health/, /^\/metrics/];

/** What the server pushes (notifications.rules.PushPayload). */
export const PushPayloadSchema = z.object({
  schema_version: z.literal(1),
  kind: z.string().max(80),
  title: z.string().min(1).max(200),
  body: z.string().max(400),
  url: z.string().max(2048),
  tag: z.string().max(80),
});

/** The minimal parts of the Clients API the click handler uses. */
export interface SwClients {
  matchAll(options: {
    type: "window";
    includeUncontrolled: boolean;
  }): Promise<readonly unknown[]>;
  openWindow(url: string): Promise<unknown>;
}

interface WindowClientLike {
  navigate(url: string): Promise<unknown>;
  focus(): Promise<unknown>;
}

function isWindowClient(client: unknown): client is WindowClientLike {
  return (
    typeof client === "object" &&
    client !== null &&
    typeof (client as Partial<WindowClientLike>).navigate === "function" &&
    typeof (client as Partial<WindowClientLike>).focus === "function"
  );
}

/** The path a click may open: a same-origin /review with only the route's valid params,
 * or the dashboard with none; anything else is /review. */
export function safeReviewUrl(raw: unknown, origin: string): string {
  if (typeof raw !== "string" || raw === "") return REVIEW;
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return REVIEW;
  }
  if (url.origin !== new URL(origin).origin) return REVIEW;
  if (url.pathname === HOME) return HOME;
  if (url.pathname !== REVIEW) return REVIEW;
  const search = reviewSearch.parse(Object.fromEntries(url.searchParams));
  const kept = new URLSearchParams();
  if (search.kind !== undefined) kept.set("kind", search.kind);
  if (search.item !== undefined) kept.set("item", search.item);
  const query = kept.toString();
  return query === "" ? REVIEW : `${REVIEW}?${query}`;
}

/** Focus an open window on `url` (a safe path), or open one. */
export async function openOrFocus(
  url: string,
  clients: SwClients,
  origin: string,
): Promise<void> {
  const target = new URL(url, origin).href;
  const windows = await clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });
  const open = windows.find(isWindowClient);
  if (open !== undefined) {
    // Focus first, while the click still allows it; then move the window to the link.
    await open.focus().catch(() => undefined);
    try {
      await open.navigate(target);
      return;
    } catch {
      // a window this worker does not control cannot be navigated: open a new one
    }
  }
  await clients.openWindow(target);
}

// --- The worker itself (only when running as one; tests import the functions above) ---

interface WaitUntil extends Event {
  waitUntil(promise: Promise<unknown>): void;
}

interface PushEventLike extends WaitUntil {
  readonly data: { json(): unknown } | null;
}

interface NotificationEventLike extends WaitUntil {
  readonly notification: Notification;
}

interface ServiceWorkerScope {
  readonly registration: ServiceWorkerRegistration;
  readonly clients: SwClients;
  readonly location: Location;
  readonly __WB_MANIFEST: (string | { url: string; revision: string | null })[];
  skipWaiting(): Promise<void>;
  addEventListener(
    type: "push",
    listener: (event: PushEventLike) => void,
  ): void;
  addEventListener(
    type: "notificationclick",
    listener: (event: NotificationEventLike) => void,
  ): void;
}

function pushed(event: PushEventLike): z.infer<typeof PushPayloadSchema> {
  let data: unknown;
  try {
    data = event.data?.json() ?? null;
  } catch {
    data = null;
  }
  const parsed = PushPayloadSchema.safeParse(data);
  if (parsed.success) return parsed.data;
  // userVisibleOnly: every push shows a notification, so an unreadable one is generic
  return {
    schema_version: 1,
    kind: "unknown",
    title: "Waiting on you",
    body: "Open Tumnis to review.",
    url: REVIEW,
    tag: "unknown",
  };
}

function install(sw: ServiceWorkerScope): void {
  precacheAndRoute((self as unknown as ServiceWorkerScope).__WB_MANIFEST);
  cleanupOutdatedCaches();
  registerRoute(
    new NavigationRoute(createHandlerBoundToURL("/index.html"), {
      denylist: NOT_THE_SHELL,
    }),
  );
  void sw.skipWaiting();
  clientsClaim();

  sw.addEventListener("push", (event) => {
    const payload = pushed(event);
    event.waitUntil(
      sw.registration.showNotification(payload.title, {
        body: payload.body,
        tag: payload.tag,
        icon: ICON,
        data: { url: payload.url },
      }),
    );
  });

  sw.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const data = event.notification.data as { url?: unknown } | null;
    const origin = sw.location.origin;
    event.waitUntil(
      openOrFocus(safeReviewUrl(data?.url, origin), sw.clients, origin),
    );
  });
}

if ("ServiceWorkerGlobalScope" in globalThis) {
  install(globalThis as unknown as ServiceWorkerScope);
}
