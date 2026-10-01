// Browser push (P4-05, FR-8.3): the subscribe flow. The permission prompt appears only from
// `enablePush`, which Settings calls when "Enable push" is pressed, never on load (UX 6).
// The subscription goes to the server with an Idempotency-Key and the CSRF token; the
// server only stores it, the worker sends. iPhone and iPad offer push only to a web app
// added to the Home Screen (iOS 16.4+), so there the status says to install it first.
// Docs: MDN Push API and PushManager.subscribe, WebKit "Web Push for Web Apps on iOS".
import { apiFetch, apiUrl, apiWrite, problemDetail } from "./fetch";

export type PushStatus = "unsupported" | "install" | "denied" | "ready" | "on";

const SUBSCRIPTION_LOOKUP_MS = 1000; // `serviceWorker.ready` never settles without a worker

function isAppleMobile(): boolean {
  const ua = navigator.userAgent;
  return (
    /iPhone|iPad|iPod/.test(ua) ||
    (ua.includes("Macintosh") && navigator.maxTouchPoints > 1) // iPadOS asks as a Mac
  );
}

function isStandalone(): boolean {
  const legacy = (navigator as Navigator & { standalone?: boolean }).standalone;
  return (
    legacy === true || window.matchMedia("(display-mode: standalone)").matches
  );
}

function supported(): boolean {
  return (
    "Notification" in window &&
    "PushManager" in window &&
    "serviceWorker" in navigator
  );
}

/** A base64url string (the VAPID public key) as the bytes `subscribe` takes. */
export function base64UrlBytes(text: string): Uint8Array<ArrayBuffer> {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<null>((resolve) => {
    timer = setTimeout(() => {
      resolve(null);
    }, SUBSCRIPTION_LOOKUP_MS);
  });
  try {
    return await Promise.race([navigator.serviceWorker.ready, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** Where this browser stands; asks for nothing and sends nothing. */
export async function pushStatus(): Promise<PushStatus> {
  if (isAppleMobile() && !isStandalone()) return "install";
  if (!supported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  if (Notification.permission !== "granted") return "ready";
  const reg = await registration();
  const existing = reg ? await reg.pushManager.getSubscription() : null;
  return existing ? "on" : "ready";
}

/** Ask for permission (this is the only place), subscribe with the workspace's VAPID key
 * and store the subscription on the server. */
export async function enablePush(): Promise<PushStatus> {
  if (!supported()) return "unsupported";
  const permission = await Notification.requestPermission();
  if (permission === "denied") return "denied";
  if (permission !== "granted") return "ready";

  const answer = await apiFetch(apiUrl("/push/vapid-public-key"));
  if (!answer.ok) throw new Error(await problemDetail(answer));
  const { public_key: publicKey } = (await answer.json()) as {
    public_key: string;
  };
  const reg = await navigator.serviceWorker.ready;
  const subscription = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: base64UrlBytes(publicKey),
  });
  const json = subscription.toJSON();
  await apiWrite({
    kind: "create",
    method: "POST",
    path: "/push/subscriptions",
    idempotencyKey: crypto.randomUUID(),
    body: {
      endpoint: json.endpoint ?? subscription.endpoint,
      keys: { p256dh: json.keys?.p256dh, auth: json.keys?.auth },
    },
  });
  return "on";
}
