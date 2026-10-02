// The push subscribe flow (P4-05, FR-8.3, UX 6): the browser asks for permission only
// after "Enable push" is pressed, never on load; the subscription is posted with an
// Idempotency-Key; a denied permission shows how to allow it and never prompts again.
// The browser's Notification, PushManager and service worker are stubbed; the
// subscription's keys are obvious placeholders, not key material.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { PushSettings } from "../components/settings/PushSettings";
import { server } from "../test/msw/server";

const SUBSCRIPTION = {
  endpoint: "https://fcm.googleapis.com/fcm/send/placeholder-subscription",
  expirationTime: null,
  keys: { p256dh: "placeholder-p256dh", auth: "placeholder-auth" },
};
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function browser(permission: NotificationPermission) {
  const requestPermission = vi.fn(() =>
    Promise.resolve<NotificationPermission>("granted"),
  );
  vi.stubGlobal("Notification", { permission, requestPermission });
  vi.stubGlobal("PushManager", {});
  const subscribe = vi.fn(() =>
    Promise.resolve({
      endpoint: SUBSCRIPTION.endpoint,
      toJSON: () => SUBSCRIPTION,
    }),
  );
  const registration = {
    pushManager: { subscribe, getSubscription: () => Promise.resolve(null) },
  };
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { ready: Promise.resolve(registration) },
  });
  return { requestPermission, subscribe };
}

describe("push subscribe", () => {
  beforeEach(() => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
  });
  afterEach(() => {
    Reflect.deleteProperty(navigator, "serviceWorker");
  });

  test("[P4-05][FR-8.3] T-P4-05-09 subscribe posts the subscription with idempotency", async () => {
    const { requestPermission, subscribe } = browser("default");
    const posted: { body: unknown; key: string | null }[] = [];
    server.use(
      http.get("*/v1/push/vapid-public-key", () =>
        HttpResponse.json({ public_key: "AQIDBA" }),
      ),
      http.post("*/v1/push/subscriptions", async ({ request }) => {
        posted.push({
          body: await request.json(),
          key: request.headers.get("Idempotency-Key"),
        });
        return HttpResponse.json(
          {
            id: "0199aa00-0000-7000-8000-0000000004b1",
            endpoint: SUBSCRIPTION.endpoint,
          },
          { status: 201 },
        );
      }),
    );

    render(createElement(PushSettings));
    const enable = await screen.findByRole("button", { name: "Enable push" });
    expect(requestPermission).not.toHaveBeenCalled(); // never on load (UX 6)

    await userEvent.click(enable);
    expect(await screen.findByText(/Push is on for this device/)).toBeVisible();
    expect(requestPermission).toHaveBeenCalledOnce();
    expect(subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: new Uint8Array([1, 2, 3, 4]),
    });
    expect(posted).toHaveLength(1);
    expect(posted[0]?.body).toEqual({
      endpoint: SUBSCRIPTION.endpoint,
      keys: SUBSCRIPTION.keys,
    });
    expect(posted[0]?.key).toMatch(UUID_RE);
  });

  test("[P4-05][FR-8.3] T-P4-05-09 a denied permission shows the explanation and never re-prompts", async () => {
    const { requestPermission, subscribe } = browser("denied");
    render(createElement(PushSettings));
    expect(
      await screen.findByText(/Notifications are blocked for this site/),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Enable push" }),
    ).not.toBeInTheDocument();
    expect(requestPermission).not.toHaveBeenCalled();
    expect(subscribe).not.toHaveBeenCalled();
  });
});
